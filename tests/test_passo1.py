"""Testes offline do passo 1 (sem acesso à internet)."""
import numpy as np
import pandas as pd

from src import precos, universo

NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
QQQ|Invesco QQQ Trust|G|N|N|100|Y|N
ABCDW|Abcd Corp - Warrant|S|N|N|100|N|N
ZXZZT|Test Stock|G|Y|N|100|N|N
File Creation Time: 1021202608:00|||||||"""

OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
BRK.B|Berkshire Hathaway Inc. Class B|N|BRK.B|N|100|N|BRK.B
JPM|JPMorgan Chase & Co. Common Stock|N|JPM|N|100|N|JPM
JPM$C|JPMorgan Chase Depositary Shares Preferred|N|JPMpC|N|100|N|JPM-C
SPY|SPDR S&P 500|P|SPY|Y|100|N|SPY
File Creation Time: 1021202608:00|||||||"""


def test_nasdaq_exclui_etf_warrant_teste():
    assert universo.parse_nasdaq_listed(NASDAQ)["ticker"].tolist() == ["AAPL"]


def test_other_converte_classes_e_exclui_preferenciais():
    df = universo.parse_other_listed(OTHER)
    assert df["ticker"].tolist() == ["BRK-B", "JPM"]
    assert set(df["bolsa"]) == {"NYSE"}


def test_tickers_europa_adiciona_sufixo():
    tabela = pd.DataFrame({"Company": [f"Empresa {i}" for i in range(12)],
                           "Ticker": ["SAP", "ADS.DE"] + [f"X{i}" for i in range(10)]})
    out = universo.tickers_de_tabelas([pd.DataFrame({"a": [1]}), tabela], ".DE")
    assert out[:2] == ["SAP.DE", "ADS.DE"]


def _yf_falso(tickers, dias=150):
    datas = pd.bdate_range("2026-01-01", periods=dias, name="Date")
    cols = {}
    for t, (preco, vol) in tickers.items():
        cols[t] = pd.DataFrame({"Open": preco, "High": preco * 1.01, "Low": preco * 0.99,
                                "Close": preco, "Volume": vol}, index=datas)
    return pd.concat(cols, axis=1)


def test_formato_longo_e_filtro_liquidez():
    bruto = _yf_falso({"LIQ": (50.0, 1_000_000), "POUCO": (50.0, 1_000),
                       "BARATA": (2.0, 10_000_000), "VOD.L": (7000.0, 50_000)})
    longo = precos._para_formato_longo(bruto, list(bruto.columns.levels[0]))
    assert set(longo.columns) == set(precos.COLUNAS)
    assert longo["ticker"].nunique() == 4

    uni = pd.DataFrame({"ticker": ["LIQ", "POUCO", "BARATA", "VOD.L"],
                        "moeda": ["USD", "USD", "USD", "GBp"]})
    taxas = {"EUR": 1.0, "USD": 1.1, "GBp": 85.0}
    cfg = {"preco_minimo": 5.0, "volume_medio_minimo_eur": 2_000_000, "dias_historico_minimo": 120}
    out = precos.filtrar_liquidez(longo, uni, taxas, cfg)
    # LIQ: 50M USD/dia; VOD.L: 70 GBP * 50k = 3.5M GBP/dia. POUCO sem volume, BARATA < 5.
    assert sorted(out["ticker"]) == ["LIQ", "VOD.L"]
    assert np.isclose(out.set_index("ticker").loc["VOD.L", "valor_medio_20d_eur"],
                      7000 * 50_000 / 85.0)


def test_juntar_e_projetar_volume():
    a = pd.DataFrame({"data": pd.to_datetime(["2026-10-20", "2026-10-21"]), "ticker": "AAPL",
                      "open": 1, "high": 1, "low": 1, "close": [1.0, 1.0], "volume": [100.0, 10.0]})
    b = a.iloc[[1]].assign(close=2.0, volume=50.0)
    j = precos.juntar(a, b)
    assert len(j) == 2 and j["close"].iloc[-1] == 2.0
    # 16:45 UTC = metade da sessão dos EUA (13:30-20:00) -> volume x2
    p = precos.projetar_volume_hoje(j, pd.Timestamp("2026-10-21 16:45"))
    assert p["volume"].iloc[-1] == 100.0 and p["volume"].iloc[0] == 100.0
