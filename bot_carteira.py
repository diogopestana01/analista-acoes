"""Bot da Carteira: ouve o Telegram (long polling) e gere a carteira de cada utilizador.

Corre no workflow "Carteira" (sempre ligado, relança-se a cada ~5h40, como o Relógio).
É o único que escreve data/carteira.enc; os sinais (diario.py) só o leem.
Uso: python bot_carteira.py [--minutos 340]
"""
import argparse
import json
import logging
import os
import subprocess
import time
from pathlib import Path

import pandas as pd
import yaml

from src import acompanhamento, bot, cofre, precos, telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("carteira")
RAIZ = Path(__file__).parent
ESTADO = RAIZ / "data" / "carteira.enc"
HIST = RAIZ / "data" / "historico_sinais.csv"


def git(*args: str) -> bool:
    r = subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, text=True)
    if r.returncode:
        log.warning("git %s: %s", " ".join(args), (r.stderr or r.stdout)[-300:])
    return r.returncode == 0


def guardar_e_enviar(estado: dict, k: bytes, com_git: bool) -> None:
    cofre.guardar(ESTADO, estado, k)
    if not com_git:
        return
    git("add", str(ESTADO))
    if git("diff", "--cached", "--quiet"):
        return
    git("commit", "-q", "-m", "Carteira atualizada")
    for espera in (0, 2, 4, 8):
        time.sleep(espera)
        git("pull", "--rebase", "-q", "origin", "main")
        if git("push", "-q", "origin", "HEAD:main"):
            return
    log.error("Não consegui guardar a carteira no GitHub")


def executar(chamadas: list) -> None:
    for metodo, params in chamadas:
        telegram.api(metodo, **params)


def main(minutos: float) -> None:
    cfg = yaml.safe_load((RAIZ / "config_carteira.yaml").read_text())
    k = cofre.chave()
    bot.COTAR = precos.atuais  # sinais em aberto mostrados ao preço de agora
    if k is None:
        raise SystemExit("Falta TELEGRAM_TOKEN.")
    com_git = os.environ.get("CARTEIRA_GIT") == "1"
    estado = cofre.carregar(ESTADO, k, os.environ.get("TELEGRAM_CHAT_ID"))
    hoje = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    bot.garantir_dono(estado, cfg["regras_base"], hoje)
    guardar_e_enviar(estado, k, com_git)
    telegram.api("setMyCommands", commands=json.dumps([
        {"command": "start", "description": "Mostrar o menu de botões"},
        {"command": "carteira", "description": "Ver a carteira"},
        {"command": "ajuda", "description": "Como usar"}]))
    hist, ultimo_pull = acompanhamento.carregar(HIST), 0.0
    fim = time.time() + minutos * 60
    while time.time() < fim:
        upds = telegram.api("getUpdates", espera=60, timeout=40, offset=estado["offset"],
                            allowed_updates='["message","callback_query"]')
        agora = pd.Timestamp.now(tz="UTC")
        hoje = agora.strftime("%Y-%m-%d")
        if com_git and (upds or time.time() - ultimo_pull > 60):
            git("pull", "--rebase", "-q", "origin", "main")
            hist, ultimo_pull = acompanhamento.carregar(HIST), time.time()
        mudou = False
        for u in upds or []:
            estado["offset"] = u["update_id"] + 1
            try:
                executar(bot.processar(estado, u, hist, hoje, cfg))
            except Exception:  # uma mensagem estranha não pode parar o bot
                log.exception("Falhou a processar %s", u.get("update_id"))
            mudou = True
        if agora.dayofweek < 5 and agora.strftime("%H:%M") >= cfg["lembrete_utc"] and estado.get("lembrete") != hoje:
            estado["lembrete"] = hoje
            executar(bot.lembretes(estado, hist, hoje))
            mudou = True
        if mudou:
            guardar_e_enviar(estado, k, com_git)
        if upds is None:
            time.sleep(5)  # Telegram em baixo ou sem rede: não martelar


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutos", type=float, default=340)
    main(ap.parse_args().minutos)
