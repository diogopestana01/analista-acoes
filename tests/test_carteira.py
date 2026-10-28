"""Testes da Carteira: tamanho de posição, compras/vendas, botões, vários utilizadores e cifra."""
import json

import pandas as pd
import pytest

from src import bot, carteira as cart, cofre

CFG = {"regras_base": dict(cart.REGRAS_BASE), "aviso": "Não é aconselhamento.", "max_pendentes": 2}
DONO = "111"
HOJE = "2026-10-28"


def _hist(linhas):
    base = {"setup": "rompimento", "direcao": "compra", "dias_estimados": 3.0, "max_dias": 5, "pontuacao": 70.0,
            "estado": "aberto", "data_fecho": None, "preco_fecho": None, "dias": None, "saida": "atr_3_15"}
    return pd.DataFrame([{**base, **l} for l in linhas])


HIST = _hist([{"data_sinal": HOJE, "ticker": "GMAB", "entrada": 50.0, "objetivo": 56.0, "stop": 47.0},
              {"data_sinal": HOJE, "ticker": "RAND.AS", "entrada": 40.0, "objetivo": 44.0, "stop": 38.0}])


def _estado():
    e = cart.novo_estado(DONO)
    bot.garantir_dono(e, CFG["regras_base"], HOJE)
    return e


def _cb(chat, dados, i="1"):
    return {"update_id": 5, "callback_query": {"id": i, "from": {"id": int(chat)}, "data": dados,
                                               "message": {"message_id": 9, "chat": {"id": int(chat)}}}}


def _txt(chat, texto, nome="Ana"):
    return {"update_id": 6, "message": {"chat": {"id": int(chat)}, "from": {"first_name": nome}, "text": texto}}


def _textos(chamadas):
    return [p["text"] for m, p in chamadas if m == "sendMessage"]


def test_numeros_em_portugues():
    assert cart.numero("135,40") == 135.4 and cart.numero("1.234,5") == 1234.5 and cart.numero("7") == 7
    with pytest.raises(ValueError):
        cart.numero("abc")


def test_tamanho_20_por_cento_e_sem_capital():
    u = _estado()["utilizadores"][DONO]
    t = cart.tamanho(u, "GMAB", 50.0)
    assert t["motivo"] is None and t["qtd"] == pytest.approx(0.4) and t["valor"] == pytest.approx(20.0)
    assert cart.tamanho(u, "RAND.AS", 40.0)["motivo"] == "fora_mercado"
    # Cinco sinais na mesma ronda enchem a carteira; o sexto fica sem capital.
    assert cart.tamanho(u, "X", 10.0, reservado=80.0, ocupadas=4)["motivo"] is None
    assert cart.tamanho(u, "X", 10.0, reservado=100.0, ocupadas=5)["motivo"] == "max_posicoes"


def test_botao_comprei_regista_ao_preco_do_sinal_e_tira_botoes():
    e = _estado()
    out = bot.processar(e, _cb(DONO, f"c|GMAB|{HOJE}"), HIST, HOJE, CFG)
    u = e["utilizadores"][DONO]
    assert u["posicoes"]["GMAB"]["qtd"] == pytest.approx(0.4) and u["caixa"] == pytest.approx(80.0)
    assert any(m == "editMessageReplyMarkup" for m, _ in out)
    assert "stop a 47,00" in _textos(out)[0]
    # Carregar outra vez não compra duas vezes.
    bot.processar(e, _cb(DONO, f"c|GMAB|{HOJE}"), HIST, HOJE, CFG)
    assert u["caixa"] == pytest.approx(80.0)


def test_outro_preco_e_aviso_de_ja_ter_fugido():
    e = _estado()
    bot.processar(e, _cb(DONO, f"o|GMAB|{HOJE}"), HIST, HOJE, CFG)
    out = bot.processar(e, _txt(DONO, "54,00 0,3"), HIST, HOJE, CFG)
    u = e["utilizadores"][DONO]
    assert u["posicoes"]["GMAB"]["preco"] == 54.0 and u["posicoes"]["GMAB"]["qtd"] == 0.3
    assert "acima do preço do sinal" in _textos(out)[0] and u["aguarda"] is None


def test_venda_parcial_total_e_desfazer():
    e = _estado()
    u = e["utilizadores"][DONO]
    cart.comprar(u, "GMAB", 0.4, 50.0, HOJE)
    bot.processar(e, _txt(DONO, "/vendi GMAB 0,1 55"), HIST, HOJE, CFG)
    assert u["posicoes"]["GMAB"]["qtd"] == pytest.approx(0.3)
    bot.processar(e, _txt(DONO, "/desfazer"), HIST, HOJE, CFG)
    assert u["posicoes"]["GMAB"]["qtd"] == pytest.approx(0.4)
    out = bot.processar(e, _cb(DONO, "v|GMAB|55.0000"), HIST, HOJE, CFG)
    assert "GMAB" not in u["posicoes"] and u["caixa"] == pytest.approx(102.0)
    assert "+2,00 USD (+10,0%)" in _textos(out)[0]
    assert cart.realizado(u) == (pytest.approx(2.0), 1, 1)


def test_ignorar_e_lembrete_do_fim_do_dia():
    e = _estado()
    bot.processar(e, _cb(DONO, f"i|GMAB|{HOJE}"), HIST, HOJE, CFG)
    hist = pd.concat([HIST, _hist([{"data_sinal": HOJE, "ticker": "NVDA", "entrada": 100.0,
                                    "objetivo": 110.0, "stop": 95.0}])])
    out = bot.lembretes(e, hist, HOJE)
    assert len(out) == 1 and "NVDA" in out[0][1]["text"] and "GMAB" not in out[0][1]["text"]
    assert bot.lembretes(e, hist, HOJE) == []  # só pergunta uma vez


def test_outras_pessoas_precisam_de_aprovacao_do_dono():
    e = _estado()
    out = bot.processar(e, _txt("222", "/start", "Rui"), HIST, HOJE, CFG)
    assert e["utilizadores"]["222"]["estado"] == "pendente"
    assert any(p["chat_id"] == DONO and "Rui" in p["text"] for m, p in out if m == "sendMessage")
    assert bot.processar(e, _txt("222", "/carteira"), HIST, HOJE, CFG) == []  # ainda não pode usar
    bot.processar(e, _cb("222", "a|222"), HIST, HOJE, CFG)  # só o dono aprova
    assert e["utilizadores"]["222"]["estado"] == "pendente"
    bot.processar(e, _cb(DONO, "a|222"), HIST, HOJE, CFG)
    assert e["utilizadores"]["222"]["estado"] == "ativo"
    out = bot.processar(e, _txt("222", "/carteira"), HIST, HOJE, CFG)
    assert "Carteira de Rui" in _textos(out)[0]
    # Um estranho não mexe na carteira de ninguém.
    assert bot.processar(e, _cb("333", f"c|GMAB|{HOJE}"), HIST, HOJE, CFG)[1:] == []


def test_mensagens_de_sinais_por_utilizador_com_tamanho():
    e = _estado()
    novos = [{"ticker": "GMAB", "data_sinal": HOJE, "setup": "rompimento", "direcao": "compra", "entrada": 50.0,
              "objetivo": 56.0, "stop": 47.0, "dias_estimados": 3, "saida": "atr_3_15"},
             {"ticker": "RAND.AS", "data_sinal": HOJE, "setup": "rompimento", "direcao": "compra", "entrada": 40.0,
              "objetivo": 44.0, "stop": 38.0, "dias_estimados": 3, "saida": "atr_3_15"}]
    msgs = bot.mensagens_sinais(e, novos, "cabeçalho", DONO)
    assert len(msgs) == 2  # o europeu nem aparece (conta só EUA)
    assert "<b>0,4</b> ações" in msgs[1][1] and msgs[1][2][0][0][1] == f"c|GMAB|{HOJE}"
    # Sem carteira (bot desligado), o dono recebe os sinais como antes.
    assert len(bot.mensagens_sinais(None, novos, "cab", DONO)) == 3


def test_aviso_de_venda_personalizado_para_quem_tem_a_acao():
    e = _estado()
    cart.comprar(e["utilizadores"][DONO], "GMAB", 0.4, 50.0, HOJE)
    f = {"ticker": "GMAB", "direcao": "compra", "data_sinal": HOJE, "resultado": "objetivo", "entrada": 50.0,
         "preco_atual": 57.0, "preco_saida": 56.0, "variacao_pct": 12.0}
    msgs = bot.mensagens_fechos(e, [f], DONO)
    assert len(msgs) == 1 and "Tens 0,4" in msgs[0][1] and msgs[0][2][0][0][1] == "v|GMAB|56.0000"


def test_carteira_simulada_entra_em_todos_os_sinais():
    hist = _hist([{"data_sinal": "2026-10-21", "ticker": "A", "entrada": 10.0, "estado": "objetivo",
                   "data_fecho": "2026-10-22", "preco_fecho": 11.0},
                  {"data_sinal": "2026-10-21", "ticker": "B", "entrada": 20.0, "estado": "stop",
                   "data_fecho": "2026-10-23", "preco_fecho": 19.0},
                  {"data_sinal": "2026-10-21", "ticker": "C.DE", "entrada": 5.0}])
    s = cart.simular(hist, cart.REGRAS_BASE)
    # 20 USD em cada: A +10% (+2), B -5% (-1).
    assert s["valor"] == pytest.approx(101.0) and s["fechadas"] == 2 and s["ganhas"] == 1


def test_cofre_cifra_e_so_abre_com_a_chave(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:abc")
    monkeypatch.delenv("CARTEIRA_CHAVE", raising=False)
    e = _estado()
    caminho = tmp_path / "carteira.enc"
    cofre.guardar(caminho, e, cofre.chave())
    assert b"capital_inicial" not in caminho.read_bytes()
    assert cofre.carregar(caminho, cofre.chave()) == json.loads(json.dumps(e))
    monkeypatch.setenv("TELEGRAM_TOKEN", "999:outro")
    with pytest.raises(RuntimeError):
        cofre.carregar(caminho, cofre.chave())


def test_menu_de_botoes_guia_compra_venda_deposito_e_regras():
    e = _estado()
    u = e["utilizadores"][DONO]
    out = bot.processar(e, _txt(DONO, "/start", "Pestana"), HIST, HOJE, CFG)
    assert "keyboard" in out[0][1]["reply_markup"] and "Carteira" in out[0][1]["reply_markup"]
    bot.processar(e, _txt(DONO, "🟢 Comprei"), HIST, HOJE, CFG)
    out = bot.processar(e, _txt(DONO, "gmab"), HIST, HOJE, CFG)
    assert "GMAB" in _textos(out)[0] and u["aguarda"]["data"] == HOJE
    bot.processar(e, _txt(DONO, "50 0,2"), HIST, HOJE, CFG)
    assert u["posicoes"]["GMAB"]["qtd"] == 0.2 and u["posicoes"]["GMAB"]["stop"] == 47.0
    out = bot.processar(e, _txt(DONO, "🔴 Vendi"), HIST, HOJE, CFG)
    assert "w|GMAB" in out[0][1]["reply_markup"]
    bot.processar(e, _cb(DONO, "w|GMAB"), HIST, HOJE, CFG)
    bot.processar(e, _txt(DONO, "55"), HIST, HOJE, CFG)
    assert "GMAB" not in u["posicoes"]
    bot.processar(e, _txt(DONO, "💵 Depositar"), HIST, HOJE, CFG)
    bot.processar(e, _txt(DONO, "50"), HIST, HOJE, CFG)
    assert u["caixa"] == pytest.approx(151.0)
    out = bot.processar(e, _txt(DONO, "⚙️ Regras"), HIST, HOJE, CFG)
    assert "x|10" in out[0][1]["reply_markup"]
    bot.processar(e, _cb(DONO, "x|10"), HIST, HOJE, CFG)
    assert u["regras"]["pct"] == 10 and u["regras"]["max_posicoes"] == 10
    bot.processar(e, _cb(DONO, "k|"), HIST, HOJE, CFG)
    bot.processar(e, _txt(DONO, "200"), HIST, HOJE, CFG)
    assert u["caixa"] == pytest.approx(200.0)
    # Carregar num botão do menu a meio de uma pergunta cancela a pergunta.
    bot.processar(e, _txt(DONO, "🟢 Comprei"), HIST, HOJE, CFG)
    bot.processar(e, _txt(DONO, "💼 Carteira"), HIST, HOJE, CFG)
    assert u["aguarda"] is None


def test_recomecar_pergunta_capital_e_posicoes_e_mostra_sinais_abertos():
    e = _estado()
    u = e["utilizadores"][DONO]
    cart.comprar(u, "GMAB", 0.4, 50.0, HOJE)
    bot.processar(e, _cb(DONO, f"i|GMAB|{HOJE}"), HIST, HOJE, CFG)
    out = bot.processar(e, _txt(DONO, "🔄 Recomeçar"), HIST, HOJE, CFG)
    assert "z|sim" in out[0][1]["reply_markup"] and u["posicoes"]  # ainda nada apagado
    bot.processar(e, _cb(DONO, "z|nao"), HIST, HOJE, CFG)
    assert u["posicoes"]
    out = bot.processar(e, _cb(DONO, "z|sim"), HIST, HOJE, CFG)
    assert not u["posicoes"] and not u["decisoes"]
    assert "Passo 1 de 2" in _textos(out)[0] and "cap|500" in out[-1][1]["reply_markup"]
    # Capital escrito à mão, depois o número de posições (a % por sinal acompanha).
    bot.processar(e, _cb(DONO, "cap|outro"), HIST, HOJE, CFG)
    out = bot.processar(e, _txt(DONO, "300"), HIST, HOJE, CFG)
    assert u["caixa"] == pytest.approx(300.0) and "Menos posições" in _textos(out)[0] and "100,00 USD" in _textos(out)[0]
    out = bot.processar(e, _cb(DONO, "pos|3"), HIST, HOJE, CFG)
    assert u["regras"]["max_posicoes"] == 3 and u["regras"]["pct"] == pytest.approx(33.33)
    textos = _textos(out)
    assert "Carteira pronta" in textos[0]
    # Recebe logo o sinal em aberto dos EUA (GMAB), com botões; o europeu fica de fora.
    assert any("Sinais ainda em aberto" in t and "o melhor" in t for t in textos)
    assert any("COMPRAR GMAB" in t and "1,9997" in t for t in textos)
    # Desfazer volta a antes do recomeço.
    bot.processar(e, _txt(DONO, "↩️ Desfazer"), HIST, HOJE, CFG)
    assert "GMAB" in u["posicoes"] and u["regras"]["capital_inicial"] == 100


def test_quem_e_aceite_configura_a_carteira_e_recebe_os_sinais_em_aberto():
    e = _estado()
    bot.processar(e, _txt("222", "/start", "Rui"), HIST, HOJE, CFG)
    out = bot.processar(e, _cb(DONO, "a|222"), HIST, HOJE, CFG)
    assert any(p["chat_id"] == "222" and "Como funciona" in p["text"] for m, p in out if m == "sendMessage")
    # Visita guiada de 3 ecrãs e, no fim, a configuração da carteira.
    assert "Um sinal" in _textos(bot.processar(e, _cb("222", "t|2|n"), HIST, HOJE, CFG))[0]
    out = bot.processar(e, _cb("222", "t|3|n"), HIST, HOJE, CFG)
    assert "Depois de comprar" in _textos(out)[0] and "t|fim|n" in out[-1][1]["reply_markup"]
    out = bot.processar(e, _cb("222", "t|fim|n"), HIST, HOJE, CFG)
    assert "Passo 1 de 2" in _textos(out)[0]
    bot.processar(e, _cb("222", "cap|500"), HIST, HOJE, CFG)
    out = bot.processar(e, _cb("222", "pos|5"), HIST, HOJE, CFG)
    rui = e["utilizadores"]["222"]
    assert rui["caixa"] == 500 and rui["regras"]["max_posicoes"] == 5
    assert any("COMPRAR GMAB" in t and "2" in t for t in _textos(out))


def test_cada_pessoa_so_recebe_os_melhores_sinais_que_cabem_na_carteira():
    e = _estado()
    u = e["utilizadores"][DONO]
    bot.definir_posicoes(u, 3)
    cart.comprar(u, "AAA", 1, 10.0, HOJE)  # 1 de 3 ocupada -> 2 lugares livres
    base = {"data_sinal": HOJE, "setup": "rompimento", "direcao": "compra", "objetivo": 22.0, "stop": 19.0,
            "dias_estimados": 3, "saida": "atr_3_15", "entrada": 20.0}
    novos = [{**base, "ticker": t, "pontuacao": p} for t, p in [("FRACO", 55.0), ("FORTE", 92.0), ("MEDIO", 70.0),
                                                                 ("AAA", 99.0)]]
    msgs = bot.mensagens_sinais(e, novos, "cab", DONO)
    textos = [m[1] for m in msgs]
    assert "os 2 melhores" in textos[0] and "2 lugares livres" in textos[0]
    assert "COMPRAR FORTE" in textos[1] and "Confiança 92/100 (n.º 1 de 3)" in textos[1]
    assert "COMPRAR MEDIO" in textos[2]
    assert "Ficaram de fora 1" in textos[3] and "FRACO" in textos[3] and msgs[3][2] is None
    # Carteira cheia e nenhum sinal claramente melhor: só um aviso, sem sinais.
    bot.definir_posicoes(u, 1)
    fracos = [{**n, "pontuacao": 60.0} for n in novos if n["ticker"] != "AAA"]
    msgs = bot.mensagens_sinais(e, fracos, "cab", DONO)
    assert len(msgs) == 1 and "carteira está cheia (1/1)" in msgs[0][1] and "Não entraram" in msgs[0][1]
    assert "AAA (força 50)" in msgs[0][1] and "COMPRAR" not in msgs[0][1]


def test_sinais_em_aberto_ao_preco_de_agora(monkeypatch):
    base = {"data_sinal": "2026-10-27", "estado": "aberto"}
    hist = _hist([{**base, "ticker": "SOBE", "entrada": 50.0, "objetivo": 56.0, "stop": 47.0, "pontuacao": 90.0},
                  {**base, "ticker": "STOP", "entrada": 50.0, "objetivo": 56.0, "stop": 47.0, "pontuacao": 85.0},
                  {**base, "ticker": "FUGIU", "entrada": 50.0, "objetivo": 56.0, "stop": 47.0, "pontuacao": 80.0},
                  {**base, "ticker": "BOM", "entrada": 50.0, "objetivo": 56.0, "stop": 47.0, "pontuacao": 70.0}])
    cot = {"SOBE": 56.5, "STOP": 46.0, "FUGIU": 54.0, "BOM": 51.0}
    monkeypatch.setattr(bot, "COTAR", lambda ts: {t: cot[t] for t in ts})
    e = _estado()
    u = e["utilizadores"][DONO]
    textos = [p["text"] for m, p in bot.sinais_abertos(DONO, u, hist)]
    assert "preço de agora" in textos[0]
    assert len(textos) == 2 and "COMPRAR BOM" in textos[1] and "51,00" in textos[1]  # só o BOM, ao preço atual
    # O botão Comprei regista ao preço mostrado (51), não ao do sinal (50).
    bot.processar(e, _cb(DONO, "c|BOM|2026-10-27"), hist, HOJE, CFG)
    assert u["posicoes"]["BOM"]["preco"] == 51.0 and u["posicoes"]["BOM"]["stop"] == 47.0


def test_convite_por_link_entra_sem_aprovacao_e_so_serve_uma_vez():
    e = _estado()
    bot.USERNAME = "AnalistaBot"
    try:
        out = bot.processar(e, _txt(DONO, "🎟️ Convidar"), HIST, HOJE, CFG)
    finally:
        bot.USERNAME = None
    link = _textos(out)[1].split("\n")[-1]
    assert link.startswith("https://t.me/AnalistaBot?start=")
    codigo = link.split("=")[1]
    # Só o dono vê o botão e só o dono cria convites.
    assert "Convidar" in out[0][1]["reply_markup"]
    out = bot.processar(e, _txt("222", f"/start {codigo}", "Rui"), HIST, HOJE, CFG)
    rui = e["utilizadores"]["222"]
    assert rui["estado"] == "ativo" and rui["convidado"]
    assert "Convidar" not in out[0][1]["reply_markup"]
    assert any(p["chat_id"] == DONO and "entrou com o teu convite" in p["text"] for m, p in out if m == "sendMessage")
    assert any("Como funciona" in t for t in _textos(out))
    # O mesmo link já não serve a outra pessoa: fica à espera de aprovação.
    out = bot.processar(e, _txt("333", f"/start {codigo}", "Ze"), HIST, HOJE, CFG)
    assert e["utilizadores"]["333"]["estado"] == "pendente" and "já foi usado" in _textos(out)[0]
    # Um convite caducado também não.
    bot.processar(e, _txt(DONO, "🎟️ Convidar"), HIST, "2026-10-01", CFG)
    velho = [c for c, v in e["convites"].items() if v["criado"] == "2026-10-01"][0]
    bot.processar(e, _txt("444", f"/start {velho}", "Ana"), HIST, HOJE, CFG)
    assert e["utilizadores"]["444"]["estado"] == "pendente"
    # Quem não é dono não cria convites.
    bot.processar(e, _txt("222", "🎟️ Convidar"), HIST, HOJE, CFG)
    assert len(e["convites"]) == 2


def test_ajuda_por_temas_e_porque_do_sinal():
    e = _estado()
    out = bot.processar(e, _txt(DONO, "❓ Ajuda"), HIST, HOJE, CFG)
    assert "h|saidas" in out[0][1]["reply_markup"] and "t|1|a" in out[0][1]["reply_markup"]
    assert "Stop" in _textos(bot.processar(e, _cb(DONO, "h|saidas"), HIST, HOJE, CFG))[0]
    out = bot.processar(e, _cb(DONO, "t|3|a"), HIST, HOJE, CFG)
    assert "t|fim|a" in out[-1][1]["reply_markup"]
    texto = _textos(bot.processar(e, _cb(DONO, f"q|GMAB|{HOJE}"), HIST, HOJE, CFG))[0]
    assert "Porquê GMAB" in texto and "Confiança 70/100" in texto


def test_nome_com_simbolos_nao_parte_o_html():
    e = _estado()
    out = bot.processar(e, _txt("222", "/start", "Zé & <Rui>"), HIST, HOJE, CFG)
    assert all("<Rui>" not in p["text"] for m, p in out if m == "sendMessage")
    assert "Zé &amp; &lt;Rui&gt;" in out[1][1]["text"]


def test_carteira_cheia_propoe_trocar_a_posicao_mais_fraca(monkeypatch):
    hist = _hist([{"data_sinal": "2026-10-22", "ticker": "VELHA", "entrada": 10.0, "objetivo": 11.0, "stop": 9.5,
                   "pontuacao": 55.0, "max_dias": 5},
                  {"data_sinal": HOJE, "ticker": "NOVA", "entrada": 20.0, "objetivo": 22.0, "stop": 19.0,
                   "pontuacao": 77.0}])
    e = _estado()
    u = e["utilizadores"][DONO]
    bot.definir_posicoes(u, 1)
    cart.comprar(u, "VELHA", 2, 10.0, "2026-10-22", bot._sinal(hist, "VELHA"))
    monkeypatch.setattr(bot, "COTAR", lambda ts: {"VELHA": 10.5, "NOVA": 20.0})
    nova = {**hist.iloc[1].to_dict(), "data_sinal": HOJE}
    msgs = bot.mensagens_sinais(e, [nova], "cab", DONO, hist, HOJE)
    # VELHA já usou 4 de 5 dias: força 55 x 1/5 = 11; NOVA tem 77 -> troca.
    assert "mas há um sinal melhor" in msgs[0][1]
    texto, botoes = msgs[1][1], msgs[1][2]
    assert "Vender <b>VELHA</b>" in texto and "Força 11" in texto and "COMPRAR NOVA" in texto
    assert botoes[0][0][1] == f"s|VELHA|NOVA|{HOJE}"
    out = bot.processar(e, _cb(DONO, f"s|VELHA|NOVA|{HOJE}"), hist, HOJE, CFG)
    assert "Troca registada" in _textos(out)[0]
    assert "VELHA" not in u["posicoes"] and u["posicoes"]["NOVA"]["preco"] == 20.0
    assert u["posicoes"]["NOVA"]["stop"] == 19.0
    # Desfazer anula a troca inteira.
    bot.processar(e, _txt(DONO, "↩️ Desfazer"), hist, HOJE, CFG)
    assert "VELHA" in u["posicoes"] and "NOVA" not in u["posicoes"]
    # Posição comprada hoje (força 55) contra um sinal de 65: diferença pequena, não troca.
    u["posicoes"]["VELHA"]["data"] = HOJE
    msgs = bot.mensagens_sinais(e, [{**nova, "pontuacao": 65.0}], "cab", DONO, hist, HOJE)
    assert len(msgs) == 1 and "Não entraram: NOVA (65)" in msgs[0][1]
