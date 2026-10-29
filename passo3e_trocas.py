"""Vale a pena trocar uma posição aberta por um sinal novo? Estudo com 2 anos de histórico.

1. "Valor que falta": para cada trade e cada dia em que ainda está aberto, quanto rendeu do fecho
   desse dia até à saída. Média por setup, dias já passados e ganho atual (em ATR). É o que uma
   posição igual ainda costuma dar.
2. Simula uma carteira de N lugares no período de teste (últimos 40%), com as tabelas aprendidas
   nos primeiros 60%, e compara três políticas:
     sem_trocas   carteira cheia = o sinal novo fica de fora
     regra_bot    a regra atual do bot: troca se confiança nova >= força da mais fraca + 15,
                  com força = confiança x (dias que faltam / prazo)
     valor        troca se o retorno esperado do sinal novo > valor que falta à mais fraca
                  + custo da troca (spread) + margem
   Os sinais candidatos imitam os alertas: um por ação e dia, retorno esperado >= fasquia,
   calculado só com trades da mesma ação já fechados antes (sem olhar para o futuro).
Uso:  python passo3e_trocas.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src import indicadores, saidas, sinais

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RAIZ = Path(__file__).parent
DADOS = RAIZ / "data"
CUSTO = 0.003      # spread aproximado da XTB, ida e volta
MARGEM = 0.002     # folga extra exigida numa troca
BALDES = [-np.inf, -1.0, -0.25, 0.25, 1.0, 2.0, np.inf]   # ganho atual em ATR


def recolher(precos: pd.DataFrame, cfg: dict) -> list[dict]:
    setups = [s for s in cfg["setups"] if sinais.SETUPS[s].direcao == "compra"]
    out = []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        flags = sinais.detetar(d)
        for nome in setups:
            regra = saidas.regra_do_setup(nome, cfg)
            filtros = cfg.get("qualidade", {}).get(nome, {}).get("filtros")
            for i in np.flatnonzero(flags[nome].to_numpy()):
                if not sinais.passa_filtros(d.iloc[i], filtros):
                    continue
                r = saidas.simular(d, i, regra)
                if r is None:
                    continue
                ret, dias, _ = r
                entrada = d["close"].iat[i]
                caminho = {d["data"].iat[i + k]: d["close"].iat[i + k] for k in range(0, dias)}
                out.append({"ticker": ticker, "setup": nome, "entrada_data": d["data"].iat[i],
                            "saida_data": d["data"].iat[i + dias], "entrada": entrada, "ret": ret,
                            "atr_pct": d["atr_pct"].iat[i], "max_dias": regra.max_dias, "caminho": caminho})
    return out


def balde(ganho: float, atr_pct: float) -> int:
    return int(np.digitize(ganho / max(atr_pct, 1e-4), BALDES[1:-1]))


def tabelas(trades: list[dict]) -> tuple[dict, dict, dict]:
    """(valor que falta por setup/dia/balde, retorno médio por setup, % a ganhar por setup)."""
    resto = defaultdict(list)
    for t in trades:
        fim = t["entrada"] * (1 + t["ret"])
        for k, (dia, preco) in enumerate(sorted(t["caminho"].items())):
            if k == 0:
                continue
            g = preco / t["entrada"] - 1
            resto[(t["setup"], min(k, 5), balde(g, t["atr_pct"]))].append(fim / preco - 1)
    valor = {k: float(np.mean(v)) for k, v in resto.items() if len(v) >= 30}
    df = pd.DataFrame([{"setup": t["setup"], "ret": t["ret"]} for t in trades])
    media = df.groupby("setup")["ret"].mean().to_dict()
    ganha = df.groupby("setup")["ret"].apply(lambda r: (r > 0).mean()).to_dict()
    return valor, media, ganha


def candidatos(trades: list[dict], base: dict, fasquia: float, k: int = 10) -> dict:
    """Por dia, os sinais que passariam a fasquia com a informação desse dia (histórico da ação)."""
    por_acao = defaultdict(list)   # (ticker, setup) -> [(saida_data, ret)] já ordenado por entrada
    por_dia = defaultdict(list)
    for t in sorted(trades, key=lambda x: x["entrada_data"]):
        hist = [r for s, r in por_acao[(t["ticker"], t["setup"])] if s < t["entrada_data"]]
        n = len(hist)
        esperado = (np.sum(hist) + base[t["setup"]] * k) / (n + k)
        if esperado >= fasquia:
            por_dia[t["entrada_data"]].append({**t, "esperado": esperado})
        por_acao[(t["ticker"], t["setup"])].append((t["saida_data"], t["ret"]))
    for d in por_dia:   # um por ação e dia, o de maior retorno esperado
        vistos, lista = set(), []
        for c in sorted(por_dia[d], key=lambda x: -x["esperado"]):
            if c["ticker"] not in vistos:
                vistos.add(c["ticker"])
                lista.append(c)
        por_dia[d] = lista
    return por_dia


def simular(por_dia: dict, datas: list, lugares: int, politica: str, valor: dict, media: dict,
            ganha: dict) -> dict:
    abertas, fechados, trocas = {}, [], 0

    def falta(p, d):
        k = sum(1 for x in p["caminho"] if x <= d) - 1
        g = p["caminho"][d] / p["entrada"] - 1
        return valor.get((p["setup"], min(max(k, 1), 5), balde(g, p["atr_pct"])), media[p["setup"]]), k

    for d in datas:
        for tk in [t for t, p in abertas.items() if p["saida_data"] == d]:
            fechados.append(abertas.pop(tk)["ret"] - CUSTO)
        for c in por_dia.get(d, []):
            if c["ticker"] in abertas:
                continue
            if len(abertas) < lugares:
                abertas[c["ticker"]] = c
                continue
            if politica == "sem_trocas":
                break
            vivas = [p for p in abertas.values() if d in p["caminho"] and p["entrada_data"] < d]
            if not vivas:
                break
            if politica == "regra_bot":
                def forca(p):
                    k = sum(1 for x in p["caminho"] if x <= d) - 1
                    return 100 * ganha[p["setup"]] * max(p["max_dias"] - k, 0) / p["max_dias"]
                fraca = min(vivas, key=forca)
                trocar = 100 * ganha[c["setup"]] >= forca(fraca) + 15
            else:
                fraca = min(vivas, key=lambda p: falta(p, d)[0])
                trocar = c["esperado"] - falta(fraca, d)[0] > CUSTO + MARGEM
            if not trocar:
                break   # os candidatos estão por ordem: se o melhor não troca, os outros também não
            fechados.append(fraca["caminho"][d] / fraca["entrada"] - 1 - CUSTO)
            abertas.pop(fraca["ticker"])
            abertas[c["ticker"]] = c
            trocas += 1
    r = np.array(fechados)
    return {"trades": len(r), "trocas": trocas, "medio": r.mean() if len(r) else np.nan,
            "total": r.sum() / lugares, "ganha": (r > 0).mean() if len(r) else np.nan}


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    trades = recolher(precos[precos["ticker"].isin(filtrado["ticker"])], cfg)
    corte = pd.Series([t["entrada_data"] for t in trades]).quantile(0.6)
    treino = [t for t in trades if t["entrada_data"] < corte]
    valor, media, ganha = tabelas(treino)
    print(f"Trades: {len(trades)} (treino até {corte:%Y-%m-%d}: {len(treino)})")
    print("\n### Valor que falta (média do resto do trade), por setup, dias já passados e ganho atual em ATR")
    nomes = ["< -1", "-1..-0,25", "~0", "0,25..1", "1..2", "> 2"]
    for s in sorted(media):
        print(f"{s}: retorno médio do trade inteiro {media[s]*100:+.2f}%, {ganha[s]*100:.0f}% a ganhar")
        for k in range(1, 6):
            cel = [f"{nomes[b]}: {valor[(s, k, b)]*100:+.2f}%" for b in range(6) if (s, k, b) in valor]
            if cel:
                print(f"  dia {k}: " + " | ".join(cel))
    teste = [t for t in trades if t["entrada_data"] >= corte]
    datas = sorted({d for t in teste for d in t["caminho"]} | {t["saida_data"] for t in teste})
    print("\n### Carteira simulada no período de teste (custo de 0,3% por trade já descontado)")
    for fasquia in (cfg["retorno_minimo_pct"] / 100, 0.005):
        por_dia = candidatos(trades, media, fasquia)
        por_dia = {d: v for d, v in por_dia.items() if d >= corte}
        n = sum(len(v) for v in por_dia.values())
        print(f"\nFasquia {fasquia*100:.1f}%: {n} sinais candidatos em {len(por_dia)} dias")
        for lugares in (5, 10):
            for pol in ("sem_trocas", "regra_bot", "valor"):
                r = simular(por_dia, datas, lugares, pol, valor, media, ganha)
                print(f"  {lugares:2d} lugares | {pol:10s} | total {r['total']*100:+6.1f}% | {r['trades']} trades, "
                      f"{r['trocas']} trocas | {r['medio']*100:+.2f}%/trade | ganha {r['ganha']*100:.0f}%")


if __name__ == "__main__":
    main()
