"""Compras na queda que continuam a cair: o MOTIVO da queda dava para saber no dia do sinal?

Pedido do Pestana (16/11): "deste sinal de compra porque desceu 10% mas as ações continuam a descer".
O que se viu nas posições a perder (ARCT, KRT, DNA, PAYS, ODD, WK, AFRM, TENB, CAI, PBYI): quase todas
caíram DEPOIS DE RESULTADOS (falharam vendas/lucro ou baixaram as previsões). Efeito conhecido
("post-earnings drift"): depois de resultados maus a ação tende a continuar a cair semanas.
O estudo da confirmação (passo3zc, 12/11) mostrou que esperar pela viragem não ajuda; aqui testa-se
outra coisa: NÃO comprar quando o motivo da queda é conhecido.

Variantes fixadas ANTES de correr (não se afinam depois). Mudam só as compras na queda (RESSALTOS);
rompimento e semana_forte ficam iguais:
  base            o modelo de hoje
  sem_resultados  não compra se a empresa apresentou resultados nos 6 dias úteis até ao sinal
                  (calendário da Nasdaq, grátis; o mesmo que o radar de resultados já usa)
  sem_choque      não compra se houve um choque nos últimos 5 dias: abriu -4% ou caiu -6% num dia com
                  volume >= 2x (indicadores.choque_baixa_5d; só preços, vale também para a Europa)
  setor_cai       não compra se o setor inteiro caiu mais de 5% em 5 dias (média das ações do setor)
  sozinha         não compra se a ação caiu 10 pontos mais do que o setor em 5 dias (motivo próprio da
                  empresa); não se aplica a contra_setor, que é exatamente isso
Saídas e custos como no modelo (passo3zc: fecho_10 ou sma5 com stop 2 ATR; líquido de 0,3%).
Critério: passa se a carteira de 5 lugares render mais nas DUAS metades e o retorno médio por trade dos
ressaltos for maior nas DUAS metades.

Parte 2 (ao vivo): ressaltos do registo em papel desde 29/10 com as mesmas marcas.
Uso: ANOS=10 ACOES=1500 python passo3zd_faca_motivo.py
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import yaml

import passo3p_regime as p3p
import passo3v_avancado as p3v
import passo3y_gestao as p3y
import passo3zc_confirmacao as p3zc
from src import contexto, regime, resultados, sinais

CUSTO = p3zc.CUSTO
RESSALTOS = p3zc.RESSALTOS
FILTROS = {"sem_resultados": "resultados", "sem_choque": "choque", "setor_cai": "setor_cai", "sozinha": "sozinha"}
VARIANTES = ["base", *FILTROS]
log = logging.getLogger(__name__)


def calendario(inicio: pd.Timestamp, fim: pd.Timestamp) -> pd.DataFrame:
    """Datas de resultados (calendário da Nasdaq dia a dia), com cache em data/resultados_hist.csv."""
    f = p3p.DADOS / "resultados_hist.csv"
    cache = pd.read_csv(f) if f.exists() else pd.DataFrame(columns=["data", "ticker", "quando"])
    feitos = set(cache["data"].astype(str))
    dias = [d for d in pd.bdate_range(inicio, fim) if f"{d:%Y-%m-%d}" not in feitos]

    def um(d):
        try:
            linhas = resultados.linhas_do_dia(resultados._pedir(d))
            return [{"data": f"{d:%Y-%m-%d}", "ticker": r["ticker"], "quando": r["quando"]} for r in linhas] or \
                [{"data": f"{d:%Y-%m-%d}", "ticker": "", "quando": ""}]
        except Exception as e:  # noqa: BLE001
            log.warning("Resultados de %s: %s", f"{d:%Y-%m-%d}", e)
            return []

    novos = []
    with ThreadPoolExecutor(6) as ex:
        for linhas in ex.map(um, dias):
            novos += linhas
    out = pd.concat([cache, pd.DataFrame(novos)], ignore_index=True)
    out.to_csv(f, index=False)
    out = out[out["ticker"].astype(str) != ""].copy()
    out["data"] = pd.to_datetime(out["data"])
    return out


def marcas(d: pd.DataFrame, i: int, datas_res: np.ndarray) -> dict:
    """Motivos da queda que se sabiam no fecho do dia i."""
    ini = d["data"].iat[max(0, i - 6)]
    res = bool(len(datas_res)) and bool(((datas_res >= ini) & (datas_res <= d["data"].iat[i])).any())
    rel = d["rel_setor5"].iat[i] if "rel_setor5" in d else np.nan
    setor5 = d["ret5"].iat[i] - rel if np.isfinite(rel) else np.nan
    return {"resultados": res, "choque": bool(d["choque_baixa_5d"].iat[i] > 0),
            "setor_cai": bool(np.isfinite(setor5) and setor5 < -0.05),
            "sozinha": bool(np.isfinite(rel) and rel < -0.10)}


def recolher(precos: pd.DataFrame, cfg: dict, cal: pd.DataFrame) -> pd.DataFrame:
    reg = cfg.get("regime") or {}
    merc, vix = p3zc.mercado(precos, float(reg.get("amplitude_min", 0.5)))
    extra = dict(tuple(contexto.tabela(precos, vix, contexto.setores(p3p.DADOS / "setores.csv")).groupby("ticker")))
    so_em = {**regime.SO_EM, **(reg.get("so_em") or {})}
    cap = set((reg.get("capitulacao") or {}).get("setups") or [])
    setups = [s for s in cfg["setups"] if sinais.SETUPS[s].direcao == "compra"]
    por_ticker = {t: np.sort(g["data"].to_numpy()) for t, g in cal.groupby("ticker")}
    linhas = []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        if len(df) < 260:
            continue
        d, a = p3zc.preparar(df, extra, ticker)
        flags = sinais.detetar(d)
        mk = d["data"].map(merc).to_numpy()
        dr = por_ticker.get(ticker, np.array([], dtype="datetime64[ns]"))
        for s in setups:
            vale = list(so_em.get(s) or ["forte"]) + (["queda"] if s in cap else [])
            filtros = cfg.get("qualidade", {}).get(s, {}).get("filtros")
            regra = p3zc.regra_saida(s, cfg)
            for i in np.flatnonzero(flags[s].to_numpy() & np.isin(mk, vale)):
                if i < 7 or (filtros and not sinais.passa_filtros(d.iloc[i], filtros)):
                    continue
                r = p3zc.simular(a, int(i), a["close"][i], regra, False)
                if r is None:
                    continue
                linhas.append({"ticker": ticker, "setup": s, "sinal_data": d["data"].iat[i],
                               "entrada_data": d["data"].iat[i], "saida_data": d["data"].iat[i + r[1]],
                               "ret": r[0] - CUSTO, "dias": r[1], "atr_pct": d["atr_pct"].iat[i], "mercado": mk[i],
                               **marcas(d, int(i), dr)})
    return pd.DataFrame(linhas)


def com_variante(t: pd.DataFrame, var: str) -> pd.DataFrame:
    if var == "base":
        return t
    m = t[FILTROS[var]] & t["setup"].isin(RESSALTOS)
    if var == "sozinha":
        m &= t["setup"] != "contra_setor"
    return t[~m]


def fmt(r: dict) -> str:
    return f"{r['total']*100:+7.1f}% (desc {r['descida']*100:+5.1f}%, {r['n']} trades)"


def estudo_10_anos(cfg: dict) -> None:
    precos = p3v.carregar()
    cal = calendario(precos["data"].min(), precos["data"].max())
    anos = cal.groupby(cal["data"].dt.year)["ticker"].count()
    print("Calendário de resultados (anúncios por ano): " + ", ".join(f"{a}: {n}" for a, n in anos.items()))
    t = recolher(precos, cfg, cal)
    meio = t["sinal_data"].median()
    t["metade"] = np.where(t["sinal_data"] < meio, 1, 2)
    t["chave"] = t["setup"]
    treino = t[t["metade"] == 1]
    media = treino.groupby("chave")["ret"].mean().to_dict()
    ganha = treino.groupby("chave")["ret"].apply(lambda r: (r > 0).mean()).to_dict()
    t = p3y.esperado(t, media, ganha)
    print(f"Trades: {len(t)}; metades a partir de {meio:%Y-%m-%d}")

    print("\n### Ressaltos com e sem cada motivo (média por trade líquida, 1.ª|2.ª metade, ganha, n)")
    r = t[t["setup"].isin(RESSALTOS)]
    for s in [*RESSALTOS, "TODOS"]:
        g0 = r if s == "TODOS" else r[r["setup"] == s]
        if g0.empty:
            continue
        print(f"  {s}")
        for nome, col in FILTROS.items():
            for v in (True, False):
                g = g0[g0[col] == v]
                if g.empty:
                    continue
                a, b = g[g["metade"] == 1]["ret"].mean(), g[g["metade"] == 2]["ret"].mean()
                print(f"    {col:11s} {'sim' if v else 'não'}  n={len(g):6d} ({len(g)/len(g0)*100:3.0f}%) "
                      f"{g['ret'].mean()*100:+.2f}% ({a*100:+.2f}|{b*100:+.2f}) ganha {(g['ret'] > 0).mean()*100:.0f}% "
                      f"perde >5%: {(g['ret'] < -0.05).mean()*100:.0f}%")

    print("\n### Todos os ressaltos (média por trade, 1.ª|2.ª metade)")
    res = {}
    for var in VARIANTES:
        g = com_variante(t, var)
        g = g[g["setup"].isin(RESSALTOS)]
        res[var] = (g[g["metade"] == 1]["ret"].mean(), g[g["metade"] == 2]["ret"].mean())
        print(f"  {var:15s} n={len(g):7d} {g['ret'].mean()*100:+.2f}% ({res[var][0]*100:+.2f}|{res[var][1]*100:+.2f}) "
              f"ganha {(g['ret'] > 0).mean()*100:.0f}%")

    print("\n### Carteira de 5 lugares (modelo inteiro; só os ressaltos mudam)")
    cart = {var: {pn: p3y.carteira(com_variante(t[t["metade"] == pn], var)) for pn in (1, 2)} for var in VARIANTES}
    for var in VARIANTES:
        ok = var != "base" and all(cart[var][pn]["total"] > cart["base"][pn]["total"] and
                                   res[var][pn - 1] > res["base"][pn - 1] for pn in (1, 2))
        print(f"  {var:15s} " + " | ".join(f"{pn}.ª {fmt(cart[var][pn])}" for pn in (1, 2))
              + ("" if var == "base" else f"  -> {'PASSA' if ok else 'não passa'}"))


def ao_vivo(cfg: dict) -> None:
    try:
        precos = pd.read_parquet(p3p.DADOS / "precos.parquet")
    except Exception as e:  # noqa: BLE001
        print(f"\n(sem preços guardados: {e})")
        return
    precos["data"] = p3p._sem_fuso(precos["data"])
    h = pd.read_csv(p3p.DADOS / "historico_sinais.csv")
    h = h[(h["direcao"] == "compra") & (h["data_sinal"] >= "2026-10-29") & h["setup"].isin(RESSALTOS)]
    h = h.assign(r=h["preco_fecho"] / h["entrada"] - 1)
    cal = calendario(pd.Timestamp("2026-10-15"), precos["data"].max())
    por_ticker = {t: np.sort(g["data"].to_numpy()) for t, g in cal.groupby("ticker")}
    sec = contexto.setores(p3p.DADOS / "setores.csv")
    extra = dict(tuple(contexto.tabela(precos, None, sec).groupby("ticker")))
    ultimo = precos.sort_values("data").groupby("ticker")["close"].last()
    linhas = []
    for ticker, df in precos[precos["ticker"].isin(set(h["ticker"]))].sort_values("data").groupby("ticker"):
        if len(df) < 60:
            continue
        d, _ = p3zc.preparar(df, extra, ticker)
        datas = d["data"].dt.strftime("%Y-%m-%d").to_numpy()
        for x in h[h["ticker"] == ticker].itertuples():
            idx = np.flatnonzero(datas == x.data_sinal)
            if not len(idx) or idx[0] < 7:
                continue
            real = x.r if x.estado != "aberto" else ultimo.get(ticker, np.nan) / x.entrada - 1
            linhas.append({"ticker": ticker, "setup": x.setup, "data": x.data_sinal, "estado": x.estado, "real": real,
                           **marcas(d, int(idx[0]), por_ticker.get(ticker, np.array([], dtype="datetime64[ns]")))})
    if not linhas:
        print("\n(sem ressaltos ao vivo com preços)")
        return
    v = pd.DataFrame(linhas)
    print(f"\n### Ao vivo: {len(v)} compras na queda desde 29/10 (abertos ao último preço guardado, sem custos)")
    for col in FILTROS.values():
        for k, g in v.groupby(v[col]):
            print(f"  {col:11s} {'sim' if k else 'não'}  n={len(g):3d} média {g['real'].mean()*100:+.2f}% "
                  f"ganha {(g['real'] > 0).mean()*100:.0f}%")
    print("  Piores 15:")
    print(v.sort_values("real").head(15).to_string(index=False))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load((p3v.RAIZ / "config.yaml").read_text())["sinais"]
    ao_vivo(cfg)
    estudo_10_anos(cfg)


if __name__ == "__main__":
    main()
