"""Execução completa: dados -> sinais -> acompanhamento -> Telegram.

O GitHub Actions corre isto de 30 em 30 minutos nos dias úteis. A primeira execução
de cada dia reconstrói o universo; as restantes só atualizam os últimos preços.
"""
import logging
from pathlib import Path

import pandas as pd
import yaml

import passo1_universo
import passo2_sinais
from src import acompanhamento, telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RAIZ = Path(__file__).parent
DADOS = RAIZ / "data"
HIST = DADOS / "historico_sinais.csv"


def main() -> None:
    config = yaml.safe_load((RAIZ / "config.yaml").read_text())
    passo1_universo.main()
    saida = passo2_sinais.main()

    precos = pd.read_parquet(DADOS / "precos.parquet")
    hist = acompanhamento.carregar(HIST)
    hist, fechos = acompanhamento.atualizar(hist, precos)
    for f in fechos:
        telegram.enviar(telegram.formatar_fecho(f))

    try:
        sinais = pd.read_csv(saida)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        sinais = pd.DataFrame()
    data = saida.stem.removeprefix("sinais_") if saida else pd.Timestamp.today().strftime("%Y-%m-%d")
    abertos = set(hist.loc[hist["estado"] == "aberto", "ticker"])
    novos = sinais[~sinais["ticker"].isin(abertos)] if not sinais.empty else sinais

    agora = pd.Timestamp.now(tz="UTC")
    ex = config["execucao"]
    ultima_hora = (agora.hour, agora.minute) >= (ex["hora_resumo_utc"], ex.get("minuto_resumo_utc", 0))
    if novos.empty:
        if ultima_hora:  # de hora a hora só se fala quando há novidades; resumo no fim do dia
            telegram.enviar(f"📊 {data}: sem novos sinais.\n{acompanhamento.resumo(hist)}")
    else:
        telegram.enviar(f"📊 <b>Novos sinais ({pd.Timestamp.now(tz='UTC'):%d/%m %H:%M} UTC)</b> ({len(novos)})\n"
                        f"Preços da última cotação; confirma na XTB antes de entrar.\n{acompanhamento.resumo(hist)}")
        for s in novos.to_dict("records"):
            telegram.enviar(telegram.formatar_sinal(s))

    hist = acompanhamento.registar(hist, novos, data, config["sinais"]["max_dias"])
    hist.to_csv(HIST, index=False)


if __name__ == "__main__":
    main()
