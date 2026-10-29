"""Vale a pena vender uma posição aberta para comprar um sinal novo?

Compara o que cada lado ainda tem para dar:
  sinal novo      retorno médio do setup no backtest (não o retorno esperado do alerta, que mistura
                  o histórico da própria ação e exagera nas ações com poucos casos)
  posição aberta  "valor que falta": quanto posições iguais (mesmo setup, mesmos dias já passados,
                  mesmo ganho atual medido em ATR) ainda renderam até à saída
Só troca se a diferença pagar o custo da troca (spread da XTB) e uma margem.

Validado em passo3e_trocas.py (carteira simulada nos últimos 40% do histórico): com 5 lugares
deu +22,3% contra +17,8% sem trocas e 56 trocas; a regra antiga (confiança x prazo + 15)
trocava 4 vezes mais e com 10 lugares perdia dinheiro (-11,7% contra +20,6%).
A tabela vem de data/valor_restante.json, gerado por passo3e_trocas.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

CAMINHO = Path(__file__).resolve().parent.parent / "data" / "valor_restante.json"


def carregar(caminho: Path = CAMINHO) -> dict | None:
    try:
        return json.loads(Path(caminho).read_text())
    except (FileNotFoundError, ValueError):
        return None


def balde(ganho: float, atr_pct: float, limites: list[float]) -> int:
    """Em que faixa está o ganho atual, medido em ATR (0 = a perder mais de 1 ATR)."""
    return int(np.digitize(ganho / max(atr_pct, 1e-4), limites))


def atr_pct(setup: str, entrada: float, stop: float, cfg: dict) -> float | None:
    """ATR em % do preço, a partir do stop do alerta (stop = entrada - k x ATR)."""
    from src import saidas
    k = saidas.regra_do_setup(setup, cfg).mult_stop
    try:
        a = (float(entrada) - float(stop)) / k / float(entrada)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return a if a > 0 and np.isfinite(a) else None


def valor_que_falta(setup: str, dias_usados: int, ganho: float, atr: float | None, modelo: dict) -> float | None:
    """Retorno que uma posição destas ainda costuma dar até à saída (0,004 = +0,4%).
    None no dia da compra: uma posição comprada hoje não se troca."""
    if dias_usados < 1:
        return None
    media = modelo["media"].get(setup)
    if atr is None:
        return media
    b = balde(ganho, atr, modelo["limites"])
    return modelo["tabela"].get(f"{setup}|{min(dias_usados, 5)}|{b}", media)


def vale_trocar(setup_novo: str, falta_fraca: float | None, modelo: dict) -> tuple[bool, float | None]:
    """(trocar?, vantagem do sinal novo depois de custos). Sem dados, não troca."""
    novo = modelo["media"].get(setup_novo)
    if novo is None or falta_fraca is None:
        return False, None
    vantagem = novo - falta_fraca - modelo["custo"]
    return vantagem > modelo["margem"], vantagem
