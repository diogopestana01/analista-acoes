"""Gestão da carteira com as regras de hoje: prazo e saída por tipo de sinal, tamanho das posições,
número de lugares e reinvestimento. 10 anos, 1500 ações dos EUA.

Pedido do Pestana (6/11): "quero agora é uma melhor gestão do portfólio... há trades que devem acontecer
no mesmo dia e há trades que se calhar demoram 1 ou 2 semanas".
As entradas são as do modelo de hoje (src/sinais.detetar, filtros da config, cada setup só no seu mercado
como em sinais.regime.so_em; travão fora). Compra no fecho do sinal; líquido de 0,3%.
Saídas testadas (todas com stop, exceto as de 1 dia):
  abertura_1   vende na abertura seguinte (só a noite)        fecho_1      vende no fecho seguinte
  sma5_5       acima da média de 5 dias, stop 2 ATR, 5 dias   sma5_10      o mesmo até 10 dias
  rsi2_5       RSI2 > 70, stop 2 ATR, 5 dias                  atr_2_1      objetivo 2 ATR, stop 1 ATR, 5 dias
  atr_3_15     objetivo 3 ATR, stop 1,5 ATR, 5 dias           atr_3_15_10  o mesmo até 10 dias
  segue_2_10   stop que sobe 2 ATR abaixo do fecho mais alto, 10 dias
  segue_3_15   stop que sobe 3 ATR abaixo do fecho mais alto, 15 dias
  fecho_10     fica 10 dias com stop 2 ATR
Para não escolher à sorte: a saída de cada setup é escolhida só com a 1.ª metade (a que dá mais à
carteira de 5 lugares, um setup de cada vez) e a carteira inteira é medida nas duas metades.
Depois, com as saídas escolhidas: tamanho igual / pelo risco (menos dinheiro nas mais voláteis) / pela
ordem (mais no 1.º do dia), 3/5/8 lugares, e ganhos reinvestidos ou não. Só mede.
Uso: ANOS=10 ACOES=1500 python passo3y_gestao.py
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import yaml

import passo3p_regime as p3p
import passo3v_avancado as p3v
from src import contexto, indicadores, regime, sinais

CUSTO = 0.003
SAIDAS = {  # nome: (objetivo ATR, stop ATR, dias, condição, stop que sobe ATR)
    "abertura_1": (None, None, 1, "abertura", None), "fecho_1": (None, None, 1, None, None),
    "sma5_5": (None, 2.0, 5, "sma5", None), "sma5_10": (None, 2.0, 10, "sma5", None),
    "rsi2_5": (None, 2.0, 5, "rsi2", None), "atr_2_1": (2.0, 1.0, 5, None, None),
    "atr_3_15": (3.0, 1.5, 5, None, None), "atr_3_15_10": (3.0, 1.5, 10, None, None),
    "segue_2_10": (None, 2.0, 10, None, 2.0), "segue_3_15": (None, 3.0, 15, None, 3.0),
    "fecho_10": (None, 2.0, 10, None, None),
}
HOJE = {"rompimento": "atr_3_15", "semana_forte": "atr_3_15"}   # o resto: sma5_5


def simular(a: dict, i: int, s: tuple) -> tuple[float, int] | None:
    obj_k, stop_k, dias, cond, segue = s
    o, h, lo, c, atr = a["open"], a["high"], a["low"], a["close"], a["atr14"]
    if i + dias >= len(c) or not np.isfinite(atr[i]):
        return None
    e = c[i]
    if cond == "abertura":
        return o[i + 1] / e - 1, 1
    obj = e + obj_k * atr[i] if obj_k else None
    stop = e - stop_k * atr[i] if stop_k else None
    topo = e
    for j in range(i + 1, i + dias + 1):
        if stop is not None and lo[j] <= stop:
            return min(stop, o[j]) / e - 1, j - i
        if obj is not None and h[j] >= obj:
            return max(obj, o[j]) / e - 1, j - i
        if cond == "sma5" and c[j] > a["sma5"][j]:
            return c[j] / e - 1, j - i
        if cond == "rsi2" and a["rsi2"][j] > 70:
            return c[j] / e - 1, j - i
        if segue:
            topo = max(topo, c[j])
            stop = max(stop, topo - segue * atr[i])
    return c[i + dias] / e - 1, dias


def recolher(precos: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    m = p3p.indices(pd.to_datetime(precos["data"]).min())
    # Mercado por dia: amplitude (fecho > média de 50 dias) e SPY 5 dias
    p = precos.sort_values(["ticker", "data"])
    sma50 = p.groupby("ticker")["close"].transform(lambda x: x.rolling(50).mean())
    amp = (p["close"] > sma50).where(sma50.notna()).groupby(p["data"]).mean()
    spy5 = m["spy_ret5"] if not m.empty else pd.Series(dtype=float)
    merc = pd.Series(np.where(spy5.reindex(amp.index) < -0.03, "queda", np.where(amp >= 0.5, "forte", "fraco")),
                     index=amp.index)
    vix = m["vix"] if not m.empty else pd.Series(dtype=float)
    extra = dict(tuple(contexto.tabela(precos, vix, contexto.setores(p3p.DADOS / "setores.csv")).groupby("ticker")))
    so_em = {**regime.SO_EM, **((cfg.get("regime") or {}).get("so_em") or {})}
    cap = set(((cfg.get("regime") or {}).get("capitulacao") or {}).get("setups") or [])
    setups = [s for s in cfg["setups"] if sinais.SETUPS[s].direcao == "compra"]
    linhas = []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 260:
            continue
        d = indicadores.calcular(df)
        if ticker in extra:
            contexto.juntar(d, extra[ticker])
        flags = sinais.detetar(d)
        mk = d["data"].map(merc).to_numpy()
        a = {k: d[k].to_numpy(float) for k in ("open", "high", "low", "close", "atr14", "sma5", "rsi2")}
        for s in setups:
            vale = so_em.get(s) or ["forte"]
            if s in cap:
                vale = list(vale) + ["queda"]
            filtros = cfg.get("qualidade", {}).get(s, {}).get("filtros")
            for i in np.flatnonzero(flags[s].to_numpy() & np.isin(mk, vale)):
                if filtros and not sinais.passa_filtros(d.iloc[i], filtros):
                    continue
                for nome, regra in SAIDAS.items():
                    r = simular(a, int(i), regra)
                    if r is not None:
                        linhas.append((ticker, s, nome, d["data"].iat[i], d["data"].iat[i + r[1]], r[0] - CUSTO,
                                       r[1], d["atr_pct"].iat[i], mk[i]))
    return pd.DataFrame(linhas, columns=["ticker", "setup", "saida", "entrada_data", "saida_data", "ret", "dias",
                                         "atr_pct", "mercado"])


def esperado(t: pd.DataFrame, media: dict, ganha: dict, k: int = 10) -> pd.DataFrame:
    """Como passo3j.medidas (retorno esperado e prob. de ganhar com o histórico da própria ação e setup,
    só trades já fechados antes da entrada, encolhidos para a média do setup), mas vetorizado."""
    ex = t.sort_values("saida_data")[["chave", "ticker", "saida_data", "ret"]].copy()
    g = ex.groupby(["chave", "ticker"])
    ex["cs"], ex["cw"], ex["cn"] = g["ret"].cumsum(), (ex["ret"] > 0).groupby([ex["chave"], ex["ticker"]]).cumsum(), g.cumcount() + 1
    ex["grupo"] = ex["chave"] + "#" + ex["ticker"]
    x = t.assign(grupo=t["chave"] + "#" + t["ticker"], _i=np.arange(len(t))).sort_values("entrada_data")
    x = pd.merge_asof(x, ex[["grupo", "saida_data", "cs", "cw", "cn"]].sort_values("saida_data"),
                      left_on="entrada_data", right_on="saida_data", by="grupo", allow_exact_matches=False,
                      suffixes=("", "_h"))
    for col in ("cs", "cw", "cn"):
        x[col] = x[col].fillna(0.0)
    mu, gw = x["chave"].map(media), x["chave"].map(ganha)
    x["esperado"] = (x["cs"] + mu * k) / (x["cn"] + k)
    x["taxa"] = (x["cw"] + gw * k) / (x["cn"] + k)
    return x.sort_values("_i").drop(columns=["grupo", "saida_data_h", "cs", "cw", "cn", "_i"]).set_index(t.index)


def carteira(c: pd.DataFrame, lugares: int = 5, peso: str = "igual", composto: bool = False) -> dict:
    """Ordem equilibrada, sem trocas. peso: igual | risco (menos nas voláteis) | ordem (mais no 1.º do dia).
    composto: cada posição usa (capital atual / lugares) em vez do capital inicial."""
    if c.empty:
        return {"total": 0.0, "descida": 0.0, "n": 0, "dias": 0.0}
    c = c.assign(rk=c.groupby("entrada_data")["esperado"].rank(pct=True) + c.groupby("entrada_data")["taxa"].rank(pct=True))
    med_atr = c["atr_pct"].median()
    abertas, capital, livre, fechos, curva_d, curva_v = {}, 1.0, 1.0, [], [], []
    datas = sorted(set(c["entrada_data"]) | set(c["saida_data"]))
    por_dia = dict(tuple(c.sort_values(["entrada_data", "rk"], ascending=[True, False]).groupby("entrada_data")))
    n = 0
    for dt in datas:
        for tk in [x for x, v in abertas.items() if v[0] <= dt]:
            _, aloc, ret = abertas.pop(tk)
            capital += aloc * ret
            livre += aloc * (1 + ret)
        curva_d.append(dt)
        curva_v.append(capital)
        g = por_dia.get(dt)
        if g is None:
            continue
        k = 0
        for r in g.drop_duplicates("ticker").itertuples():
            if len(abertas) >= lugares:
                break
            if r.ticker in abertas:
                continue
            w = 1.0
            if peso == "risco":
                w = float(np.clip(med_atr / r.atr_pct, 0.5, 1.0)) if r.atr_pct > 0 else 1.0
            elif peso == "ordem":
                w = 1.4 if k == 0 else 1.0 if k < 3 else 0.7
            aloc = (capital if composto else 1.0) / lugares * w
            aloc = min(aloc, max(livre, 0.0))
            if aloc <= 0:
                break
            livre -= aloc
            abertas[r.ticker] = (r.saida_data, aloc, r.ret)
            fechos.append(r.dias)
            n += 1
            k += 1
    for _, aloc, ret in abertas.values():
        capital += aloc * ret
    v = pd.Series(curva_v + [capital])
    return {"total": capital - 1, "descida": float((v / v.cummax() - 1).min()), "n": n,
            "dias": float(np.mean(fechos)) if fechos else 0.0}


def fmt(r: dict) -> str:
    return f"{r['total']*100:+7.1f}% (desc {r['descida']*100:+5.1f}%, {r['n']} trades, {r['dias']:.1f} dias)"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load((p3v.RAIZ / "config.yaml").read_text())["sinais"]
    precos = p3v.carregar()
    t = recolher(precos, cfg)
    meio = t["entrada_data"].median()
    t["metade"] = np.where(t["entrada_data"] < meio, 1, 2)
    t["chave"] = t["setup"] + "|" + t["saida"]
    print(f"Trades (setup x saída): {len(t)}; metades a partir de {meio:%Y-%m-%d}")

    print("\n### Por setup e saída (líquido; 1.ª|2.ª metade; dias médios; ganho por dia de posição)")
    for (s, sd), g in t.groupby(["setup", "saida"]):
        a, b = g[g["metade"] == 1], g[g["metade"] == 2]
        print(f"  {s:15s} {sd:12s} n={len(g)//1:6d} {g['ret'].mean()*100:+.2f}% ({a['ret'].mean()*100:+.2f}|"
              f"{b['ret'].mean()*100:+.2f}) ganha {(g['ret'] > 0).mean()*100:.0f}% {g['dias'].mean():.1f} dias "
              f"-> {g['ret'].mean() / g['dias'].mean() * 100:+.3f}%/dia")

    treino = t[t["metade"] == 1]
    media = treino.groupby("chave")["ret"].mean().to_dict()
    ganha = treino.groupby("chave")["ret"].apply(lambda r: (r > 0).mean()).to_dict()
    for k in t["chave"].unique():
        media.setdefault(k, 0.0)
        ganha.setdefault(k, 0.5)
    t = esperado(t, media, ganha)

    def escolher(saidas: dict) -> pd.DataFrame:
        return t[t["saida"] == t["setup"].map(saidas)]

    setups = sorted(t["setup"].unique())
    hoje = {s: HOJE.get(s, "sma5_5") for s in setups}
    print("\n### Carteira de 5 lugares, saídas de hoje: " + " | ".join(
        f"{pn}.ª {fmt(carteira(escolher(hoje)[lambda x: x['metade'] == pn]))}" for pn in (1, 2)))
    # Escolha na 1.ª metade, um setup de cada vez (as outras ficam como estão)
    escolha = dict(hoje)
    t1 = t[t["metade"] == 1]
    for s in setups:
        melhor, val = escolha[s], None
        for sd in SAIDAS:
            tent = {**escolha, s: sd}
            r = carteira(t1[t1["saida"] == t1["setup"].map(tent)])
            if val is None or r["total"] > val:
                melhor, val = sd, r["total"]
        escolha[s] = melhor
        print(f"  {s:15s} -> {melhor:12s} (1.ª metade {val*100:+.1f}%)")
    print("\n### Saídas escolhidas na 1.ª metade, medidas nas duas: " + " | ".join(
        f"{pn}.ª {fmt(carteira(escolher(escolha)[lambda x: x['metade'] == pn]))}" for pn in (1, 2)))

    # Hipótese fixa (não escolhida pelos números): sinais de tendência deixam correr (stop que sobe),
    # sinais de ressalto saem depressa. É a ideia do Pestana: umas trades rápidas, outras de 1-2 semanas.
    tendencia = {"rompimento", "semana_forte", "pullback", "lider_recuo"}
    for nome_h, sd in (("tendência a correr 15 dias (stop que sobe 3 ATR)", "segue_3_15"),
                       ("tendência a correr 10 dias (stop que sobe 2 ATR)", "segue_2_10"),
                       ("tendência 10 dias (stop 2 ATR)", "fecho_10")):
        h = {s: (sd if s in tendencia else hoje[s]) for s in setups}
        for lug in (5, 3):
            print(f"  {nome_h}, {lug} lugares: " + " | ".join(
                f"{pn}.ª {fmt(carteira(escolher(h)[lambda x: x['metade'] == pn], lugares=lug))}" for pn in (1, 2)))
        if sd == "segue_3_15":
            escolha = h      # a hipótese fixa substitui a escolha da 1.ª metade nas comparações de gestão

    print("\n### Gestão (tendência a correr 15 dias | saídas de hoje), 1.ª e 2.ª metade")
    for nome, kw in (("5 lugares, igual", {}), ("5 lugares, pelo risco", {"peso": "risco"}),
                     ("5 lugares, pela ordem", {"peso": "ordem"}), ("3 lugares", {"lugares": 3}),
                     ("8 lugares", {"lugares": 8}), ("10 lugares", {"lugares": 10}),
                     ("5 lugares, reinvestido", {"composto": True}),
                     ("5 lugares, pelo risco, reinvestido", {"peso": "risco", "composto": True}),
                     ("8 lugares, reinvestido", {"lugares": 8, "composto": True})):
        txt = []
        for sds in (escolha, hoje):
            x = escolher(sds)
            txt.append(" ".join(f"{fmt(carteira(x[x['metade'] == pn], **kw))}" for pn in (1, 2)))
        print(f"  {nome:36s} {txt[0]}  ||  hoje: {txt[1]}")


if __name__ == "__main__":
    main()
