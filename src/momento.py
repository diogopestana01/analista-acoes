"""Melhor momento para comprar ou vender, por causa do spread.

Pedido do Pestana (30/10): o spread muda ao longo do dia (logo à abertura de NY é maior) e às vezes
compensa esperar uns minutos para comprar ou vender. Aqui compara-se o spread de agora com o spread
normal da ação (data/spreads.csv, gravado pelos sinais a cada corrida, só medidas com a bolsa já calma)
e diz-se se vale a pena esperar e quanto se poupa.

Só informação: a decisão é da pessoa e um stop nunca espera. O spread de cada corrida fica guardado,
para medir no fim da validação se esperar ganhou mesmo (o preço também mexe enquanto se espera).
"""
from __future__ import annotations

import logging
from datetime import time as hora
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

log = logging.getLogger(__name__)

NY, LISBOA = ZoneInfo("America/New_York"), ZoneInfo("Europe/Lisbon")
ABERTURA = hora(9, 30)
MINUTOS_AGITADOS = 30      # primeira meia hora de NY: spread costuma ser maior
MIN_MEDIDAS = 3            # medidas calmas precisas para saber o spread normal de uma ação
BARATO = 0.003             # abaixo de 0,3% (o custo que o backtest contava) não vale esperar
VEZES_NORMAL = 1.5         # "alto" = 1,5x o normal da ação...
DIF_MIN = 0.0015           # ...e pelo menos 0,15 pontos acima
ALTO_ABERTURA = 0.005      # sem histórico: na primeira meia hora, acima de 0,5% é alto

_cache: dict = {}


def minutos_desde_abertura(agora: pd.Timestamp | None = None) -> float | None:
    """Minutos desde a abertura de NY de hoje (None fora da sessão normal ou ao fim de semana)."""
    agora = pd.Timestamp.now(tz="UTC") if agora is None else pd.Timestamp(agora)
    if agora.tzinfo is None:
        agora = agora.tz_localize("UTC")
    ny = agora.tz_convert(NY)
    if ny.weekday() >= 5:
        return None
    m = (ny.hour * 60 + ny.minute) - (ABERTURA.hour * 60 + ABERTURA.minute)
    return float(m) if 0 <= m < 390 else None


def hora_calma_lisboa(agora: pd.Timestamp | None = None) -> str:
    """Hora de Lisboa em que acaba a primeira meia hora de NY (ex. "14:00")."""
    agora = pd.Timestamp.now(tz="UTC") if agora is None else pd.Timestamp(agora)
    if agora.tzinfo is None:
        agora = agora.tz_localize("UTC")
    ny = agora.tz_convert(NY)
    dia = ny.normalize().tz_localize(None)
    if ny.weekday() >= 5 or ny.hour >= 16:  # já fechou: a próxima sessão (a mudança de hora conta)
        dia = dia + pd.offsets.BDay(1)
    calma = pd.Timestamp(dia.year, dia.month, dia.day, ABERTURA.hour, ABERTURA.minute, tz=NY)
    calma += pd.Timedelta(minutes=MINUTOS_AGITADOS)
    return f"{calma.tz_convert(LISBOA):%H:%M}"


def normais(caminho: Path | None) -> dict[str, float]:
    """{ticker: spread mediano} das medidas feitas depois da primeira meia hora de NY (≥ MIN_MEDIDAS).
    Lê data/spreads.csv uma vez por alteração do ficheiro."""
    if caminho is None or not Path(caminho).exists():
        return {}
    try:
        chave = (str(caminho), Path(caminho).stat().st_mtime)
        if _cache.get("chave") == chave:
            return _cache["valor"]
        d = pd.read_csv(caminho)
        quando = pd.to_datetime(d["hora"], utc=True, errors="coerce")
        mins = quando.map(lambda x: minutos_desde_abertura(x) if pd.notna(x) else None)
        calmas = d[pd.to_numeric(mins, errors="coerce") >= MINUTOS_AGITADOS]
        g = calmas.groupby("ticker")["spread_pct"].agg(["median", "count"])
        valor = {t: float(r["median"]) for t, r in g.iterrows() if r["count"] >= MIN_MEDIDAS}
    except Exception as e:  # noqa: BLE001  sem histórico, fica só a regra da primeira meia hora
        log.warning("Spreads normais não lidos: %s", e)
        return {}
    _cache.update(chave=chave, valor=valor)
    return valor


def conselho(spread: float | None, normal: float | None, agora: pd.Timestamp | None = None) -> dict | None:
    """Se compensa esperar: {"poupa": fração do preço que se poupa ao esperar, "porque": "normal"/"abertura",
    "normal", "ate"}. Compra-se ao ask e vende-se ao bid, ou seja a meio spread do preço do meio; esperar
    que o spread volte ao normal poupa metade da diferença."""
    if spread is None or spread < BARATO:
        return None
    mins = minutos_desde_abertura(agora)
    if mins is None:
        return None
    if normal is not None:
        if spread >= VEZES_NORMAL * normal and spread - normal >= DIF_MIN:
            return {"poupa": (spread - normal) / 2, "porque": "normal", "normal": normal,
                    "ate": hora_calma_lisboa(agora) if mins < MINUTOS_AGITADOS else None}
        return None
    if mins < MINUTOS_AGITADOS and spread >= ALTO_ABERTURA:
        return {"poupa": None, "porque": "abertura", "normal": None, "ate": hora_calma_lisboa(agora)}
    return None


def _pct(x: float) -> str:
    return f"{x * 100:.2f}".replace(".", ",")


def texto(c: dict | None, spread: float, lado: str = "compra") -> str:
    """Linha para o Telegram (vazio se não compensa esperar)."""
    if not c:
        return ""
    verbo = "comprar" if lado == "compra" else "vender"
    quando = f" (até às {c['ate']})" if c.get("ate") else ""
    if c["porque"] == "normal":
        return (f"⏳ Spread alto agora: {_pct(spread)}%, o normal desta ação é {_pct(c['normal'])}%. "
                f"Se não tiveres pressa, espera uns minutos{quando} para {verbo}: poupas ~{_pct(c['poupa'])}%.")
    return (f"⏳ Primeira meia hora de NY: o spread está em {_pct(spread)}% e costuma baixar. "
            f"Se não tiveres pressa, espera{quando} para {verbo}.")


def curto(c: dict | None) -> str:
    """Versão de uma palavra para o ranking."""
    if not c:
        return ""
    return f"⏳ espera{' até ' + c['ate'] if c.get('ate') else ' uns min'}"
