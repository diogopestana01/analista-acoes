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
from src import acompanhamento, bot, cofre, telegram, vigia

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RAIZ = Path(__file__).parent
DADOS = RAIZ / "data"
HIST = DADOS / "historico_sinais.csv"
CARTEIRA = DADOS / "carteira.enc"


def ler_carteira() -> dict | None:
    """Estado da Carteira, só para leitura (quem escreve é o bot). Se falhar, os sinais seguem sem ela."""
    try:
        return cofre.carregar(CARTEIRA, cofre.chave()) if CARTEIRA.exists() else None
    except Exception as e:  # noqa: BLE001
        logging.warning("Carteira indisponível: %s", e)
        return None


def vigiar_posicoes(hist: pd.DataFrame, data: str, dono: str | None, config: dict) -> None:
    """Notícias de risco novas sobre posições abertas. Por agora o alerta vai só para o dono."""
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
    for a in alertas:
        telegram.enviar(vigia.formatar(a), dono)


def main() -> None:
    config = yaml.safe_load((RAIZ / "config.yaml").read_text())
    passo1_universo.main()
    saida = passo2_sinais.main()

    precos = pd.read_parquet(DADOS / "precos.parquet")
    hist = acompanhamento.carregar(HIST)
    hist, fechos = acompanhamento.atualizar(hist, precos)
    estado, dono = ler_carteira(), os.environ.get("TELEGRAM_CHAT_ID")
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
        # último preço guardado de cada ação: serve para avaliar as posições abertas (troca pela mais fraca)
        ultimo = precos.sort_values("data").groupby("ticker")["close"].last()
        bot.COTAR = lambda ts: {t: float(ultimo[t]) for t in ts if t in ultimo.index}
        for chat, texto, botoes in bot.mensagens_sinais(estado, registos, cabecalho, dono, hist, data):
            telegram.enviar(texto, chat, botoes)

    vigiar_posicoes(hist, data, dono, config)
    hist = acompanhamento.registar(hist, novos, data, config["sinais"]["max_dias"])
    hist.to_csv(HIST, index=False)


if __name__ == "__main__":
    main()
