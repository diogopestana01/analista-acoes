"""Sinais em teste (src/em_teste.py): mercado, janela antes do fecho, alerta e registo em papel. Offline."""
import numpy as np
import pandas as pd

from src import em_teste

CFG = {"ligado": True, "janela_min": 40, "max_por_dia": 5, "ordem": "rsi2", "amplitude_min": 0.5,
       "queda_spy_5d": -0.03, "regras": {"forte": ["caiu_10_5d"], "queda": ["rsi2_10", "caiu_10_5d"]}}


def _acao(ticker: str, fim: str, queda_final: float, dias: int = 260) -> pd.DataFrame:
    """Sobe devagar (tendência) e cai `queda_final` nos últimos 5 dias."""
    datas = pd.bdate_range(end=fim, periods=dias)
    c = 50 * (1.002 ** np.arange(dias))
    c[-5:] = c[-6] * (1 - queda_final) ** (np.arange(1, 6) / 5)
    return pd.DataFrame({"data": datas, "ticker": ticker, "open": c, "high": c * 1.01, "low": c * 0.99,
                         "close": c, "volume": 1e6})


def test_tipo_de_mercado():
    assert em_teste.tipo_mercado(0.6, -0.01, CFG) == "forte"
    assert em_teste.tipo_mercado(0.4, -0.01, CFG) == "fraco"
    assert em_teste.tipo_mercado(0.4, -0.05, CFG) == "queda"
    assert em_teste.tipo_mercado(None, None, CFG) is None


def test_janela_so_antes_do_fecho_de_ny():
    assert em_teste.na_janela(pd.Timestamp("2026-11-06 20:30", tz="UTC"), 40)       # NY fecha às 21:00 UTC
    assert not em_teste.na_janela(pd.Timestamp("2026-11-06 19:00", tz="UTC"), 40)
    assert not em_teste.na_janela(pd.Timestamp("2026-11-07 20:30", tz="UTC"), 40)   # sábado


def test_detecta_caiu_10_em_tendencia():
    from src import indicadores
    d = indicadores.calcular(_acao("A", "2026-11-06", 0.12))
    assert em_teste.detetar(d)["caiu_10_5d"].iat[-1]
    d = indicadores.calcular(_acao("B", "2026-11-06", 0.03))
    assert not em_teste.detetar(d)["caiu_10_5d"].iat[-1]


def test_corrida_envia_so_no_mercado_certo_e_regista(tmp_path):
    p = pd.concat([_acao("A", "2026-11-06", 0.12), _acao("B", "2026-11-06", 0.15), _acao("C", "2026-11-06", 0.02)])
    enviados = []
    agora = pd.Timestamp("2026-11-06 20:30", tz="UTC")
    f = tmp_path / "t.csv"
    # mercado fraco: nada
    em_teste.correr(p, ["A", "B", "C"], CFG, agora, {"amplitude": 0.4, "spy_5d": -0.01}, f, enviados.append)
    assert not enviados
    f.unlink()
    reg = em_teste.correr(p, ["A", "B", "C"], CFG, agora, {"amplitude": 0.6, "spy_5d": -0.01}, f,
                          lambda t: enviados.append(t) or True, abertos_modelo={"A"})
    assert len(enviados) == 1 and "B" in enviados[0] and "<b>A</b>" not in enviados[0] and "em teste" in enviados[0]
    assert list(reg.loc[reg["estado"] == "aberto", "ticker"]) == ["B"]
    # segunda corrida no mesmo dia: não repete
    em_teste.correr(p, ["A", "B", "C"], CFG, agora, {"amplitude": 0.6, "spy_5d": -0.01}, f, enviados.append)
    assert len(enviados) == 1


def test_fecha_em_papel_ao_passar_a_media_de_5(tmp_path):
    p = _acao("B", "2026-11-06", 0.15)
    f = tmp_path / "t.csv"
    agora = pd.Timestamp("2026-11-06 20:30", tz="UTC")
    em_teste.correr(p, ["B"], CFG, agora, {"amplitude": 0.6, "spy_5d": 0.0}, f, lambda t: True)
    # dois dias depois a ação recupera forte: fecha acima da média de 5 dias
    ult = p.iloc[-1]
    extra = pd.DataFrame([{**ult, "data": d, "open": ult["close"] * k, "high": ult["close"] * k * 1.01,
                           "low": ult["close"] * k * 0.995, "close": ult["close"] * k}
                          for d, k in ((pd.Timestamp("2026-11-09"), 1.01), (pd.Timestamp("2026-11-10"), 1.12))])
    p2 = pd.concat([p, extra], ignore_index=True)
    reg = em_teste.correr(p2, ["B"], CFG, pd.Timestamp("2026-11-10 22:00", tz="UTC"), {}, f, lambda t: True)
    fechado = reg[reg["ticker"] == "B"].iloc[0]
    assert fechado["estado"] == "fechado" and fechado["motivo"] == "regra" and float(fechado["retorno"]) > 0.1
    assert "Em teste" in em_teste.resumo(reg)


def test_desligado_nao_faz_nada(tmp_path):
    assert em_teste.correr(pd.DataFrame(), [], {"ligado": False}, pd.Timestamp.now(tz="UTC"), {}, tmp_path / "x",
                           print) is None
