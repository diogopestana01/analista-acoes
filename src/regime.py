"""Regime do mercado: quando o modelo não abre posições novas.

Duas regras (aprovadas pelo Pestana a 6/11; estudos passo3p_regime.py e passo3q_skills.py, carteira de
5 lugares com a ordem do bot, nas duas metades do histórico):
  amplitude   só se compram ações dos EUA quando pelo menos `amplitude_min` (50%) das ações dos EUA do
              universo fecharam acima da sua média de 50 dias (o último fecho completo). Com o mercado
              estreito, os sinais rendem menos.
  travão      se as vendas dos últimos `janela_dias` (7) da carteira simulada que segue o modelo
              (`carteira`, a de 5 lugares em data/simuladas.json) somarem uma perda maior do que `perda`
              (5%) do capital inicial, não se abre nada durante `pausa_dias` (5) dias de calendário.
              Ideia do "Risk Manager" das skills de day trading (limite de perda por período).
Juntas: +16,0% | +24,0% (pior queda -12%) contra +3,8% | -0,9% (pior queda -26%) sem nada.
As posições abertas seguem as suas regras de saída; só as compras novas param. Os sinais continuam a
ser registados em papel, para se medir o que se perdeu ou evitou.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


def amplitude(precos: pd.DataFrame, tickers, sem_hoje: str | None = None) -> float | None:
    """% das ações `tickers` com o último fecho acima da média de 50 dias. `sem_hoje`: data (AAAA-MM-DD)
    a ignorar porque a vela ainda não fechou."""
    p = precos[precos["ticker"].isin(set(tickers))][["data", "ticker", "close"]].copy()
    if p.empty:
        return None
    p["data"] = pd.to_datetime(p["data"]).dt.strftime("%Y-%m-%d")
    if sem_hoje:
        p = p[p["data"] < sem_hoje]
    datas = sorted(p["data"].unique())[-60:]   # só o fim do histórico: chega para a média de 50 dias
    p = p[p["data"].isin(set(datas))].sort_values(["ticker", "data"])
    media = p.groupby("ticker")["close"].rolling(50).mean().reset_index(level=0, drop=True)
    p = p.assign(acima=p["close"] > media, ok=media.notna())
    ultimo = p.groupby("ticker").tail(1)
    ultimo = ultimo[ultimo["ok"]]
    return float(ultimo["acima"].mean()) if len(ultimo) >= 50 else None


def perda_recente(simuladas: dict, carteira: str, hoje: str, janela_dias: int) -> float | None:
    """Soma do lucro das vendas dos últimos `janela_dias`, a dividir pelo capital inicial (negativo = perda)."""
    c = (simuladas.get("carteiras") or {}).get(str(carteira))
    if not c:
        return None
    capital = float((c.get("regras") or {}).get("capital_inicial") or 0)
    if capital <= 0:
        return None
    inicio = (pd.Timestamp(hoje) - pd.Timedelta(days=janela_dias)).strftime("%Y-%m-%d")
    lucro = sum(float(o.get("lucro") or 0) for o in c.get("operacoes") or []
                if o.get("tipo") == "venda" and inicio < str(o.get("data"))[:10] <= hoje)
    return lucro / capital


def estado(precos: pd.DataFrame, tickers_eua, cfg: dict | None, hoje: str, bolsa_aberta: bool,
           simuladas_path: Path, estado_path: Path) -> dict:
    """Estado do regime nesta corrida. Grava em `estado_path` até quando dura o travão."""
    cfg = cfg or {}
    try:
        guardado = json.loads(estado_path.read_text()) if estado_path.exists() else {}
    except (ValueError, OSError):
        guardado = {}
    amp_min = cfg.get("amplitude_min")
    amp = amplitude(precos, tickers_eua, hoje if bolsa_aberta else None) if amp_min is not None else None
    t = cfg.get("travao") or {}
    perda, ate = None, guardado.get("travao_ate")
    if t:
        try:
            sim = json.loads(simuladas_path.read_text()) if simuladas_path.exists() else {}
        except (ValueError, OSError):
            sim = {}
        perda = perda_recente(sim, t.get("carteira", "5"), hoje, int(t.get("janela_dias", 7)))
        if (not ate or hoje >= ate) and perda is not None and perda < -float(t.get("perda", 0.05)):
            ate = (pd.Timestamp(hoje) + pd.Timedelta(days=int(t.get("pausa_dias", 5)))).strftime("%Y-%m-%d")
            log.info("Travão de perdas: vendas de 7 dias %.1f%% -> sem compras novas até %s", perda * 100, ate)
    travao = bool(t) and bool(ate) and hoje < ate
    amplitude_baixa = amp_min is not None and amp is not None and amp < float(amp_min)
    r = {"amplitude": amp, "amplitude_min": amp_min, "amplitude_baixa": amplitude_baixa,
         "perda_7d": perda, "travao": travao, "travao_ate": ate if travao else None,
         "avisado": guardado.get("avisado")}
    try:
        estado_path.write_text(json.dumps({**guardado, "travao_ate": ate, "amplitude": amp, "perda_7d": perda,
                                           "atualizado": hoje}))
    except OSError as e:
        log.warning("Estado do regime não gravado: %s", e)
    return r


def bloqueia(r: dict | None, ticker: str) -> str | None:
    """Motivo para não comprar `ticker` agora (None = pode). A amplitude só vale para os EUA."""
    if not r:
        return None
    if r.get("travao"):
        return f"travão de perdas: a carteira do modelo perdeu mais de 5% em 7 dias (até {r['travao_ate']})"
    if r.get("amplitude_baixa") and "." not in ticker:
        return f"mercado fraco: só {r['amplitude'] * 100:.0f}% das ações acima da média de 50 dias"
    return None


def aplicar(itens: list[dict], r: dict | None) -> list[dict]:
    """Na lista de sinais, os que dariam para comprar passam a 'andamento', com o motivo em 'pausa_mercado'."""
    for s in itens:
        motivo = bloqueia(r, s.get("ticker", ""))
        s["pausa_mercado"] = motivo
        if motivo and s.get("estado_lista") == "comprar":
            s["estado_lista"] = "andamento"
    return itens


def texto_aviso(r: dict) -> str | None:
    """Mensagem ao dono quando a pausa começa ou acaba (uma vez por mudança)."""
    if r.get("travao"):
        return (f"⏸️ <b>Compras em pausa até {r['travao_ate']}</b>\nA carteira que segue o modelo perdeu "
                f"{-r['perda_7d'] * 100:.1f}% em 7 dias. As posições abertas seguem as regras de saída; "
                "não entram compras novas (travão de perdas).")
    if r.get("amplitude_baixa"):
        return (f"⏸️ <b>Compras nos EUA em pausa</b>\nSó {r['amplitude'] * 100:.0f}% das ações estão acima da "
                f"média de 50 dias (mínimo {r['amplitude_min'] * 100:.0f}%). Voltam quando o mercado alargar.")
    return "▶️ <b>Compras retomadas</b>\nO mercado voltou a passar os filtros do modelo."


def chave(r: dict) -> str:
    return "travao" if r.get("travao") else "amplitude" if r.get("amplitude_baixa") else "livre"


def avisar(r: dict, estado_path: Path, enviar) -> None:
    """Avisa (com `enviar(texto)`) só quando o estado muda: pausa a começar, mudar de motivo ou acabar."""
    k = chave(r)
    if k == (r.get("avisado") or "livre"):
        return
    if enviar(texto_aviso(r)) is not False:
        try:
            g = json.loads(estado_path.read_text()) if estado_path.exists() else {}
            estado_path.write_text(json.dumps({**g, "avisado": k}))
        except (ValueError, OSError) as e:
            log.warning("Aviso do regime não gravado: %s", e)
