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

import requests

from src import (acompanhamento, bot, carteira, cofre, cotacoes, leitor_print, precos, precos_agora, resultados,
                 telegram)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("carteira")
RAIZ = Path(__file__).parent
ESTADO = RAIZ / "data" / "carteira.enc"
HIST = RAIZ / "data" / "historico_sinais.csv"
COTACOES = RAIZ / "data" / "cotacoes.json"
LISTA = RAIZ / "data" / "lista_sinais.json"
CACHE_SINAIS = RAIZ / "data" / "avaliacoes_cache.json"  # avisos já dados pelos sinais (diario.py)


VIVOS: dict[str, tuple[float, float]] = {}  # ticker -> (preço de agora, quando foi lido)


def cotar(tickers: list[str]) -> dict:
    """Preço de agora: o da vigia de 2 em 2 min (Nasdaq) se tiver menos de 5 min; senão o que os sinais
    guardaram (10 em 10 min); o Yahoo só para o que faltar."""
    px = cotacoes.precos_agora(cotacoes.ler(COTACOES), tickers)
    px.update({t: VIVOS[t][0] for t in tickers if t in VIVOS and time.time() - VIVOS[t][1] < 300})
    falta = [t for t in tickers if t not in px]
    return {**(precos.atuais(falta) if falta else {}), **px}


def ler_print(file_id: str, tipo: str) -> dict | None:
    """Descarrega a imagem do Telegram e lê os números (o Claude lê, o código confere)."""
    info = telegram.api("getFile", file_id=file_id) or {}
    caminho, token = info.get("file_path"), os.environ.get("TELEGRAM_TOKEN")
    if not caminho or not token:
        return None
    try:
        r = requests.get(f"https://api.telegram.org/file/bot{token}/{caminho}", timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("Imagem do Telegram falhou: %s", type(e).__name__)
        return None
    lido = leitor_print.verificar(leitor_print.ler(r.content, tipo))
    log.info("Print lido: %s", "números conferidos" if lido else "não deu para confiar")
    return lido


def nomes_universo() -> dict:
    """Ticker -> nome das ações dos EUA (listas da Nasdaq), para achar a ação de um print pelo nome."""
    try:
        from src import universo
        u = universo.universo_eua()
        return dict(zip(u["ticker"], u["nome"].fillna("")))
    except Exception as e:  # noqa: BLE001  sem nomes, o bot pergunta o ticker
        log.warning("Nomes das ações indisponíveis: %s", e)
        return {}


def bolsa_eua_aberta(agora: pd.Timestamp) -> bool:
    """Dias úteis entre as 13:25 e as 21:05 UTC (cobre a sessão de NY com e sem hora de verão)."""
    return agora.day_of_week < 5 and "13:25" <= agora.strftime("%H:%M") <= "21:05"


def vigiar_posicoes(estado: dict, agora: pd.Timestamp) -> bool:
    """De 2 em 2 min com a bolsa aberta: preço de agora (Nasdaq, Yahoo de reserva) das ações em carteira
    de todos e aviso de stop/objetivo a quem as tem, uma vez só. Devolve True se avisou alguém."""
    tickers = sorted({t for u in carteira.ativos(estado).values() for t in u["posicoes"]})
    if not tickers:
        return False
    vivos = precos_agora.agora(tickers)
    for t, q in vivos.items():
        VIVOS[t] = (q["preco"], time.time())
    hora = agora.strftime("%Y-%m-%dT%H:%MZ")
    dados = cotacoes.juntar_agora(cotacoes.ler(COTACOES), {t: q["preco"] for t, q in vivos.items()},
                                  agora.strftime("%Y-%m-%d"), hora)
    avisados = set(estado.get("saidas_avisadas", []))
    msgs = bot.mensagens_saidas(estado, dados, avisados)
    for chat, texto, botoes in msgs:
        telegram.enviar(texto, chat, botoes)
    if msgs:
        estado["saidas_avisadas"] = sorted(avisados)[-500:]
    log.info("Vigia de 2 em 2 min: %d preços de %d ações, %d avisos", len(vivos), len(tickers), len(msgs))
    return bool(msgs)


# ---------- gestão das posições (passou do diario.py para aqui: config.yaml gestao.no_bot) ----------

AGENDA = {"dia": "", "agenda": {}, "vistos": set()}  # calendário de resultados, pedido uma vez por dia


def gestao_no_bot() -> bool:
    try:
        return bool((yaml.safe_load((RAIZ / "config.yaml").read_text()).get("gestao") or {}).get("no_bot"))
    except (OSError, yaml.YAMLError, AttributeError):
        return False


def _json(caminho: Path) -> dict:
    try:
        return json.loads(caminho.read_text())
    except (OSError, ValueError):
        return {}


def _avisados(estado: dict, nome: str) -> set:
    """Avisos já dados pelo bot e pelos sinais: ninguém recebe o mesmo duas vezes na passagem."""
    return set(estado.get(nome) or []) | set(_json(CACHE_SINAIS).get(nome) or [])


def gerir(estado: dict, hist: pd.DataFrame, agora: pd.Timestamp, dono: str | None, trocas_agora: bool) -> bool:
    """Saídas do registo (prazo, regra, stop/objetivo ao fecho), resultados e trocas, a quem tem a posição.
    Os fechos são sempre marcados como vistos; só se avisa com gestao.no_bot ligado (senão é o diario.py)."""
    ativo, mudou = gestao_no_bot(), False
    desde = (agora - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    primeira = "fechos_vistos" not in estado
    novos, chaves = bot.fechos_do_registo(hist, set(estado.get("fechos_vistos") or []), desde)
    if novos and ativo and not primeira:
        avisados = _avisados(estado, "saidas_avisadas")
        msgs = bot.mensagens_fechos(estado, novos, dono, avisados)
        executar([("sendMessage", bot._msg(c, t, b)[1]) for c, t, b in msgs])
        estado["saidas_avisadas"] = sorted(avisados)[-500:]
        log.info("Saídas do registo: %d fechos, %d avisos", len(novos), len(msgs))
    if primeira or novos or set(estado.get("fechos_vistos") or []) != chaves:
        estado["fechos_vistos"], mudou = sorted(chaves), True
    if not ativo:
        return mudou
    hoje = agora.strftime("%Y-%m-%d")
    em_carteira = sorted({t for u in carteira.ativos(estado).values() for t in u["posicoes"]})
    if AGENDA["dia"] != hoje:
        AGENDA.update(dia=hoje, agenda={}, vistos=set())
    falta = [t for t in em_carteira if t not in AGENDA["vistos"]]
    if falta:
        try:
            AGENDA["agenda"].update(resultados.agenda(agora, falta, 1))
            AGENDA["vistos"] |= set(falta)
        except Exception:  # noqa: BLE001  sem calendário, tenta-se na volta seguinte
            log.warning("Calendário de resultados falhou")
    avisados = _avisados(estado, "resultados_avisados")
    msgs = bot.mensagens_resultados(estado, AGENDA["agenda"], avisados, hoje)
    if msgs:
        executar([("sendMessage", bot._msg(c, t, b)[1]) for c, t, b in msgs])
        estado["resultados_avisados"], mudou = sorted(avisados)[-500:], True
    if trocas_agora:
        avisados = _avisados(estado, "trocas_avisadas")
        msgs = bot.mensagens_trocas(estado, hist, hoje, avisados)
        if msgs:
            executar([("sendMessage", bot._msg(c, t, b)[1]) for c, t, b in msgs])
            estado["trocas_avisadas"] = sorted(avisados, key=lambda k: k.rsplit("|", 1)[-1])[-300:]
            mudou = True
    return mudou


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


def sinais_parados(agora: pd.Timestamp, ultimo_commit: pd.Timestamp | None, minutos: int = 40) -> bool:
    """Num dia útil entre as 08:00 e as 21:30 UTC, os sinais commitam de 10 em 10 min (cotacoes.json).
    Mais de `minutos` sem commit "Sinais de ..." = o Relógio ou os sinais pararam."""
    if agora.day_of_week >= 5 or not ("08:00" <= agora.strftime("%H:%M") <= "21:30"):
        return False
    return ultimo_commit is None or (agora - ultimo_commit).total_seconds() > minutos * 60


def ultimo_commit_sinais() -> pd.Timestamp | None:
    r = subprocess.run(["git", "log", "-1", "--grep=^Sinais de", "--format=%ct", "origin/main"],
                       cwd=RAIZ, capture_output=True, text=True)
    try:
        return pd.Timestamp(int(r.stdout.strip()), unit="s", tz="UTC")
    except ValueError:
        return None


def executar(chamadas: list) -> None:
    for metodo, params in chamadas:
        telegram.api(metodo, **params)


def main(minutos: float) -> None:
    cfg = yaml.safe_load((RAIZ / "config_carteira.yaml").read_text())
    k = cofre.chave()
    bot.COTAR = cotar  # carteira e sinais em aberto ao preço de agora
    bot.LER_COTACOES = lambda: cotacoes.ler(COTACOES)
    bot.LER_LISTA = lambda: _json(LISTA)  # a lista de sinais que os sinais gravam a cada corrida
    bot.LER_PRINT, bot.NOMES = ler_print, nomes_universo()
    bot.TALAO_URL = cfg.get("talao_url", "")
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
    hist, ultimo_pull, ultima_vigia, ultimo_aviso_sinais = acompanhamento.carregar(HIST), 0.0, 0.0, 0.0
    ultima_vigia_precos, ultima_gestao, ultimas_trocas = 0.0, 0.0, 0.0
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
        ny = agora.tz_convert("America/New_York")
        if (ny.day_of_week < 5 and cfg.get("resumo_manha_ny", "09:00") <= ny.strftime("%H:%M") < "09:30"
                and estado.get("resumo_manha") != hoje):
            # Meia hora antes de Nova Iorque abrir (com e sem hora de verão): resumo de cada pessoa.
            estado["resumo_manha"] = hoje
            abre = ny.replace(hour=9, minute=30).tz_convert("Europe/Lisbon").strftime("%H:%M")
            executar(bot.resumos_manha(estado, hist, hoje, abre))
            mudou = True
        if (agora.day_of_week == 4 and agora.strftime("%H:%M") >= cfg.get("resumo_semanal_utc", "21:10")
                and estado.get("resumo_semanal") != hoje):
            estado["resumo_semanal"] = hoje
            executar(bot.resumos_semanais(estado, hoje))
            mudou = True
        if bolsa_eua_aberta(agora) and time.time() - ultima_vigia_precos > cfg.get("vigia_precos_seg", 120):
            ultima_vigia_precos = time.time()
            try:
                mudou |= vigiar_posicoes(estado, agora)
            except Exception:  # noqa: BLE001  a vigia nunca pode parar o bot
                log.exception("Vigia dos preços falhou")
        if time.time() - ultima_gestao > 60:
            ultima_gestao = time.time()
            trocas_agora = bolsa_eua_aberta(agora) and time.time() - ultimas_trocas > 600
            ultimas_trocas = time.time() if trocas_agora else ultimas_trocas
            try:
                mudou |= gerir(estado, hist, agora, os.environ.get("TELEGRAM_CHAT_ID"), trocas_agora)
            except Exception:  # noqa: BLE001  a gestão nunca pode parar o bot
                log.exception("Gestão das posições falhou")
        if mudou:
            guardar_e_enviar(estado, k, com_git)
        if com_git and time.time() - ultima_vigia > 600:
            ultima_vigia = time.time()
            dono = os.environ.get("TELEGRAM_CHAT_ID")
            vigiar_relogio(dono)
            # O contrário da vigia do Relógio: se os sinais deixarem de correr, o dono sabe (1 vez por hora).
            ultimo = ultimo_commit_sinais()
            if dono and sinais_parados(agora, ultimo) and time.time() - ultimo_aviso_sinais > 3600:
                ultimo_aviso_sinais = time.time()
                desde = f" desde as {ultimo:%H:%M} UTC" if ultimo is not None else ""
                telegram.api("sendMessage", chat_id=dono,
                             text=f"⚠️ Os sinais não correm{desde}. Vê o separador Actions do GitHub "
                                  f"(workflows Relógio e Alertas do analista).")
        if upds is None:
            time.sleep(5)  # Telegram em baixo ou sem rede: não martelar


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutos", type=float, default=340)
    main(ap.parse_args().minutos)
