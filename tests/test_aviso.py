"""Aviso "vende já" do advogado do diabo fecha o sinal no registo (pedido de 12/11)."""
import pandas as pd

from src import acompanhamento, diario_trades


def _hist():
    return pd.DataFrame([
        {"data_sinal": "2026-11-09", "ticker": "AAA", "setup": "pullback", "direcao": "compra", "entrada": 100.0,
         "objetivo": float("nan"), "stop": 90.0, "max_dias": 10, "estado": "aberto", "data_fecho": float("nan"),
         "preco_fecho": float("nan"), "dias": float("nan"), "saida": "fecho_10"},
        {"data_sinal": "2026-11-10", "ticker": "BBB", "setup": "caiu_10", "direcao": "compra", "entrada": 50.0,
         "objetivo": float("nan"), "stop": 45.0, "max_dias": 5, "estado": "aberto", "data_fecho": float("nan"),
         "preco_fecho": float("nan"), "dias": float("nan"), "saida": "sma5"},
    ])


def test_fecha_so_o_sinal_avisado_ao_ultimo_preco():
    precos = pd.DataFrame({"data": ["2026-11-11", "2026-11-12", "2026-11-12"], "ticker": ["AAA", "AAA", "BBB"],
                           "close": [97.0, 95.5, 49.0]})
    alertas = [{"ticker": "AAA", "motivo": "Emissão de ações.", "data_sinal": "2026-11-09"}]
    h = acompanhamento.fechar_por_aviso(_hist(), alertas, precos, "2026-11-12")
    a, b = h.iloc[0], h.iloc[1]
    assert (a["estado"], a["data_fecho"], a["preco_fecho"], a["dias"]) == ("aviso", "2026-11-12", 95.5, 3)
    assert b["estado"] == "aberto"
    # sem alertas, nada muda; e o fecho por aviso conta como saída do modelo no diário de trades
    assert acompanhamento.fechar_por_aviso(_hist(), [], precos, "2026-11-12").equals(_hist())
    assert "aviso" in diario_trades.FECHADO


def test_sem_preco_nao_fecha():
    alertas = [{"ticker": "AAA", "motivo": "x", "data_sinal": "2026-11-09"}]
    h = acompanhamento.fechar_por_aviso(_hist(), alertas, pd.DataFrame(columns=["data", "ticker", "close"]), "2026-11-12")
    assert h.iloc[0]["estado"] == "aberto"


def test_carteira_diz_vender_quando_o_sinal_fechou_por_aviso(monkeypatch):
    from src import bot
    monkeypatch.setattr(bot, "_agenda", lambda: {})
    monkeypatch.setattr(bot, "_cotacoes", lambda *a, **k: {})
    monkeypatch.setattr(bot, "_modelo_trocas", lambda: None)
    h = pd.DataFrame([{"data_sinal": "2026-11-09", "ticker": "AAA", "setup": "pullback", "estado": "aviso",
                       "entrada": 100.0, "stop": 90.0, "saida": "fecho_10", "max_dias": 10}])
    u = {"posicoes": {"AAA": {"preco": 100.0, "qtd": 1, "data": "2026-11-10", "sinal": "AAA|2026-11-09"}},
         "regras": {"max_posicoes": 5}}
    v = bot.avaliar_posicoes(u, h, "2026-11-12")
    assert v["AAA"]["tipo"] == "vender" and "advogado do diabo" in v["AAA"]["texto"]
