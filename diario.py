"""Execução completa: dados -> sinais -> acompanhamento -> Telegram.

O GitHub Actions corre isto de 30 em 30 minutos nos dias úteis. A primeira execução
de cada dia reconstrói o universo; as restantes só atualizam os últimos preços.
"""
import json
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

import passo1_universo
import passo2_sinais
from src import acompanhamento, resumos, telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RAIZ = Path(__file__).parent
DADOS = RAIZ / "data"
HIST = DADOS / "historico_sinais.csv"
ESTADO = DADOS / "estado_resumos.json"


def hora_dos_resumos(agora: pd.Timestamp, ex: dict, estado: dict) -> tuple[bool, bool]:
    """(manhã, fecho): manhã a partir da hora configurada (UTC); fecho depois de Wall Street fechar."""
    dia = f"{agora:%Y-%m-%d}"
    hh, mm = map(int, ex.get("resumo_manha_utc", "06:30").split(":"))
    manha = (agora.hour, agora.minute) >= (hh, mm) and estado.get("manha") != dia
    ny = agora.tz_convert(ZoneInfo("America/New_York"))
    fecho = (ny.hour, ny.minute) >= (16, ex.get("minutos_apos_fecho_eua", 20)) and estado.get("fecho") != dia
    return manha, fecho


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
    try:
        estado = json.loads(ESTADO.read_text())
    except (FileNotFoundError, ValueError):
        estado = {}
    manha, fecho = hora_dos_resumos(agora, config["execucao"], estado)
    forcar = os.environ.get("FORCAR_RESUMO", "")   # teste à mão no Actions; não conta como enviado
    manha, fecho = manha or forcar == "manha", fecho or forcar == "fecho"
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    if manha:
        try:
            telegram.enviar(resumos.resumo_manha(agora, filtrado))
        except Exception:  # um resumo falhado não pode travar os sinais
            logging.exception("Resumo da manhã falhou")
        if forcar != "manha":
            estado["manha"] = f"{agora:%Y-%m-%d}"
    if not novos.empty:
        telegram.enviar(f"📊 <b>Novos sinais ({pd.Timestamp.now(tz='UTC'):%d/%m %H:%M} UTC)</b> ({len(novos)})\n"
                        f"Preços da última cotação; confirma na XTB antes de entrar.\n{acompanhamento.resumo(hist)}")
        for s in novos.to_dict("records"):
            telegram.enviar(telegram.formatar_sinal(s))

    hist = acompanhamento.registar(hist, novos, data, config["sinais"]["max_dias"])
    hist.to_csv(HIST, index=False)
    if fecho:
        try:
            telegram.enviar(resumos.resumo_fecho(agora, precos, filtrado, acompanhamento.resumo(hist)))
        except Exception:
            logging.exception("Resumo do fecho falhou")
            telegram.enviar(f"📊 {data}: {acompanhamento.resumo(hist)}")
        if forcar != "fecho":
            estado["fecho"] = f"{agora:%Y-%m-%d}"
    ESTADO.write_text(json.dumps(estado))


if __name__ == "__main__":
    main()
