"""Comprar ações a cair cedo demais ("apanhar a faca a cair")? Confirmação antes de entrar nos ressaltos.

Pedido do Pestana (12/11): "parece que às vezes apanhas uma ação a descer e achas que vai dar para cima,
mas na verdade ainda não acabou de descer".
Setups de compra na queda (ressaltos): pullback, caiu_10, reversao_queda, medo_vix, viragem_mes,
contra_setor, lider_recuo. Rompimento e semana_forte ficam como estão em todas as variantes.

Variantes fixadas ANTES de correr (não se afinam depois):
  base        compra no fecho do dia do sinal (o modelo de hoje)
  espera1     compra no fecho do dia seguinte, sem condição
  verde       compra no 1.º fecho a subir (fecho > fecho anterior) nos 3 dias seguintes; senão não compra
  fecho_max   compra no 1.º fecho acima do máximo do dia anterior nos 3 dias seguintes; senão não compra
  rompe_max   ordem de compra no máximo do dia anterior (stop de compra, dá para fazer durante o dia):
              compra quando o preço passa o máximo do dia anterior nos 3 dias seguintes, ao preço
              max(abertura, máximo de ontem)
  vela_hoje   só compra se o dia do sinal fechar na metade de cima do dia (vela de viragem); fecho do sinal
  sem_faca    não compra se o dia do sinal cair mais do que 1 ATR (ainda a cair com força); fecho do sinal
Saídas: as do modelo (fecho_10 em pullback/lider_recuo/tendências, sma5 com stop 2 ATR e 5 dias nos outros),
contadas a partir do dia da compra, com o ATR do dia anterior à compra. Líquido de 0,3%.
Critério: passa se a carteira de 5 lugares (ordem equilibrada, sem trocas) render mais nas DUAS metades e o
retorno médio por trade dos ressaltos for maior nas DUAS metades.

Parte 2 (ao vivo): os ressaltos do registo em papel desde 29/10 (data/historico_sinais.csv) com os preços
guardados (data/precos.parquet): quanto caíram ainda depois do sinal e o que cada variante teria feito.
Só mede; nada muda no modelo.

Resultado 12/11 (10 anos, 1500 ações; metades a partir de 2022-04-12): NADA passa. Ressaltos juntos por trade:
base +0,56|+0,68; espera1 +0,32|+0,52; verde -0,02|+0,19; fecho_max -0,08|-0,24; rompe_max +0,17|+0,10;
vela_hoje +0,74|+0,53; sem_faca +0,76|+0,75 (melhor por trade mas a carteira cai: +178|+173% contra +306|+181%).
Carteira 5 lugares: base +306%|+181% (desc -16|-23%); todas as variantes pior em pelo menos uma metade.
Esperar pela confirmação compra mais caro e perde a parte boa do ressalto. Os 10% que caem 3 dias seguidos
depois do sinal dão -4,95%, mas não se distinguem antes. Ao vivo (134 ressaltos desde 29/10): todas negativas,
base -1,16%, fecho_max -0,67% (só 56 compras), sem_faca -2,40%; amostra pequena e mercado a cair.
Uso: ANOS=10 ACOES=1500 python passo3zc_confirmacao.py
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import yaml

import passo3p_regime as p3p
import passo3v_avancado as p3v
import passo3y_gestao as p3y
from src import contexto, indicadores, regime, sinais

CUSTO = 0.003
RESSALTOS = ["pullback", "caiu_10", "reversao_queda", "medo_vix", "viragem_mes", "contra_setor", "lider_recuo"]
VARIANTES = ["base", "espera1", "verde", "fecho_max", "rompe_max", "vela_hoje", "sem_faca"]
JANELA = 3


def regra_saida(setup: str, cfg: dict) -> tuple:
    s = (cfg.get("qualidade", {}).get(setup, {}) or {}).get("saida")
    return p3y.SAIDAS["fecho_10"] if s == "fecho_10" else p3y.SAIDAS["sma5_5"]


def entrada(a: dict, i: int, var: str) -> tuple[int, float] | None:
    """(dia da compra, preço) ou None se a variante não compra."""
    o, h, c = a["open"], a["high"], a["close"]
    n = len(c)
    if var in ("base", "vela_hoje", "sem_faca"):
        if var == "vela_hoje" and a["pos_fecho"][i] < 0.5:
            return None
        if var == "sem_faca" and a["ret_dia"][i] < -a["atr_pct"][i - 1]:
            return None
        return i, c[i]
    if var == "espera1":
        return (i + 1, c[i + 1]) if i + 1 < n else None
    for j in range(i + 1, min(i + JANELA, n - 1) + 1):
        if var == "verde" and c[j] > c[j - 1]:
            return j, c[j]
        if var == "fecho_max" and c[j] > h[j - 1]:
            return j, c[j]
        if var == "rompe_max" and h[j] > h[j - 1]:
            return j, max(o[j], h[j - 1])
    return None


def simular(a: dict, j: int, e: float, regra: tuple, no_dia: bool) -> tuple[float, int] | None:
    """Trade comprado no dia j ao preço e. no_dia: comprado durante o dia (o fecho de j já conta para a
    saída pela média de 5 dias; o mínimo de j não, porque pode ter sido antes da compra)."""
    obj_k, stop_k, dias, cond, _ = regra
    o, h, lo, c = a["open"], a["high"], a["low"], a["close"]
    atr = a["atr14"][j - 1] if no_dia else a["atr14"][j]
    if j + dias >= len(c) or not np.isfinite(atr):
        return None
    stop = e - stop_k * atr if stop_k else None
    if no_dia and cond == "sma5" and c[j] > a["sma5"][j]:
        return c[j] / e - 1, 0
    for k in range(j + 1, j + dias + 1):
        if stop is not None and lo[k] <= stop:
            return min(stop, o[k]) / e - 1, k - j
        if cond == "sma5" and c[k] > a["sma5"][k]:
            return c[k] / e - 1, k - j
    return c[j + dias] / e - 1, dias


def mercado(precos: pd.DataFrame, amp_min: float) -> pd.Series:
    m = p3p.indices(pd.to_datetime(precos["data"]).min())
    p = precos.sort_values(["ticker", "data"])
    sma50 = p.groupby("ticker")["close"].transform(lambda x: x.rolling(50).mean())
    amp = (p["close"] > sma50).where(sma50.notna()).groupby(p["data"]).mean()
    spy5 = m["spy_ret5"] if not m.empty else pd.Series(dtype=float)
    return pd.Series(np.where(spy5.reindex(amp.index) < -0.03, "queda", np.where(amp >= amp_min, "forte", "fraco")),
                     index=amp.index), (m["vix"] if not m.empty else pd.Series(dtype=float))


def preparar(df: pd.DataFrame, extra: dict, ticker: str) -> tuple[pd.DataFrame, dict]:
    d = indicadores.calcular(df.reset_index(drop=True))
    if ticker in extra:
        contexto.juntar(d, extra[ticker])
    a = {k: d[k].to_numpy(float) for k in ("open", "high", "low", "close", "atr14", "sma5", "rsi2",
                                           "pos_fecho", "ret_dia", "atr_pct")}
    return d, a


def recolher(precos: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    reg = cfg.get("regime") or {}
    merc, vix = mercado(precos, float(reg.get("amplitude_min", 0.5)))
    extra = dict(tuple(contexto.tabela(precos, vix, contexto.setores(p3p.DADOS / "setores.csv")).groupby("ticker")))
    so_em = {**regime.SO_EM, **(reg.get("so_em") or {})}
    cap = set((reg.get("capitulacao") or {}).get("setups") or [])
    setups = [s for s in cfg["setups"] if sinais.SETUPS[s].direcao == "compra"]
    linhas = []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        if len(df) < 260:
            continue
        d, a = preparar(df, extra, ticker)
        flags = sinais.detetar(d)
        mk = d["data"].map(merc).to_numpy()
        for s in setups:
            vale = list(so_em.get(s) or ["forte"]) + (["queda"] if s in cap else [])
            filtros = cfg.get("qualidade", {}).get(s, {}).get("filtros")
            regra = regra_saida(s, cfg)
            variantes = VARIANTES if s in RESSALTOS else ["base"]
            for i in np.flatnonzero(flags[s].to_numpy() & np.isin(mk, vale)):
                if i < 1 or (filtros and not sinais.passa_filtros(d.iloc[i], filtros)):
                    continue
                for var in variantes:
                    en = entrada(a, int(i), var)
                    if en is None:
                        continue
                    j, e = en
                    r = simular(a, j, e, regra, var == "rompe_max")
                    if r is None:
                        continue
                    linhas.append((ticker, s, var, d["data"].iat[i], d["data"].iat[j], d["data"].iat[j + r[1]],
                                   r[0] - CUSTO, r[1] + (j - i), d["atr_pct"].iat[i], mk[i]))
    return pd.DataFrame(linhas, columns=["ticker", "setup", "variante", "sinal_data", "entrada_data", "saida_data",
                                         "ret", "dias", "atr_pct", "mercado"])


def com_variante(t: pd.DataFrame, var: str) -> pd.DataFrame:
    """Trades do modelo com a variante `var` nos ressaltos e as tendências como hoje."""
    return t[(t["variante"] == var) | (~t["setup"].isin(RESSALTOS) & (t["variante"] == "base"))]


def fmt(r: dict) -> str:
    return f"{r['total']*100:+7.1f}% (desc {r['descida']*100:+5.1f}%, {r['n']} trades)"


def estudo_10_anos(cfg: dict) -> None:
    precos = p3v.carregar()
    t = recolher(precos, cfg)
    meio = t["sinal_data"].median()
    t["metade"] = np.where(t["sinal_data"] < meio, 1, 2)
    t["chave"] = t["setup"] + "|" + t["variante"]
    treino = t[t["metade"] == 1]
    media = treino.groupby("chave")["ret"].mean().to_dict()
    ganha = treino.groupby("chave")["ret"].apply(lambda r: (r > 0).mean()).to_dict()
    for k in t["chave"].unique():
        media.setdefault(k, 0.0)
        ganha.setdefault(k, 0.5)
    t = p3y.esperado(t, media, ganha)
    print(f"Trades (setup x variante): {len(t)}; metades a partir de {meio:%Y-%m-%d}")

    print("\n### Ressaltos por setup e variante (líquido; 1.ª|2.ª metade; ganha; quantos sinais compram)")
    for s in RESSALTOS:
        g0 = t[(t["setup"] == s) & (t["variante"] == "base")]
        if g0.empty:
            continue
        for var in VARIANTES:
            g = t[(t["setup"] == s) & (t["variante"] == var)]
            if g.empty:
                continue
            a, b = g[g["metade"] == 1]["ret"].mean(), g[g["metade"] == 2]["ret"].mean()
            print(f"  {s:15s} {var:10s} n={len(g):6d} ({len(g)/len(g0)*100:3.0f}%) {g['ret'].mean()*100:+.2f}% "
                  f"({a*100:+.2f}|{b*100:+.2f}) ganha {(g['ret'] > 0).mean()*100:.0f}% {g['dias'].mean():.1f} dias")

    print("\n### Todos os ressaltos juntos (média por trade, 1.ª|2.ª metade)")
    res = {}
    for var in VARIANTES:
        g = t[t["setup"].isin(RESSALTOS) & (t["variante"] == var)]
        res[var] = (g[g["metade"] == 1]["ret"].mean(), g[g["metade"] == 2]["ret"].mean())
        print(f"  {var:10s} n={len(g):7d} {g['ret'].mean()*100:+.2f}% ({res[var][0]*100:+.2f}|{res[var][1]*100:+.2f}) "
              f"ganha {(g['ret'] > 0).mean()*100:.0f}%")

    print("\n### Carteira de 5 lugares (modelo inteiro; só os ressaltos mudam)")
    cart = {var: {pn: p3y.carteira(com_variante(t[t["metade"] == pn], var)) for pn in (1, 2)} for var in VARIANTES}
    for var in VARIANTES:
        ok = var != "base" and all(cart[var][pn]["total"] > cart["base"][pn]["total"] and
                                   res[var][pn - 1] > res["base"][pn - 1] for pn in (1, 2))
        print(f"  {var:10s} " + " | ".join(f"{pn}.ª {fmt(cart[var][pn])}" for pn in (1, 2))
              + ("" if var == "base" else f"  -> {'PASSA' if ok else 'não passa'}"))

    # Faca a cair: os ressaltos do modelo de hoje que ainda caíram depois do sinal
    print("\n### Diagnóstico (base): ressaltos pelo dia seguinte ao sinal")
    b = t[t["setup"].isin(RESSALTOS) & (t["variante"] == "base")]
    v = t[t["setup"].isin(RESSALTOS) & (t["variante"] == "verde")][["ticker", "setup", "sinal_data"]]
    b = b.merge(v.assign(confirmou=True), on=["ticker", "setup", "sinal_data"], how="left")
    b["confirmou"] = b["confirmou"].fillna(False).astype(bool)
    for k, g in b.groupby("confirmou"):
        print(f"  {'subiu num dos 3 dias seguintes' if k else 'caiu 3 dias seguidos depois'}: n={len(g)} "
              f"{g['ret'].mean()*100:+.2f}% ganha {(g['ret'] > 0).mean()*100:.0f}%")


def ao_vivo(cfg: dict) -> None:
    """Ressaltos do registo em papel desde 29/10 com os preços guardados pelos sinais."""
    try:
        precos = pd.read_parquet(p3p.DADOS / "precos.parquet")
    except Exception as e:  # noqa: BLE001
        print(f"\n(sem preços guardados: {e})")
        return
    precos["data"] = p3p._sem_fuso(precos["data"])
    h = pd.read_csv(p3p.DADOS / "historico_sinais.csv")
    h = h[(h["direcao"] == "compra") & (h["data_sinal"] >= "2026-10-29")]
    h = h.assign(r=h["preco_fecho"] / h["entrada"] - 1)
    print("\n### Ao vivo: registo em papel desde 29/10, fechados (retorno real, sem custos)")
    f = h[h["estado"] != "aberto"]
    for s, g in f.groupby("setup"):
        print(f"  {s:15s} n={len(g):4d} {g['r'].mean()*100:+.2f}% ganha {(g['r'] > 0).mean()*100:.0f}%")
    tick = set(h.loc[h["setup"].isin(RESSALTOS), "ticker"])
    linhas = []
    for ticker, df in precos[precos["ticker"].isin(tick)].sort_values("data").groupby("ticker"):
        if len(df) < 60:
            continue
        d, a = preparar(df, {}, ticker)
        datas = d["data"].dt.strftime("%Y-%m-%d").to_numpy()
        for x in h[(h["ticker"] == ticker) & h["setup"].isin(RESSALTOS)].itertuples():
            idx = np.flatnonzero(datas == x.data_sinal)
            if not len(idx) or idx[0] + 1 >= len(datas):
                continue
            i = int(idx[0])
            fim = min(i + 3, len(datas) - 1)
            atr = a["atr14"][i]
            linha = {"ticker": ticker, "setup": x.setup, "data": x.data_sinal, "estado": x.estado, "real": x.r,
                     "dia1": a["close"][i + 1] / a["close"][i] - 1,
                     "caiu_mais_atr": (a["close"][i] - a["low"][i + 1:fim + 1].min()) / atr if atr > 0 else np.nan}
            regra = regra_saida(x.setup, cfg)
            for var in VARIANTES:
                en = entrada(a, i, var) if i >= 1 else None
                r = simular(a, en[0], en[1], regra, var == "rompe_max") if en else None
                if en is not None and r is None:   # ainda a decorrer: valor de hoje
                    r = (a["close"][-1] / en[1] - 1, 0)
                linha[var] = (r[0] - CUSTO) if r else np.nan
            linhas.append(linha)
    if not linhas:
        print("  (sem ressaltos com preços)")
        return
    v = pd.DataFrame(linhas)
    print(f"\n  Ressaltos ao vivo com preços: {len(v)}; dia seguinte a cair: {(v['dia1'] < 0).mean()*100:.0f}% "
          f"(média {v['dia1'].mean()*100:+.2f}%); caíram mais de 1 ATR abaixo da entrada nos 3 dias seguintes: "
          f"{(v['caiu_mais_atr'] > 1).mean()*100:.0f}%")
    print("  Simulação com os mesmos preços (líquido de 0,3%; abertos contam ao preço de hoje):")
    for var in VARIANTES:
        x = v[var].dropna()
        print(f"    {var:10s} compra {len(x):4d}/{len(v)} média {x.mean()*100:+.2f}% ganha {(x > 0).mean()*100:.0f}% "
              f"| soma {x.sum()*100:+.1f} pts")
    for s, g in v.groupby("setup"):
        print(f"    {s:15s} n={len(g):3d} " + " ".join(f"{var} {g[var].mean()*100:+.2f}%" for var in VARIANTES))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load((p3v.RAIZ / "config.yaml").read_text())["sinais"]
    ao_vivo(cfg)
    estudo_10_anos(cfg)


if __name__ == "__main__":
    main()
