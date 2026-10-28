"""Passo 2: gerar os sinais de hoje para todo o universo filtrado.

Uso:  python passo2_sinais.py   (depois de passo1_universo.py)
Gera data/sinais_AAAA-MM-DD.csv com as melhores oportunidades do dia.
"""
import json
import logging
from pathlib import Path

import pandas as pd
import yaml

from src import acompanhamento, investigador, noticias, precos, resultados, sentimento, sinais

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RAIZ = Path(__file__).parent
DADOS = RAIZ / "data"


def combinar(tecnico: pd.DataFrame, avaliacoes: dict, cfg: dict) -> pd.DataFrame:
    s = tecnico.copy()
    s["sentimento"] = s["ticker"].map(lambda t: avaliacoes[t].sentimento if t in avaliacoes else 0.0)
    s["impacto_noticias"] = s["ticker"].map(lambda t: avaliacoes[t].impacto if t in avaliacoes else "neutro")
    s["risco_evento"] = s["ticker"].map(lambda t: avaliacoes[t].risco_evento if t in avaliacoes else False)
    s["resumo_noticias"] = s["ticker"].map(lambda t: avaliacoes[t].resumo if t in avaliacoes else "")
    s["causa_fundamental"] = s["ticker"].map(lambda t: avaliacoes[t].causa_fundamental if t in avaliacoes else False)
    direcao = s["direcao"].map({"compra": 1, "venda": -1})
    s["pontuacao"] = (s["pont_tecnica"] + cfg["peso_noticias"] * s["sentimento"] * direcao).round(1)
    s.loc[s["risco_evento"], "pontuacao"] -= 10
    s = s[s["impacto_noticias"] != "contraria"]  # notícias contra o sinal eliminam-no
    # Opcional: pullback só para quedas sem notícia. Desligado por defeito porque no backtest
    # (2024-26) os pullbacks depois de um choque de baixa com volume renderam mais, não menos.
    if cfg.get("filtro_queda_com_noticia"):
        s = s[~((s["setup"] == "pullback") & s["causa_fundamental"])]
    s["noticia_forte"] = (s["impacto_noticias"] == "apoia") & (s["sentimento"] * direcao >= 0.5)
    return s.sort_values(["noticia_forte", "retorno_esperado_liquido"], ascending=False).reset_index(drop=True)


def avisar_resultados(s: pd.DataFrame, datas_res: dict) -> pd.DataFrame:
    """Sinais com resultados dentro do prazo do trade: data no alerta, risco de evento e -10 pontos."""
    if s.empty:
        return s
    s = s.copy()
    s["resultados_em"] = s["ticker"].map(datas_res)
    tem = s["resultados_em"].notna()
    s.loc[tem & ~s["risco_evento"].astype(bool), "pontuacao"] -= 10   # quem já tinha aviso já perdeu os 10
    s["risco_evento"] = s["risco_evento"].astype(bool) | tem
    return s


def contraditorio(final: pd.DataFrame, cache: dict, dia: str, nomes: dict, news: pd.DataFrame | None,
                  cfg_inv: dict) -> pd.DataFrame:
    """Investigador advogado do diabo: uma vez por dia e sinal (cache), só nos sinais finais."""
    if final.empty or not cfg_inv.get("ativo", False):
        return final
    chave = lambda s: f"inv2|{s['ticker']}|{s['setup']}|{dia}"
    falta = [s for s in final.to_dict("records") if chave(s) not in cache]
    sem_news = [s["ticker"] for s in falta if news is None or s["ticker"] not in set(news["ticker"])]
    if sem_news:
        extra = noticias.recolher({t: nomes.get(t, "") for t in sem_news})
        news = extra if news is None else pd.concat([news, extra], ignore_index=True)
    for s in falta:
        riscos = investigador.pesquisar_riscos(s["ticker"], nomes.get(s["ticker"], ""))
        c = investigador.contestar(s, news[news["ticker"] == s["ticker"]] if news is not None else pd.DataFrame(),
                                   riscos)
        cache[chave(s)] = c.model_dump() if c else {"veredicto": "", "argumento": "", "risco": None}
    f = final.copy()
    f["veredicto_inv"] = [cache[chave(s)]["veredicto"] for s in f.to_dict("records")]
    f["argumento_inv"] = [cache[chave(s)]["argumento"] for s in f.to_dict("records")]
    f["risco_inv"] = [cache[chave(s)].get("risco") for s in f.to_dict("records")]
    logging.info("Investigador: %d analisados agora; veredictos %s", len(falta),
                 f["veredicto_inv"].value_counts().to_dict())
    if cfg_inv.get("bloquear"):
        f = f[f["veredicto_inv"] != "rejeitar"].reset_index(drop=True)
    return f


def main() -> Path:
    config = yaml.safe_load((RAIZ / "config.yaml").read_text())
    cfg = config["sinais"]
    hist = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    hist = hist[hist["ticker"].isin(filtrado["ticker"])]

    hist = precos.projetar_volume_hoje(hist, pd.Timestamp.now(tz="UTC"))
    ultimo_dia = hist["data"].max()
    hist = hist[hist.groupby("ticker")["data"].transform("max") == ultimo_dia]  # só ações com dados de hoje

    # O peso de cada setup ajusta-se sozinho aos resultados reais em papel (sinais.base_ajustada).
    reais = acompanhamento.resultados_reais(acompanhamento.carregar(DADOS / "historico_sinais.csv"))
    logging.info("Base por setup (backtest + papel): %s",
                 {k: f"{v*100:+.2f}%" for k, v in sinais.base_ajustada(cfg, reais).items()})
    tecnico = sinais.pontuar(sinais.sinais_hoje(hist, cfg, cfg["setups"]), cfg, reais)
    saida = DADOS / f"sinais_{pd.Timestamp(ultimo_dia):%Y-%m-%d}.csv"
    if tecnico.empty:
        logging.info("Sem sinais técnicos hoje")
        pd.DataFrame().to_csv(saida, index=False)
        return saida
    logging.info("Setups detetados: %d", len(tecnico))
    # Só seguem os sinais fortes: passam os filtros do setup e têm retorno esperado
    # (após custos) acima da fasquia. Não há número fixo de sinais por dia.
    tecnico = tecnico[tecnico["passa_filtros"]
                      & (tecnico["retorno_esperado_liquido"] * 100 >= cfg["retorno_minimo_pct"])]
    tecnico = tecnico.drop_duplicates("ticker")   # vários setups na mesma ação: fica o de maior retorno esperado
    logging.info("Sinais fortes (antes das notícias): %d", len(tecnico))
    # Radar de resultados: fora os sinais técnicos de empresas que apresentam contas nos próximos dias.
    # Até `evitar_resultados_dias` sai o sinal; até `avisar_resultados_dias` (o prazo do trade) fica com aviso.
    datas_res = {}
    if cfg.get("evitar_resultados_dias", 0) and not tecnico.empty:
        agora = pd.Timestamp.now(tz="UTC")
        datas_res = resultados.datas(agora, max(cfg["evitar_resultados_dias"], cfg.get("avisar_resultados_dias", 0)))
        limite = f"{pd.bdate_range(agora.normalize().tz_localize(None), periods=cfg['evitar_resultados_dias'] + 1)[-1]:%Y-%m-%d}"
        com_resultados = {t for t, d in datas_res.items() if d <= limite}
        antes = len(tecnico)
        tecnico = tecnico[~(tecnico["ticker"].isin(com_resultados) & (tecnico["setup"] != "gap_continuacao"))]
        logging.info("Retirados por resultados próximos: %d", antes - len(tecnico))
    if tecnico.empty:
        pd.DataFrame().to_csv(saida, index=False)
        return saida

    candidatos = tecnico.head(cfg["max_candidatos_noticias"])
    # Cada ação só é analisada uma vez por dia e setup, para não repetir custos de hora a hora.
    cache_path = DADOS / "avaliacoes_cache.json"
    dia = f"{pd.Timestamp(ultimo_dia):%Y-%m-%d}"
    try:
        cache = {k: v for k, v in json.loads(cache_path.read_text()).items() if k.endswith(dia)}
    except (FileNotFoundError, ValueError):
        cache = {}
    chave = lambda s: f"v3|{s['ticker']}|{s['setup']}|{dia}"
    falta = [s for s in candidatos.to_dict("records") if chave(s) not in cache]
    nomes = filtrado.set_index("ticker")["nome"].fillna("").to_dict()
    news = noticias.recolher({s["ticker"]: nomes.get(s["ticker"], "") for s in falta}) if falta else None
    for sinal in falta:
        av = sentimento.avaliar(sinal, news[news["ticker"] == sinal["ticker"]])
        cache[chave(sinal)] = av.model_dump()
    cache_path.write_text(json.dumps(cache))
    avaliacoes = {s["ticker"]: sentimento.Avaliacao(**cache[chave(s)]) for s in candidatos.to_dict("records")}
    logging.info("Notícias analisadas agora: %d (resto vem da cache)", len(falta))

    final = avisar_resultados(combinar(candidatos, avaliacoes, cfg), datas_res).head(cfg["max_alertas"])
    final = contraditorio(final, cache, dia, nomes, news, config.get("investigador", {}))
    cache_path.write_text(json.dumps(cache))
    final.to_csv(saida, index=False)
    logging.info("Alertas finais: %d -> %s", len(final), saida)
    return saida


if __name__ == "__main__":
    main()
