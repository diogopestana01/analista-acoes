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

from src import acompanhamento, bot, cofre, cotacoes, precos, telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("carteira")
RAIZ = Path(__file__).parent
ESTADO = RAIZ / "data" / "carteira.enc"
HIST = RAIZ / "data" / "historico_sinais.csv"
COTACOES = RAIZ / "data" / "cotacoes.json"


def cotar(tickers: list[str]) -> dict:
    """Preço de agora: o que os sinais guardaram há menos de 10 min; o Yahoo só para o que faltar."""
    px = cotacoes.precos_agora(cotacoes.ler(COTACOES), tickers)
    falta = [t for t in tickers if t not in px]
    return {**(precos.atuais(falta) if falta else {}), **px}


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


def _chat(upd: dict) -> str:
    m = upd.get("message") or (upd.get("callback_query") or {}).get("message") or {}
    return str((m.get("chat") or {}).get("id", ""))


def _gh(*args: str) -> str | None:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    return r.stdout.strip() if r.returncode == 0 else None


def vigiar_relogio(dono: str | None) -> None:
    """O Relógio lança os sinais e vigia este bot; este bot vigia o Relógio. Se o Relógio estiver ativo
    mas sem nenhuma corrida, relança-o e avisa o dono. Desativado no Actions = desligado de propósito."""
    repo = os.environ.get("REPO")
    if not repo or not os.environ.get("GH_TOKEN"):
        return
    try:
        if _gh("api", f"repos/{repo}/actions/workflows/relogio.yml", "-q", ".state") != "active":
            return
        vivos = _gh("run", "list", "-w", "relogio.yml", "-R", repo, "-L", "10", "--json", "status",
                    "-q", '[.[] | select(.status != "completed")] | length')
        if vivos == "0" and _gh("workflow", "run", "relogio.yml", "-R", repo, "--ref", "main") is not None:
            log.warning("Relógio parado: relançado")
            if dono:
                telegram.api("sendMessage", chat_id=dono,
                             text="🔧 O relógio dos sinais tinha parado; voltei a ligá-lo.")
    except Exception as e:  # noqa: BLE001  a vigia nunca pode parar o bot
        log.warning("Vigia do Relógio falhou: %s", e)


def executar(chamadas: list) -> None:
    for metodo, params in chamadas:
        telegram.api(metodo, **params)


def main(minutos: float) -> None:
    cfg = yaml.safe_load((RAIZ / "config_carteira.yaml").read_text())
    k = cofre.chave()
    bot.COTAR = cotar  # carteira e sinais em aberto ao preço de agora
    bot.LER_COTACOES = lambda: cotacoes.ler(COTACOES)
    eu = telegram.api("getMe") or {}
    bot.USERNAME = eu.get("username")  # para os links de convite
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
    hist, ultimo_pull, ultima_vigia = acompanhamento.carregar(HIST), 0.0, 0.0
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
                chamadas = bot.processar(estado, u, hist, hoje, cfg)
                # registo sem ids nem nomes (os registos do Actions são públicos)
                quem = (estado["utilizadores"].get(_chat(u)) or {}).get("estado", "novo")
                log.info("Recebida %s de um utilizador %s: %d respostas",
                         "mensagem" if "message" in u else "carregadela", quem, len(chamadas))
                executar(chamadas)
            except Exception:  # uma mensagem estranha não pode parar o bot
                log.exception("Falhou a processar %s", u.get("update_id"))
            mudou = True
        if agora.day_of_week < 5 and agora.strftime("%H:%M") >= cfg["lembrete_utc"] and estado.get("lembrete") != hoje:
            estado["lembrete"] = hoje
            executar(bot.lembretes(estado, hist, hoje))
            mudou = True
        if (agora.day_of_week == 4 and agora.strftime("%H:%M") >= cfg.get("resumo_semanal_utc", "21:10")
                and estado.get("resumo_semanal") != hoje):
            estado["resumo_semanal"] = hoje
            executar(bot.resumos_semanais(estado, hoje))
            mudou = True
        if mudou:
            guardar_e_enviar(estado, k, com_git)
        if com_git and time.time() - ultima_vigia > 600:
            ultima_vigia = time.time()
            vigiar_relogio(os.environ.get("TELEGRAM_CHAT_ID"))
        if upds is None:
            time.sleep(5)  # Telegram em baixo ou sem rede: não martelar


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutos", type=float, default=340)
    main(ap.parse_args().minutos)
