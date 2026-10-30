"""Carteiras simuladas (src/simuladas.py), histórico do número de posições e retoques do menu."""
import json

import pandas as pd

from src import bot, simuladas
from tests.test_carteira import CFG, DONO, _cb, _hist, _textos
from tests.test_lista_sinais import HIST, HOJE, _preparar


def _txt(t):
    return {"update_id": 1, "message": {"chat": {"id": int(DONO)}, "text": t}}


def test_simuladas_compram_a_lista_e_vendem_quando_o_sinal_fecha(monkeypatch):
    _preparar(monkeypatch)
    sim = simuladas.nova(HOJE)
    assert simuladas.passo(sim, HIST, HOJE)
    tres = sim["carteiras"]["3"]
    assert sorted(tres["posicoes"]) == ["CEDO", "NOVO"]           # os dois que dão para comprar
    assert tres["posicoes"]["NOVO"]["qtd"] * 20.1 == pytest_approx(1000 / 3)
    assert not simuladas.passo(sim, HIST, HOJE)                    # nada de novo: não compra outra vez
    # O registo fecha a NOVO no objetivo: todas as carteiras vendem a 22.
    fechado = HIST.copy()
    i = fechado.index[fechado["ticker"] == "NOVO"][0]
    fechado.loc[i, ["estado", "preco_fecho"]] = ["objetivo", 22.0]
    assert simuladas.passo(sim, fechado, HOJE, aberta=False)
    assert "NOVO" not in tres["posicoes"] and tres["operacoes"][-1]["motivo"] == "prazo"
    res = {r["n"]: r for r in simuladas.resultados(sim)}
    assert res[3]["vendas"] == 1 and res[3]["ganhas"] == 1 and res[3]["pct"] > 0


def pytest_approx(x):
    import pytest
    return pytest.approx(x, rel=1e-3)


def test_regras_mostram_historico_e_simuladas(monkeypatch, tmp_path):
    e, u = _preparar(monkeypatch)
    est = {"desde": "2025-07-01", "ate": "2026-10-28", "meses": 16,
           "por_n": {"3": {"total": 0.31, "descida": -0.12, "trades": 300},
                     "5": {"total": 0.25, "descida": -0.08, "trades": 480}}}
    (tmp_path / "e.json").write_text(json.dumps(est))
    sim = simuladas.nova(HOJE)
    simuladas.passo(sim, HIST, HOJE)
    simuladas.guardar(tmp_path / "s.json", sim)
    monkeypatch.setattr(bot, "ESTUDO_POSICOES", tmp_path / "e.json")
    monkeypatch.setattr(bot, "SIMULADAS", tmp_path / "s.json")
    txt = _textos(bot.processar(e, _txt("⚙️ Regras"), HIST, HOJE, CFG))[0]
    assert "📚 <b>No histórico</b> (01/07/25 a 28/10/26, 16 meses" in txt
    assert "• <b>5</b> posições: <b>+25%</b> · pior descida -8% · 480 trades ← a tua" in txt
    assert "🧪 <b>Carteiras simuladas</b> (desde 29/10" in txt and "• <b>3</b> posições:" in txt


def test_comprei_mostra_os_sinais_e_mais_explica_os_botoes(monkeypatch):
    e, u = _preparar(monkeypatch)
    out = bot.processar(e, _txt("🟢 Comprei"), HIST, HOJE, CFG)
    assert "Que ação compraste?</b> Estes são os sinais" in _textos(out)[0]
    teclado = out[0][1]["reply_markup"]
    assert f"c|NOVO|{HOJE}" in teclado and "ct|" in teclado
    out = bot.processar(e, _cb(DONO, "ct|"), HIST, HOJE, CFG)
    assert "Escreve o ticker" in _textos(out)[-1] and u["aguarda"] == {"tipo": "ticker"}
    txt = _textos(bot.processar(e, _txt("⚙️ Mais"), HIST, HOJE, CFG))[0]
    assert "↩️ <b>Desfazer</b>: anula a última operação registada" in txt
