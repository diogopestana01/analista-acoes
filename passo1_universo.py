"""Passo 1: construir o universo, descarregar preços e filtrar por liquidez.

Uso:  python passo1_universo.py
Gera em data/:
  universo.csv            todas as ações encontradas
  precos.parquet          histórico diário
  universo_filtrado.csv   ações líquidas o suficiente para trading de curto prazo
"""
import logging
import time
from pathlib import Path

import pandas as pd

import yaml

from src import precos, universo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RAIZ = Path(__file__).parent
DADOS = RAIZ / "data"


MARCA = DADOS / "universo_atualizado_em.txt"


def universo_recente(horas: float = 20) -> bool:
    try:
        return time.time() - float(MARCA.read_text()) < horas * 3600 \
            and (DADOS / "precos.parquet").exists() and (DADOS / "universo_filtrado.csv").exists()
    except (FileNotFoundError, ValueError):
        return False


def atualizar_rapido(config: dict) -> None:
    """Execução de hora a hora: só descarrega os últimos dias das ações já filtradas."""
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    novos = precos.descarregar(filtrado["ticker"].tolist(), "5d", config["precos"]["lote"])
    hist = precos.juntar(pd.read_parquet(DADOS / "precos.parquet"), novos)
    precos.guardar(hist, DADOS / "precos.parquet")
    logging.info("Atualização rápida: %d ações, último dia %s", novos["ticker"].nunique(), hist["data"].max())


def main(forcar: bool = False) -> None:
    config = yaml.safe_load((RAIZ / "config.yaml").read_text())
    DADOS.mkdir(exist_ok=True)
    if not forcar and universo_recente():
        atualizar_rapido(config)
        return

    uni = universo.construir_universo(config, DADOS)
    uni.to_csv(DADOS / "universo.csv", index=False)
    logging.info("Universo: %d ações (%s)", len(uni), uni["bolsa"].value_counts().to_dict())

    cfg_p = config["precos"]
    hist = precos.juntar(precos.descarregar(uni["ticker"].tolist(), cfg_p["periodo"], cfg_p["lote"]),
                         pd.DataFrame(columns=precos.COLUNAS))
    precos.guardar(hist, DADOS / "precos.parquet")
    logging.info("Preços: %d linhas, %d ações", len(hist), hist["ticker"].nunique())

    filtrado = precos.filtrar_liquidez(hist, uni, precos.taxas_eur(), config["filtro_liquidez"])
    filtrado = filtrado.merge(uni[["ticker", "nome", "bolsa"]], on="ticker", how="left")
    filtrado.to_csv(DADOS / "universo_filtrado.csv", index=False)
    logging.info("Depois do filtro de liquidez: %d ações", len(filtrado))
    MARCA.write_text(str(time.time()))


if __name__ == "__main__":
    main()
