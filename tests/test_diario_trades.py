"""Diário de trades: porque vendeu, compra acima do limite, venda antes do modelo e "tu vs modelo"."""
from src import bot, carteira as cart, diario_trades
from tests.test_carteira import CFG, DONO, _cb, _estado, _hist, _textos, _txt

HOJE = "2026-10-29"
# FECHOU: o modelo saiu no objetivo a 55 (+10%). ABERTO: o modelo ainda o tem.
HIST = _hist([{"data_sinal": "2026-10-26", "ticker": "FECHOU", "entrada": 50.0, "objetivo": 55.0, "stop": 47.0,
               "estado": "objetivo", "preco_fecho": 55.0, "data_fecho": HOJE},
              {"data_sinal": HOJE, "ticker": "ABERTO", "entrada": 20.0, "objetivo": 22.0, "stop": 19.0}])


def _comprar(e, u, t, data, preco):
    bot.processar(e, _cb(DONO, f"c|{t}|{data}"), HIST, HOJE, CFG)
    u["talao"].update(preco=preco, valor=round(preco * 0.2, 2), tudo=False, fixa=False)
    bot.processar(e, _cb(DONO, "b|ok"), HIST, HOJE, CFG)


def _vender(e, u, t, preco):
    bot.processar(e, _cb(DONO, f"w|{t}"), HIST, HOJE, CFG)
    u["talao"]["preco"] = preco
    return bot.processar(e, _cb(DONO, "b|t"), HIST, HOJE, CFG) and bot.processar(e, _cb(DONO, "b|ok"), HIST, HOJE, CFG)


def test_pergunta_porque_vendeu_e_regista_sozinho_o_resto(monkeypatch):
    monkeypatch.setattr(bot, "COTAR", lambda ts: {})
    e = _estado()
    u = e["utilizadores"][DONO]
    _comprar(e, u, "ABERTO", HOJE, 21.5)       # limite 21,00 (meio caminho até 22): comprou acima
    assert u["operacoes"][-1]["acima_limite"] is True
    out = _vender(e, u, "ABERTO", 21.0)
    txt = _textos(out)
    assert any("Porque vendeste <b>ABERTO</b>" in x for x in txt)
    op = u["operacoes"][-1]
    assert op["antes_modelo"] is True and op["acima_limite"] is True and op["sinal"] == f"ABERTO|{HOJE}"
    bot.processar(e, _cb(DONO, f"mv|{len(u['operacoes']) - 1}|ABERTO|medo"), HIST, HOJE, CFG)
    assert u["operacoes"][-1]["motivo"] == "medo"


def test_outro_motivo_por_escrito(monkeypatch):
    monkeypatch.setattr(bot, "COTAR", lambda ts: {})
    e = _estado()
    u = e["utilizadores"][DONO]
    _comprar(e, u, "FECHOU", "2026-10-26", 50.0)
    _vender(e, u, "FECHOU", 52.0)
    i = len(u["operacoes"]) - 1
    assert u["operacoes"][i]["antes_modelo"] is False   # o modelo já tinha fechado
    bot.processar(e, _cb(DONO, f"mv|{i}|FECHOU|outro"), HIST, HOJE, CFG)
    out = bot.processar(e, _txt(DONO, "fui de férias <já>"), HIST, HOJE, CFG)
    assert "registado no teu diário" in _textos(out)[0]
    assert u["operacoes"][i]["motivo"] == "outro" and u["operacoes"][i]["motivo_txt"] == "fui de férias &lt;já&gt;"


def test_tu_vs_modelo():
    u = cart.novo_utilizador("Ana", dict(cart.REGRAS_BASE))
    u["operacoes"] = [
        {"tipo": "venda", "ticker": "FECHOU", "qtd": 1, "preco": 52.0, "preco_compra": 50.0, "lucro": 2.0,
         "data": HOJE, "sinal": "FECHOU|2026-10-26", "motivo": "medo", "antes_modelo": True},
        {"tipo": "venda", "ticker": "ABERTO", "qtd": 1, "preco": 21.0, "preco_compra": 20.0, "lucro": 1.0,
         "data": HOJE, "sinal": f"ABERTO|{HOJE}", "motivo": "obj", "acima_limite": True}]
    d = diario_trades.comparar(u, HIST)
    assert d["n_comp"] == 1 and abs(d["real"] - 0.04) < 1e-9 and abs(d["modelo"] - 0.10) < 1e-9
    assert d["pendentes"] == 1 and d["por_motivo"]["medo"] == (1, 2.0)
    txt = diario_trades.texto(u, HIST)
    assert "tu <b>+4,0%</b>" in txt and "o modelo <b>+10,0%</b>" in txt and "-6,0 pontos para o modelo" in txt
    assert "😟 Medo/notícia +2,00 USD (1)" in txt and "1 compra acima do preço máximo" in txt
    assert "1 venda antes da saída do modelo" in txt
    assert diario_trades.texto(u, HIST, "2026-11-01") is None
