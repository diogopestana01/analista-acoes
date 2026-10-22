"""Testes das ferramentas de notícias e resultados (offline)."""
import numpy as np
import pandas as pd

import passo2_sinais
from src import indicadores, resultados, sentimento, sinais


def _precos(closes, opens=None, vols=None):
    closes = np.asarray(closes, float)
    n = len(closes)
    opens = closes if opens is None else np.asarray(opens, float)
    return pd.DataFrame({"data": pd.bdate_range("2025-01-01", periods=n), "ticker": "X",
                         "open": opens, "high": np.maximum(opens, closes) * 1.005,
                         "low": np.minimum(opens, closes) * 0.995, "close": closes,
                         "volume": np.full(n, 1e6) if vols is None else np.asarray(vols, float)})


def test_calendario_nasdaq_le_tickers():
    resp = {"data": {"rows": [{"symbol": "AAPL"}, {"symbol": "BRK.B"}, {"symbol": None}]}}
    assert resultados.tickers_do_dia(resp) == {"AAPL", "BRK-B"}
    assert resultados.tickers_do_dia({"data": None}) == set()


def test_gap_continuacao_no_dia_seguinte_ao_salto():
    closes = np.r_[np.full(40, 100.0), [108.0, 109.0]]
    opens = np.r_[np.full(40, 100.0), [105.0, 108.5]]     # dia 40: abre +5% e fecha no topo
    vols = np.r_[np.full(40, 1e6), [5e6, 2e6]]
    d = indicadores.calcular(_precos(closes, opens, vols))
    f = sinais.detetar(d)["gap_continuacao"]
    assert not f.iat[40] and f.iat[41]                    # sinal só no dia seguinte, se segurou


def test_choque_de_baixa_marca_5_dias():
    closes = np.r_[np.full(40, 100.0), [90.0], np.full(6, 90.0)]
    vols = np.r_[np.full(40, 1e6), [4e6], np.full(6, 1e6)]
    d = indicadores.calcular(_precos(closes, vols=vols))
    assert d["choque_baixa_5d"].iat[40] == 1 and d["choque_baixa_5d"].iat[44] == 1
    assert d["choque_baixa_5d"].iat[45] == 0 and d["choque_baixa_5d"].iat[39] == 0


def test_pullback_com_causa_fundamental_e_eliminado():
    tec = pd.DataFrame({"ticker": ["A", "B", "C"], "setup": ["pullback", "pullback", "rompimento"],
                        "direcao": "compra", "pont_tecnica": 60.0, "retorno_esperado_liquido": 0.02})
    av = {t: sentimento.Avaliacao(sentimento=0, impacto="neutro", risco_evento=False,
                                  causa_fundamental=c, resumo="") for t, c in zip("ABC", [True, False, True])}
    out = passo2_sinais.combinar(tec, av, {"peso_noticias": 15, "filtro_queda_com_noticia": True})
    assert sorted(out["ticker"]) == ["B", "C"]   # rompimento com notícia fica


def test_filtro_queda_com_noticia_desligado_mantem_pullback():
    tec = pd.DataFrame({"ticker": ["A"], "setup": ["pullback"], "direcao": "compra",
                        "pont_tecnica": 60.0, "retorno_esperado_liquido": 0.02})
    av = {"A": sentimento.Avaliacao(sentimento=0, impacto="neutro", risco_evento=False,
                                    causa_fundamental=True, resumo="")}
    out = passo2_sinais.combinar(tec, av, {"peso_noticias": 15})
    assert list(out["ticker"]) == ["A"] and bool(out["causa_fundamental"].iat[0])
