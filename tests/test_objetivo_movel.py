"""Objetivo móvel (saída pela média de 5 dias) e carteira recente lida pelos sinais."""
import subprocess

from cryptography.fernet import Fernet

import diario
from src import carteira as cart, cofre, cotacoes

VELAS = [["2026-10-26", 0, 0, 10.0], ["2026-10-27", 0, 0, 11.0], ["2026-10-28", 0, 0, 12.0],
         ["2026-10-29", 0, 0, 13.0], ["2026-10-30", 0, 0, 9.0]]


def test_alvo_da_media_de_5_dias():
    # Fecha acima da média de 5 dias (com o fecho de hoje) = fecha acima da média dos 4 fechos anteriores.
    assert cotacoes.alvo_media5(VELAS, "2026-10-30") == 11.5
    assert cotacoes.alvo_media5(VELAS[:3], "2026-10-30") is None


def test_carteira_mostra_objetivo_movel_na_barra():
    u = cart.novo_utilizador("Ana", dict(cart.REGRAS_BASE))
    cart.comprar(u, "PULL", 1, 10.0, "2026-10-30", {"chave": "PULL|2026-10-30", "stop": 9.0, "entrada": 10.0})
    txt = cart.formatar_carteira(u, {"PULL": 10.2}, "2026-10-30", {"PULL": 5}, alvos={"PULL": 11.5})
    assert "🛑 9,00 ━━━━●━━━━ ~11,50 🎯" in txt and "objetivo móvel" in txt
    assert "sai quando o bot avisar" in cart.formatar_carteira(u, {"PULL": 10.2}, "2026-10-30", {"PULL": 5})


def test_sinais_leem_a_carteira_mais_recente(monkeypatch):
    k = Fernet.generate_key()
    monkeypatch.setattr(cofre, "chave", lambda: k)
    novo = Fernet(k).encrypt(b'{"utilizadores": {"1": {"posicoes": {"A": 1}}}}')

    def correr(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=novo if cmd[1] == "show" else b"")
    monkeypatch.setattr(diario.subprocess, "run", correr)
    assert diario.ler_carteira_recente({"velho": True})["utilizadores"]["1"]["posicoes"] == {"A": 1}

    def falha(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(diario.subprocess, "run", falha)
    assert diario.ler_carteira_recente({"velho": True}) == {"velho": True}   # sem rede: a do início


def test_bid_ask_da_nasdaq_e_lado_certo(monkeypatch):
    from src import bot, precos_agora

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"primaryData": {"lastSalePrice": "$68.10", "bidPrice": "$68.00", "askPrice": "$68.30",
                                             "isRealTime": True}}}

    class S:
        def get(self, *a, **k):
            return R()
    q = precos_agora.nasdaq("NTNX", S())
    assert q["preco"] == 68.1 and q["bid"] == 68.0 and q["ask"] == 68.3
    # A carteira vale ao bid (como na XTB); quem compra paga o ask; sem lado, o último negócio.
    monkeypatch.setattr(bot, "COTAR", lambda ts: {"NTNX": 68.1})
    monkeypatch.setattr(bot, "COTAR_LADO", lambda ts, lado: {"NTNX": 68.0 if lado == "venda" else 68.3})
    assert bot._cotacoes(["NTNX"], "venda")["NTNX"] == 68.0 and bot._cotacoes(["NTNX"], "compra")["NTNX"] == 68.3
    assert bot._cotacoes(["NTNX"])["NTNX"] == 68.1
    u = cart.novo_utilizador("Ana", dict(cart.REGRAS_BASE))
    cart.comprar(u, "NTNX", 1, 68.2, "2026-10-30")
    txt = bot.carteira_agora(u, None, "2026-10-30")
    assert "🔴 <b>NTNX</b>   <b>-0,3%</b>" in txt and "preço de venda (bid)" in txt
