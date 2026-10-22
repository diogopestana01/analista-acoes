"""Resumos diários no Telegram, à parte do modelo de sinais.

Uso:  python resumos_diarios.py manha     (resumo de abertura)
      python resumos_diarios.py fecho     (resumo de fecho; só sai na janela depois de Wall Street fechar)
      python resumos_diarios.py fecho --forcar   (envia já, para testar)

Lê os preços que o modelo de sinais guardou (só leitura) e o histórico de sinais
do repositório. Se este script falhar, os sinais continuam a funcionar.
"""
import argparse
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from src import acompanhamento, resumos, telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RAIZ = Path(__file__).parent
DADOS = RAIZ / "data"


def na_janela_do_fecho(agora: pd.Timestamp, janela: list[str]) -> bool:
    """O relógio chama o fecho às 20:35 e às 21:35 UTC; só uma delas cai na janela de Nova Iorque
    (hora de verão ou de inverno), por isso o resumo sai uma vez por dia sem guardar estado."""
    ny = agora.tz_convert(ZoneInfo("America/New_York"))
    ini, fim = (tuple(map(int, x.split(":"))) for x in janela)
    return ini <= (ny.hour, ny.minute) <= fim


def main(tipo: str, forcar: bool = False) -> None:
    cfg = yaml.safe_load((RAIZ / "config_resumos.yaml").read_text())
    agora = pd.Timestamp.now(tz="UTC")
    try:
        filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    except FileNotFoundError:
        filtrado = pd.DataFrame(columns=["ticker", "nome"])

    if tipo == "manha":
        telegram.enviar(resumos.resumo_manha(agora, filtrado, cfg))
        return
    if not forcar and not na_janela_do_fecho(agora, cfg["fecho"]["janela_nova_iorque"]):
        logging.info("Fora da janela do fecho de Nova Iorque; nada a enviar")
        return
    # Preços frescos do fecho (os guardados pelos sinais podem ser de antes do fecho).
    # Só altera a cópia local deste job; os dados dos sinais não são tocados.
    try:
        import passo1_universo
        passo1_universo.atualizar_rapido(yaml.safe_load((RAIZ / "config.yaml").read_text()))
    except Exception:
        logging.exception("Não consegui atualizar os preços; uso os guardados")
    precos = pd.read_parquet(DADOS / "precos.parquet")
    carteira = acompanhamento.resumo(acompanhamento.carregar(DADOS / "historico_sinais.csv"))
    telegram.enviar(resumos.resumo_fecho(agora, precos, filtrado, carteira, cfg))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("tipo", choices=["manha", "fecho"])
    p.add_argument("--forcar", action="store_true")
    a = p.parse_args()
    main(a.tipo, a.forcar)
