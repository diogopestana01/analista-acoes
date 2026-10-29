"""Lista de sinais reavaliada a cada corrida: estado (comprar / em andamento), força e trocas."""
import pytest

from src import bot, carteira as cart, lista_sinais
from tests.test_carteira import CFG, DONO, _cb, _estado, _hist, _textos

HOJE = "2026-10-29"
MODELO = {"limites": [-1.0, -0.25, 0.25, 1.0, 2.0], "custo": 0.003, "margem": 0.002,
          "media": {"rompimento": 0.004, "semana_forte": 0.012, "pullback": 0.004},
          "tabela": {"rompimento|2|5": 0.0005, "rompimento|2|4": 0.001}}  # 2 dias, ganho >2 ATR / 1-2 ATR
CFG_S = {"qualidade": {"rompimento": {"saida": "atr_3_15"}, "semana_forte": {"saida": "atr_3_15"}}}

# Objetivo 110, stop 95 (ATR 3,3% do preço): limite de compra = 105.
HIST = _hist([{"data_sinal": "2026-10-27", "ticker": "SUBIU", "entrada": 100.0, "objetivo": 110.0, "stop": 95.0,
               "retorno_esperado": 0.03},
              {"data_sinal": "2026-10-27", "ticker": "CEDO", "entrada": 50.0, "objetivo": 55.0, "stop": 47.5,
               "retorno_esperado": 0.01},
              {"data_sinal": HOJE, "ticker": "NOVO", "entrada": 20.0, "objetivo": 22.0, "stop": 19.0,
               "setup": "semana_forte", "retorno_esperado": 0.02},
              {"data_sinal": "2026-10-22", "ticker": "FIM", "entrada": 10.0, "objetivo": 11.0, "stop": 9.5},
              {"data_sinal": HOJE, "ticker": "STOP", "entrada": 30.0, "objetivo": 33.0, "stop": 29.0}])
PRECOS = {"SUBIU": 107.0, "CEDO": 50.5, "NOVO": 20.1, "FIM": 10.2, "STOP": 28.0}


def test_estado_e_forca_de_cada_sinal():
    assert lista_sinais.limite(100.0, 110.0, 95.0) == 105.0
    assert lista_sinais.limite(100.0, None, 95.0) == 102.5  # sem objetivo: a distância do stop
    itens = {s["ticker"]: s for s in lista_sinais.avaliar(HIST, PRECOS, HOJE, MODELO, CFG_S)}
    assert "STOP" not in itens  # já abaixo do stop: sai da lista
    assert itens["SUBIU"]["estado_lista"] == "andamento" and itens["CEDO"]["estado_lista"] == "comprar"
    assert itens["FIM"]["estado_lista"] == "andamento" and itens["FIM"]["forca"] == 0.0  # último dia
    # Subiu 7% (2,1 ATR) em 2 dias: já deu quase tudo, vale menos do que no dia do sinal.
    assert itens["SUBIU"]["forca"] == pytest.approx(0.0005) and itens["NOVO"]["forca"] == 0.012
    assert itens["CEDO"]["forca"] == 0.004 and itens["SUBIU"]["dias_usados"] == 2
    # Ordem pelo retorno esperado da ação (passo3g_reavaliar.py), não pelo que falta.
    ordem = [s["ticker"] for s in lista_sinais.avaliar(HIST, PRECOS, HOJE, MODELO, CFG_S)]
    assert ordem[:3] == ["SUBIU", "NOVO", "CEDO"]
    # Abaixo do limite de compra (105), mas já subiu 4% (1,2 ATR): o que falta (+0,1%) não paga o spread.
    itens = {s["ticker"]: s for s in lista_sinais.avaliar(HIST, {**PRECOS, "SUBIU": 104.0}, HOJE, MODELO, CFG_S)}
    assert itens["SUBIU"]["forca"] == 0.001 and itens["SUBIU"]["estado_lista"] == "andamento"


def _preparar(monkeypatch):
    monkeypatch.setattr(bot, "MODELO_TROCAS", MODELO)
    monkeypatch.setattr(bot, "CFG_SINAIS", CFG_S)
    monkeypatch.setattr(bot, "COTAR", lambda ts: {t: PRECOS[t] for t in ts if t in PRECOS})
    e = _estado()
    return e, e["utilizadores"][DONO]


def test_lista_mostra_lugares_comprar_e_andamento(monkeypatch):
    e, u = _preparar(monkeypatch)
    out = bot.processar(e, {"update_id": 1, "message": {"chat": {"id": int(DONO)}, "text": "📈 Sinais"}},
                        HIST, HOJE, CFG)
    txt = _textos(out)[0]
    assert "Os teus 5 lugares" in txt and "1. 🟢 <b>NOVO</b> esperado <b>+2,0%</b>" in txt
    assert "Em andamento" in txt and "<b>SUBIU</b> +7,0% desde o sinal" in txt
    assert "<b>FIM</b> +2,0% desde o sinal · último dia, sai ao fecho" in txt
    assert "STOP" not in txt
    out = bot.processar(e, _cb(DONO, f"ls|SUBIU|2026-10-27"), HIST, HOJE, CFG)
    assert "já não está para comprar" in _textos(out)[0]
    out = bot.processar(e, _cb(DONO, f"ls|CEDO|2026-10-27"), HIST, HOJE, CFG)
    assert "COMPRAR CEDO</b> a ~50,50" in _textos(out)[0] and u["precos_mostrados"]["CEDO|2026-10-27"] == 50.5


def test_reage_quando_a_posicao_ja_deu_o_que_tinha_a_dar(monkeypatch):
    e, u = _preparar(monkeypatch)
    bot.definir_posicoes(u, 1)
    cart.comprar(u, "SUBIU", 0.1, 100.0, "2026-10-27", bot._sinal(HIST, "SUBIU"))
    avisados = set()
    msgs = bot.mensagens_trocas(e, HIST, HOJE, avisados)
    # SUBIU ainda dá +0,05%; NOVO dá +1,2%: paga o spread e a margem.
    assert len(msgs) == 1 and "Vender <b>SUBIU</b>" in msgs[0][1] and "COMPRAR NOVO" in msgs[0][1]
    assert msgs[0][2][0][0][1] == f"s|SUBIU|NOVO|{HOJE}"
    assert bot.mensagens_trocas(e, HIST, HOJE, avisados) == []  # só uma vez
    # Quem carregou em ❌ Manter não volta a receber a proposta desse sinal.
    bot.processar(e, _cb(DONO, f"n|NOVO|{HOJE}"), HIST, HOJE, CFG)
    msgs = bot.mensagens_trocas(e, HIST, HOJE, set())
    assert all("COMPRAR NOVO" not in m[1] for m in msgs)
    assert bot.mensagens_trocas(None, HIST, HOJE, set()) == []
