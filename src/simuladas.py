"""Carteiras simuladas ao vivo: o modelo seguido à risca com 3, 5, 8 e 10 posições.

Pedido do Pestana (30/10): para saber, com resultados reais e não só com o histórico, o que muda o
número de posições. Cada carteira começa com 1000 USD e faz, sozinha, exatamente o que o bot aconselha:
  - compra os primeiros sinais da lista que dão para comprar (ao ask, se houver) enquanto há lugares
  - vende no stop/objetivo, quando o sinal fecha no registo em papel (regra de saída ou prazo),
    e antes dos resultados (mesmo calendário dos avisos)
  - com a carteira cheia, troca pela regra de src/trocas.py (a mesma da proposta de troca)
Compras só com a bolsa aberta. Fica em data/simuladas.json (público: não tem dados de ninguém).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from . import bot, carteira as cart, cotacoes

log = logging.getLogger(__name__)

LUGARES = (3, 5, 8, 10)
CAPITAL = 1000.0


def nova(hoje: str) -> dict:
    out = {"desde": hoje, "carteiras": {}}
    for n in LUGARES:
        u = cart.novo_utilizador(f"Simulada {n}", {"capital_inicial": CAPITAL}, hoje=hoje)
        bot.definir_posicoes(u, n)
        out["carteiras"][str(n)] = u
    return out


def ler(caminho: Path, hoje: str) -> dict:
    try:
        d = json.loads(Path(caminho).read_text())
        if isinstance(d.get("carteiras"), dict):
            return d
    except (OSError, ValueError):
        pass
    return nova(hoje)


def guardar(caminho: Path, sim: dict) -> None:
    for u in sim["carteiras"].values():
        u["anterior"] = None  # o "desfazer" não serve aqui e duplicava o ficheiro
    Path(caminho).write_text(json.dumps(sim, ensure_ascii=False, indent=1, default=str))


def _vender(u: dict, t: str, preco: float, hoje: str, motivo: str) -> None:
    cart.vender(u, t, None, preco, hoje)
    u["operacoes"][-1]["motivo"] = motivo


def _saidas(u: dict, hist, hoje: str, agenda: dict, aberta: bool) -> bool:
    velas = bot._ler_cotacoes().get("acoes", {})
    bids = bot._cotacoes(list(u["posicoes"]), "venda") if aberta else {}
    mudou = False
    for t, p in list(u["posicoes"].items()):
        sx = cotacoes.saida_atingida(p, (velas.get(t) or {}).get("velas") or [])
        linha = bot._linha_sinal(t, p, hist) or {}
        fecho = bot._num(linha.get("preco_fecho"))
        if sx:
            _vender(u, t, sx["nivel"], hoje, "stop" if sx["tipo"] == "stop" else "obj")
        elif str(linha.get("estado", "aberto")) != "aberto" and fecho:
            _vender(u, t, fecho, hoje, "prazo")
        elif aberta and agenda.get(t) and bids.get(t):
            _vender(u, t, bids[t], hoje, "resultados")
        else:
            continue
        mudou = True
    return mudou


def _entradas(u: dict, itens: list[dict], hist, hoje: str) -> bool:
    mudou = False
    troca = bot._melhor_troca(u, itens, hist, hoje)
    if troca:
        s, fraca = troca[0], troca[1]
        bid = bot._cotacoes([fraca], "venda").get(fraca)
        if bid:
            _vender(u, fraca, bid, hoje, "troca")
            mudou = True
    for s in bot._compraveis(u, itens):
        if len(u["posicoes"]) >= u["regras"]["max_posicoes"]:
            break
        if bot._a_espera_da_abertura(s):
            continue
        preco = bot._num(s.get("ask")) or bot._num(s.get("preco")) or bot._num(s.get("entrada"))
        if not preco:
            continue
        tam = cart.tamanho(u, s["ticker"], preco)
        if tam["motivo"]:
            continue
        cart.comprar(u, s["ticker"], tam["qtd"], preco, hoje, bot._sinal(hist, s["ticker"], s["data_sinal"]))
        mudou = True
    return mudou


def passo(sim: dict, hist, hoje: str, agenda: dict | None = None, aberta: bool = True) -> bool:
    """Uma volta: saídas sempre (o registo pode fechar à noite); trocas e compras só com a bolsa aberta."""
    if hist is None or hist.empty:
        return False
    mudou = False
    itens = None
    for n, u in sim["carteiras"].items():
        try:
            mudou |= _saidas(u, hist, hoje, agenda or {}, aberta)
            if aberta:
                itens = bot._avaliados(u, hist, hoje) if itens is None else itens
                mudou |= _entradas(u, itens, hist, hoje)
        except Exception:  # noqa: BLE001  uma carteira com problema não para as outras
            log.exception("Carteira simulada de %s posições falhou", n)
    return mudou


def resultados(sim: dict | None) -> list[dict]:
    """Por carteira: valor ao preço de agora (bid), resultado, vendas e % ganhas."""
    out = []
    for n, u in sorted((sim or {}).get("carteiras", {}).items(), key=lambda x: int(x[0])):
        px = bot._cotacoes(list(u["posicoes"]), "venda")
        em_acoes = sum(p["qtd"] * (px.get(t) or p["preco"]) for t, p in u["posicoes"].items())
        total = u["caixa"] + em_acoes
        vendas = [o for o in u["operacoes"] if o.get("tipo") == "venda"]
        out.append({"n": int(n), "total": total, "pct": total / u["regras"]["capital_inicial"] - 1,
                    "vendas": len(vendas), "ganhas": sum((o.get("lucro") or 0) > 0 for o in vendas),
                    "abertas": len(u["posicoes"])})
    return out
