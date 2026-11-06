"""Regime do mercado (src/regime.py): amplitude e travão de perdas. Offline."""
import json

import numpy as np
import pandas as pd

from src import regime


def _precos(n_acima: int, n_abaixo: int, dias: int = 60) -> pd.DataFrame:
    datas = pd.bdate_range("2026-08-01", periods=dias)
    linhas = []
    for k in range(n_acima + n_abaixo):
        sobe = k < n_acima
        for i, d in enumerate(datas):
            linhas.append({"data": d, "ticker": f"T{k}", "close": 100 + (i if sobe else -i) * 0.5})
    return pd.DataFrame(linhas)


def test_amplitude_conta_acima_da_media_de_50():
    p = _precos(30, 70)
    assert abs(regime.amplitude(p, p["ticker"].unique()) - 0.3) < 1e-9


def test_amplitude_ignora_vela_de_hoje():
    p = _precos(60, 40)
    hoje = p["data"].max()
    # hoje todas caem a pique: com a vela por fechar, conta só o fecho de ontem
    p.loc[p["data"] == hoje, "close"] = 1.0
    assert regime.amplitude(p, p["ticker"].unique(), f"{hoje:%Y-%m-%d}") == 0.6
    assert regime.amplitude(p, p["ticker"].unique()) == 0.0


def test_amplitude_poucas_acoes_e_none():
    p = _precos(5, 5)
    assert regime.amplitude(p, p["ticker"].unique()) is None


def _sim(vendas):
    return {"carteiras": {"5": {"regras": {"capital_inicial": 1000.0},
                                "operacoes": [{"tipo": "venda", "data": d, "lucro": l} for d, l in vendas]}}}


def test_perda_recente_so_ultimos_7_dias():
    s = _sim([("2026-11-05", -40), ("2026-11-03", -20), ("2026-10-20", -500)])
    assert abs(regime.perda_recente(s, "5", "2026-11-06", 7) + 0.06) < 1e-9


def test_travao_liga_e_dura_5_dias(tmp_path):
    sim, est = tmp_path / "sim.json", tmp_path / "reg.json"
    sim.write_text(json.dumps(_sim([("2026-11-05", -60)])))
    cfg = {"travao": {"perda": 0.05, "janela_dias": 7, "pausa_dias": 5, "carteira": "5"}}
    p = _precos(60, 40)
    r = regime.estado(p, [], cfg, "2026-11-06", False, sim, est)
    assert r["travao"] and r["travao_ate"] == "2026-11-11"
    assert "travão" in regime.bloqueia(r, "AAPL")
    # sem perdas novas, continua até ao fim da pausa e depois acaba
    sim.write_text(json.dumps(_sim([])))
    assert regime.estado(p, [], cfg, "2026-11-10", False, sim, est)["travao"]
    assert not regime.estado(p, [], cfg, "2026-11-11", False, sim, est)["travao"]


def test_sem_perda_grande_nao_trava(tmp_path):
    sim, est = tmp_path / "sim.json", tmp_path / "reg.json"
    sim.write_text(json.dumps(_sim([("2026-11-05", -30)])))
    r = regime.estado(_precos(60, 40), [], {"travao": {"perda": 0.05}}, "2026-11-06", False, sim, est)
    assert not r["travao"]


def test_aplicar_amplitude_so_eua():
    r = {"amplitude": 0.4, "amplitude_min": 0.5, "amplitude_baixa": True, "travao": False}
    itens = [{"ticker": "AAPL", "estado_lista": "comprar"}, {"ticker": "SAP.DE", "estado_lista": "comprar"},
             {"ticker": "MSFT", "estado_lista": "andamento"}]
    regime.aplicar(itens, r)
    assert [s["estado_lista"] for s in itens] == ["andamento", "comprar", "andamento"]
    assert "40%" in itens[0]["pausa_mercado"] and itens[1]["pausa_mercado"] is None


def test_sem_regime_nao_mexe():
    itens = [{"ticker": "AAPL", "estado_lista": "comprar"}]
    assert regime.aplicar(itens, None)[0]["estado_lista"] == "comprar"


def test_aviso_so_quando_muda(tmp_path):
    est = tmp_path / "reg.json"
    est.write_text("{}")
    enviados = []
    r = {"travao": True, "travao_ate": "2026-11-11", "perda_7d": -0.06, "avisado": None}
    regime.avisar(r, est, enviados.append)
    r["avisado"] = json.loads(est.read_text())["avisado"]
    regime.avisar(r, est, enviados.append)
    assert len(enviados) == 1 and "pausa" in enviados[0]
    regime.avisar({"travao": False, "amplitude_baixa": False, "avisado": "travao"}, est, enviados.append)
    assert len(enviados) == 2 and "retomadas" in enviados[1]


def test_capitulacao_deixa_passar_pullbacks(tmp_path):
    sim, est = tmp_path / "sim.json", tmp_path / "reg.json"
    sim.write_text(json.dumps(_sim([("2026-11-05", -60)])))
    cfg = {"travao": {"perda": 0.05}, "capitulacao": {"spy_5d": -0.03, "setups": ["pullback"]}}
    r = regime.estado(_precos(60, 40), [], cfg, "2026-11-06", False, sim, est, spy5=lambda: -0.04)
    assert r["travao"] and r["capitulacao"]
    assert regime.bloqueia(r, "AAPL", "pullback") is None
    assert "travão" in regime.bloqueia(r, "AAPL", "rompimento")
    itens = regime.aplicar([{"ticker": "AAPL", "setup": "pullback", "estado_lista": "comprar"},
                            {"ticker": "MSFT", "setup": "rompimento", "estado_lista": "comprar"}], r)
    assert [s["estado_lista"] for s in itens] == ["comprar", "andamento"]
    assert regime.chave(r) == "capitulacao" and "pullbacks" in regime.texto_aviso(r)


def test_sem_queda_forte_nao_ha_capitulacao(tmp_path):
    sim, est = tmp_path / "sim.json", tmp_path / "reg.json"
    sim.write_text(json.dumps(_sim([("2026-11-05", -60)])))
    cfg = {"travao": {"perda": 0.05}, "capitulacao": {"spy_5d": -0.03, "setups": ["pullback"]}}
    r = regime.estado(_precos(60, 40), [], cfg, "2026-11-06", False, sim, est, spy5=lambda: -0.02)
    assert not r["capitulacao"] and regime.bloqueia(r, "AAPL", "pullback")


def test_so_em_cada_setup_no_seu_mercado():
    forte = {"mercado": "forte", "so_em": {"caiu_10": ["forte"], "reversao_queda": ["queda"]}}
    queda = {**forte, "mercado": "queda", "capitulacao": True, "isentos": ["pullback", "reversao_queda"],
             "amplitude_baixa": True, "amplitude": 0.4}
    assert regime.bloqueia(forte, "AAPL", "caiu_10") is None
    assert "queda forte" in regime.bloqueia(forte, "AAPL", "reversao_queda")
    assert regime.bloqueia(queda, "AAPL", "reversao_queda") is None
    assert regime.bloqueia(queda, "AAPL", "caiu_10")
    assert regime.bloqueia(None, "AAPL", "caiu_10")          # sem regime não passam
    assert regime.bloqueia(None, "AAPL", "pullback") is None


def test_estado_diz_o_mercado(tmp_path):
    cfg = {"amplitude_min": 0.5, "so_em": {"caiu_10": ["forte"]}, "capitulacao": {"spy_5d": -0.03}}
    sim, est = tmp_path / "s.json", tmp_path / "r.json"
    p = _precos(60, 40)
    assert regime.estado(p, [f"T{k}" for k in range(100)], cfg, "2026-11-06", False, sim, est,
                         spy5=lambda: -0.01)["mercado"] == "forte"
    assert regime.estado(p, [f"T{k}" for k in range(100)], cfg, "2026-11-06", False, sim, est,
                         spy5=lambda: -0.05)["mercado"] == "queda"


def test_mercado_atual():
    p = _precos(60, 40)
    tk = [f"T{k}" for k in range(100)]
    assert regime.mercado_atual(p, tk, {"amplitude_min": 0.5}, spy5=lambda: 0.0) == "forte"
    assert regime.mercado_atual(p, tk, {"amplitude_min": 0.7}, spy5=lambda: 0.0) == "fraco"
    assert regime.mercado_atual(p, tk, {"capitulacao": {"spy_5d": -0.03}}, spy5=lambda: -0.04) == "queda"


def test_setups_do_mercado_fraco_passam_a_amplitude():
    so_em = {"medo_vix": ["forte", "fraco", "queda"], "viragem_mes": ["fraco", "queda"], "lider_recuo": ["forte"]}
    fraco = {"mercado": "fraco", "so_em": so_em, "amplitude_baixa": True, "amplitude": 0.4}
    assert regime.bloqueia(fraco, "AAPL", "medo_vix") is None
    assert regime.bloqueia(fraco, "AAPL", "viragem_mes") is None
    assert regime.bloqueia(fraco, "AAPL", "lider_recuo")
    assert "mercado fraco" in regime.bloqueia(fraco, "AAPL", "pullback")
    assert regime.bloqueia({**fraco, "travao": True, "travao_ate": "2026-11-11"}, "AAPL", "viragem_mes")
