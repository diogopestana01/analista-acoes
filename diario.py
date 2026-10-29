"""Execução completa: dados -> sinais -> acompanhamento -> Telegram.

O GitHub Actions corre isto de 30 em 30 minutos nos dias úteis. Só fala quando há
sinais novos ou fechos; os resumos de abertura e fecho estão à parte (resumos_diarios.py). A primeira execução
de cada dia reconstrói o universo; as restantes só atualizam os últimos preços.
"""
import logging
import os
from pathlib import Path

import pandas as pd
import yaml

import passo1_universo
import passo2_sinais
from src import acompanhamento, bot, cofre, cotacoes, precos as precos_mod, telegram, vigia

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RAIZ = Path(__file__).parent
DADOS = RAIZ / "data"
HIST = DADOS / "historico_sinais.csv"
CARTEIRA = DADOS / "carteira.enc"
COTACOES = DADOS / "cotacoes.json"


def ler_carteira() -> dict | None:
    """Estado da Carteira, só para leitura (quem escreve é o bot). Se falhar, os sinais seguem sem ela."""
    try:
        return cofre.carregar(CARTEIRA, cofre.chave()) if CARTEIRA.exists() else None
    except Exception as e:  # noqa: BLE001
        logging.warning("Carteira indisponível: %s", e)
        return None


def vigiar_posicoes(hist: pd.DataFrame, data: str, dono: str | None, config: dict) -> None:
    """Notícias de risco novas sobre posições abertas, para quem as tem (e para o dono)."""
    if not config.get("vigia", {}).get("ativo", False):
        return
    import json
    caminho = DADOS / "avaliacoes_cache.json"
    try:
        cache = json.loads(caminho.read_text())
        # Uma vez por hora (a primeira corrida de cada hora) para não abusar do Google Notícias.
        hora = f"vigia_hora|{pd.Timestamp.now(tz='UTC'):%H}|{data}"
        if hora in cache:
            return
        cache[hora] = True
        nomes = pd.read_csv(DADOS / "universo_filtrado.csv").set_index("ticker")["nome"].fillna("").to_dict()
        alertas = vigia.vigiar(hist[hist["estado"] == "aberto"], nomes, cache, data)
        caminho.write_text(json.dumps(cache))
    except Exception as e:  # noqa: BLE001 — a vigia nunca pode parar os sinais
        logging.warning("Vigia das posições falhou: %s", e)
        return
    # Cada pessoa que tem a ação recebe o alerta com botões de venda; o dono recebe sempre.
    for chat, texto, botoes in bot.mensagens_vigia(ler_carteira(), alertas, dono, vigia.formatar):
        telegram.enviar(texto, chat, botoes)


def guardar_cotacoes(precos: pd.DataFrame, hist: pd.DataFrame, estado: dict | None) -> dict:
    em_carteira = {t for u in (_ativos(estado)) for t in u["posicoes"]}
    quero = em_carteira | set(hist.loc[hist["estado"] == "aberto", "ticker"])
    falta = sorted(t for t in em_carteira if t not in set(precos["ticker"]))
    if falta:  # compradas à mão fora do universo
        try:
            precos = pd.concat([precos, precos_mod.descarregar(falta, "1mo")], ignore_index=True)
        except Exception as e:  # noqa: BLE001
            logging.warning("Sem preços para %d ações fora do universo: %s", len(falta), e)
    dados = cotacoes.construir(precos, quero, pd.Timestamp.now(tz="UTC"))
    cotacoes.guardar(COTACOES, dados)
    logging.info("Cotações guardadas: %d ações", len(dados["acoes"]))
    return dados


def _ativos(estado: dict | None) -> list:
    return [u for u in (estado or {}).get("utilizadores", {}).values() if u.get("estado") == "ativo"]


def avisar_saidas(estado: dict | None, dados: dict) -> None:
    """Quem tem uma posição que tocou no stop ou no objetivo recebe o aviso (uma vez)."""
    import json
    caminho = DADOS / "avaliacoes_cache.json"
    try:
        cache = json.loads(caminho.read_text()) if caminho.exists() else {}
    except ValueError:
        cache = {}
    avisados = set(cache.get("saidas_avisadas", []))
    msgs = bot.mensagens_saidas(estado, dados, avisados)
    for chat, texto, botoes in msgs:
        telegram.enviar(texto, chat, botoes)
    if msgs:
        cache["saidas_avisadas"] = sorted(avisados)[-500:]
        caminho.write_text(json.dumps(cache))
    logging.info("Avisos de stop/objetivo: %d", len(msgs))


def main() -> None:
    config = yaml.safe_load((RAIZ / "config.yaml").read_text())
    passo1_universo.main()
    saida = passo2_sinais.main()

    precos = pd.read_parquet(DADOS / "precos.parquet")
    hist = acompanhamento.carregar(HIST)
    estado, dono = ler_carteira(), os.environ.get("TELEGRAM_CHAT_ID")
    # Preço de agora das ações em carteira e com sinal aberto: o bot mostra a carteira com ele e
    # cada posição é comparada com o seu stop e objetivo a cada corrida.
    dados = guardar_cotacoes(precos, hist, estado)
    bot.COTAR = lambda ts: cotacoes.precos_agora(dados, ts)
    avisar_saidas(estado, dados)
    hist, fechos = acompanhamento.atualizar(hist, precos)
    for chat, texto, botoes in bot.mensagens_fechos(estado, fechos, dono):
        telegram.enviar(texto, chat, botoes)

    try:
        sinais = pd.read_csv(saida)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        sinais = pd.DataFrame()
    data = saida.stem.removeprefix("sinais_") if saida else pd.Timestamp.today().strftime("%Y-%m-%d")
    abertos = set(hist.loc[hist["estado"] == "aberto", "ticker"])
    # Pausa depois de fechar: evita voltar a comprar logo a seguir a um objetivo ou stop.
    pausa = acompanhamento.em_pausa(hist, data, config["sinais"].get("pausa_apos_fecho_dias", 0))
    novos = sinais[~sinais["ticker"].isin(abertos | pausa)] if not sinais.empty else sinais

    if not novos.empty:
        cabecalho = (f"📊 <b>Novos sinais ({pd.Timestamp.now(tz='UTC'):%d/%m %H:%M} UTC)</b> ({len(novos)})\n"
                     f"Preços da última cotação; confirma na XTB antes de entrar.\n{acompanhamento.resumo(hist)}")
        registos = [{**s, "data_sinal": data} for s in novos.to_dict("records")]
        for chat, texto, botoes in bot.mensagens_sinais(estado, registos, cabecalho, dono, hist, data):
            telegram.enviar(texto, chat, botoes, silencioso=bot.sem_som(estado, chat))

    vigiar_posicoes(hist, data, dono, config)
    hist = acompanhamento.registar(hist, novos, data, config["sinais"]["max_dias"])
    hist.to_csv(HIST, index=False)


if __name__ == "__main__":
    main()
