"""Testes offline do passo 2 com preços sintéticos."""
import numpy as np
import pandas as pd
import yaml
from pathlib import Path

from src import indicadores, sentimento, sinais

CFG = yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())["sinais"]


def _serie(ticker, closes, vol=1_000_000, vol_ultimo=None):
    closes = np.asarray(closes, float)
    n = len(closes)
    v = np.full(n, vol, float)
    if vol_ultimo:
        v[-1] = vol_ultimo
    return pd.DataFrame({"data": pd.bdate_range("2025-01-01", periods=n), "ticker": ticker,
                         "open": closes, "high": closes * 1.01, "low": closes * 0.99,
                         "close": closes, "volume": v})


def test_rsi_e_atr_valores_basicos():
    c = pd.Series(np.arange(1, 60, dtype=float))
    assert indicadores.rsi(c).iloc[-1] == 100.0  # só subidas
    df = _serie("X", np.full(60, 100.0))
    assert np.isclose(indicadores.atr(df).iloc[-1], 2.0, atol=1e-6)  # high-low = 2


def test_rompimento_detetado_com_objetivo_acima_e_stop_abaixo():
    rng = np.random.default_rng(0)
    base = 100 * np.cumprod(1 + 0.001 + rng.normal(0, 0.01, 300))
    base[-1] = base[-21:-1].max() * 1.05  # rompe o máximo de 20 dias
    precos = _serie("ROMP", base, vol_ultimo=5_000_000)
    s = sinais.sinais_hoje(precos, CFG, ["rompimento"])
    assert len(s) == 1
    r = s.iloc[0]
    assert r["direcao"] == "compra" and r["objetivo"] > r["entrada"] > r["stop"]


def test_resultado_trade_objetivo_e_stop():
    d = indicadores.calcular(_serie("X", np.r_[np.full(30, 100.0), [110, 120]]))
    # ATR ~2 no dia 29; objetivo 104 é atingido no dia seguinte.
    assert sinais.resultado_trade(d, 29, "compra", 2, 1, 5) == ("objetivo", 1)
    d2 = indicadores.calcular(_serie("Y", np.r_[np.full(30, 100.0), [90, 80]]))
    assert sinais.resultado_trade(d2, 29, "compra", 2, 1, 5) == ("stop", 1)


def test_pontuar_retorno_esperado_custos_e_filtros():
    cfg = {**CFG, "custo_cambio_pct_por_lado": 0.5, "peso_base_casos": 10,
           "qualidade": {"pullback": {"retorno_base_pct": 0.5, "filtros": {"vol_ratio": [1.0, None]}}}}
    s = pd.DataFrame({"ticker": ["A.DE", "B", "C"], "setup": "pullback", "direcao": "compra",
                      "regiao": ["europa", "eua", "eua"], "n_historico": [10, 0, 10],
                      "taxa_acerto": [0.7, np.nan, 0.5], "dias_mediana": [3.0, np.nan, 2.0],
                      "retorno_medio_hist": [0.03, np.nan, 0.03],
                      "vol_ratio": [2.0, 2.0, 0.5], "ret20": [0.1, 0.1, 0.1]})
    out = sinais.pontuar(s, cfg).set_index("ticker")
    # A: (3% x 10 + 0,5% x 10) / 20 = 1,75%, sem custo cambial (euro)
    assert np.isclose(out.loc["A.DE", "retorno_esperado_liquido"], 0.0175)
    # B: sem histórico -> base 0,5% menos 1% de câmbio
    assert np.isclose(out.loc["B", "retorno_esperado_liquido"], -0.005)
    assert out.loc["B", "dias_estimados"] == CFG["max_dias"]
    # C: volume abaixo do filtro
    assert not out.loc["C", "passa_filtros"] and out.loc["A.DE", "passa_filtros"]


def test_sentimento_sem_chave_usa_palavras(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    news = pd.DataFrame({"titulo": ["Company beats estimates, analysts upgrade"], "resumo": [""],
                         "fonte": ["X"], "data": [pd.Timestamp.now(tz="UTC")]})
    av = sentimento.avaliar({"ticker": "A", "direcao": "compra"}, news)
    assert av.sentimento > 0 and av.impacto == "apoia"
