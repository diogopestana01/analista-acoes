"""Testes das ferramentas de notícias e resultados (offline)."""
from pathlib import Path

import pytest

import numpy as np
import pandas as pd
import yaml

import passo2_sinais
from src import indicadores, resultados, sentimento, sinais


def _precos(closes, opens=None, vols=None):
    closes = np.asarray(closes, float)
    n = len(closes)
    opens = closes if opens is None else np.asarray(opens, float)
    return pd.DataFrame({"data": pd.bdate_range("2025-01-01", periods=n), "ticker": "X",
                         "open": opens, "high": np.maximum(opens, closes) * 1.005,
                         "low": np.minimum(opens, closes) * 0.995, "close": closes,
                         "volume": np.full(n, 1e6) if vols is None else np.asarray(vols, float)})


def test_calendario_nasdaq_le_tickers():
    resp = {"data": {"rows": [{"symbol": "AAPL"}, {"symbol": "BRK.B"}, {"symbol": None}]}}
    assert resultados.tickers_do_dia(resp) == {"AAPL", "BRK-B"}
    assert resultados.tickers_do_dia({"data": None}) == set()


def test_gap_continuacao_no_dia_seguinte_ao_salto():
    closes = np.r_[np.full(40, 100.0), [108.0, 109.0]]
    opens = np.r_[np.full(40, 100.0), [105.0, 108.5]]     # dia 40: abre +5% e fecha no topo
    vols = np.r_[np.full(40, 1e6), [5e6, 2e6]]
    d = indicadores.calcular(_precos(closes, opens, vols))
    f = sinais.detetar(d)["gap_continuacao"]
    assert not f.iat[40] and f.iat[41]                    # sinal só no dia seguinte, se segurou


def test_choque_de_baixa_marca_5_dias():
    closes = np.r_[np.full(40, 100.0), [90.0], np.full(6, 90.0)]
    vols = np.r_[np.full(40, 1e6), [4e6], np.full(6, 1e6)]
    d = indicadores.calcular(_precos(closes, vols=vols))
    assert d["choque_baixa_5d"].iat[40] == 1 and d["choque_baixa_5d"].iat[44] == 1
    assert d["choque_baixa_5d"].iat[45] == 0 and d["choque_baixa_5d"].iat[39] == 0


def test_pullback_com_causa_fundamental_e_eliminado():
    tec = pd.DataFrame({"ticker": ["A", "B", "C"], "setup": ["pullback", "pullback", "rompimento"],
                        "direcao": "compra", "pont_tecnica": 60.0, "retorno_esperado_liquido": 0.02})
    av = {t: sentimento.Avaliacao(sentimento=0, impacto="neutro", risco_evento=False,
                                  causa_fundamental=c, resumo="") for t, c in zip("ABC", [True, False, True])}
    out = passo2_sinais.combinar(tec, av, {"peso_noticias": 15, "filtro_queda_com_noticia": True})
    assert sorted(out["ticker"]) == ["B", "C"]   # rompimento com notícia fica


def test_filtro_queda_com_noticia_desligado_mantem_pullback():
    tec = pd.DataFrame({"ticker": ["A"], "setup": ["pullback"], "direcao": "compra",
                        "pont_tecnica": 60.0, "retorno_esperado_liquido": 0.02})
    av = {"A": sentimento.Avaliacao(sentimento=0, impacto="neutro", risco_evento=False,
                                    causa_fundamental=True, resumo="")}
    out = passo2_sinais.combinar(tec, av, {"peso_noticias": 15})
    assert list(out["ticker"]) == ["A"] and bool(out["causa_fundamental"].iat[0])


def test_calendario_linhas_com_valor_e_hora():
    from src import resultados as r
    resp = {"data": {"rows": [{"symbol": "AAPL", "name": "Apple Inc.", "marketCap": "$3,000,000",
                               "time": "time-after-hours"}, {"symbol": "X", "marketCap": "N/A"}]}}
    linhas = r.linhas_do_dia(resp)
    assert linhas[0] == {"ticker": "AAPL", "nome": "Apple Inc.", "quando": "depois do fecho",
                         "valor_mercado": 3_000_000.0}
    assert linhas[1]["valor_mercado"] == 0.0


def test_janela_do_fecho_so_uma_vez_por_dia():
    import resumos_diarios as r
    j = ["16:20", "16:50"]
    # Verão em Nova Iorque (UTC-4): sai às 20:35 UTC, não às 21:35
    assert r.na_janela_do_fecho(pd.Timestamp("2026-10-22 20:35", tz="UTC"), j)
    assert not r.na_janela_do_fecho(pd.Timestamp("2026-10-22 21:35", tz="UTC"), j)
    # Inverno (UTC-5): sai às 21:35 UTC, não às 20:35
    assert not r.na_janela_do_fecho(pd.Timestamp("2026-11-04 20:35", tz="UTC"), j)
    assert r.na_janela_do_fecho(pd.Timestamp("2026-11-04 21:35", tz="UTC"), j)


def test_resumo_fecho_sem_chave_lista_movimentos(monkeypatch):
    from src import resumos
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(resumos, "variacoes", lambda t: pd.DataFrame({"mercado": ["S&P 500"], "preco": [1.0],
                                                                       "var_pct": [0.5]}))
    monkeypatch.setattr(resumos, "manchetes_acao", lambda t, n, k=3: ["Beats estimates"])
    datas = pd.bdate_range("2026-10-20", periods=2)
    precos = pd.DataFrame({"data": list(datas) * 3, "ticker": ["A", "A", "B", "B", "C", "C"],
                           "close": [10, 12, 10, 9, 10, 10.1]})
    filtrado = pd.DataFrame({"ticker": ["A", "B", "C"], "nome": ["Alfa Inc.", "Beta Corp", ""]})
    sobe, desce = resumos.maiores_movimentos(precos, filtrado, 1, 1)
    assert sobe["ticker"].tolist() == ["A"] and desce["ticker"].tolist() == ["B"]
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config_resumos.yaml").read_text())
    txt = resumos.resumo_fecho(pd.Timestamp("2026-10-21", tz="UTC"), precos, filtrado, "3 sinais fechados",
                               {**cfg, "fecho": {**cfg["fecho"], "mais_subiram": 1, "mais_desceram": 1}})
    assert "A +20.0% (Alfa)" in txt and "B -10.0%" in txt and "S&amp;P 500 +0.5%" in txt


def test_regras_de_saida():
    from src import saidas
    closes = np.r_[np.full(30, 100.0), [101.0, 103.0, 104.0, 99.0, 98.0, 97.0]]
    d = indicadores.calcular(_precos(closes))
    # ATR ~ 1; entrada 100 no dia 29
    r = saidas.simular(d, 29, saidas.REGRAS["sma5"])
    assert r[2] == "regra" and r[1] == 1 and np.isclose(r[0], 0.01)   # fecha acima da média de 5 dias logo no dia 30
    r = saidas.simular(d, 29, saidas.REGRAS["fecho_5d"])
    assert r == (pytest.approx(-0.02), 5, "prazo")
    r = saidas.simular(d, 29, saidas.REGRAS["atr_2_1"])
    assert r[2] == "objetivo" and r[1] == 2
    assert saidas.simular(d, 33, saidas.REGRAS["fecho_5d"]) is None   # ainda não terminou


def test_investigador_avisa_e_bloqueia_so_se_configurado(monkeypatch):
    from src import investigador
    chamadas = []

    def falso(s, news, riscos=None, cliente=None):
        chamadas.append(s["ticker"])
        assert list(riscos["titulo"]) == ["Downgrade"]
        return investigador.Contraditorio(veredicto="rejeitar" if s["ticker"] == "A" else "manter",
                                          argumento="Subida já esticada.")
    monkeypatch.setattr(investigador, "contestar", falso)
    monkeypatch.setattr(investigador, "pesquisar_riscos", lambda t, nome="": pd.DataFrame({"titulo": ["Downgrade"]}))
    final = pd.DataFrame({"ticker": ["A", "B"], "setup": "pullback", "entrada": 10.0})
    news = pd.DataFrame({"ticker": ["A", "B"], "titulo": ["x", "y"], "data": pd.NaT})
    cache = {}
    out = passo2_sinais.contraditorio(final, cache, "2026-10-28", {}, news, {"ativo": True})
    assert list(out["veredicto_inv"]) == ["rejeitar", "manter"] and len(out) == 2
    out = passo2_sinais.contraditorio(final, cache, "2026-10-28", {}, news, {"ativo": True, "bloquear": True})
    assert list(out["ticker"]) == ["B"] and chamadas == ["A", "B"]   # segunda vez vem da cache
    from src import telegram
    m = telegram.formatar_sinal({"ticker": "A", "direcao": "compra", "entrada": 10.0, "objetivo": 11.0,
                                 "stop": 9.5, "dias_estimados": 3, "setup": "pullback",
                                 "veredicto_inv": "rejeitar", "argumento_inv": "Subida já esticada."})
    assert "Advogado do diabo (rejeitar): Subida já esticada." in m


def test_intradia_momento_de_entrada():
    import passo3b_intradia as b
    idx = pd.date_range("2026-10-01 13:30", periods=7, freq="h", tz="UTC")
    dia = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0,
                        "close": [99, 100.6, 99.5, 100.8, 101.2, 101.5, 101.4], "volume": 400.0}, index=idx)
    e = {"max20": 100.5, "atr": 1.0, "vol_media": 1000.0}   # volume do dia 2800 >= 1,5 x 1000
    assert b.entrada(dia, e, "imediato") == 1
    assert b.entrada(dia, e, "confirma_1h") is None          # a hora 2 voltou abaixo
    assert b.entrada(dia, e, "folga") == 4                    # 101.2 > 100.5 + 0.5
    assert b.entrada(dia, e, "depois_2h") == 3
    assert b.entrada(dia, e, "fecho") == 6


def test_semanal_nao_ve_o_futuro():
    # Subida forte só na última semana: de segunda a quinta o RSI semanal ainda é o da semana anterior.
    closes = np.r_[np.full(200, 100.0) + np.sin(np.arange(200)), np.linspace(100, 130, 5)]
    d = indicadores.calcular(_precos(closes))
    dias = d["data"].dt.dayofweek
    sexta_antes = d.index[(dias == 4) & (d.index < 200)][-1]
    sexta_depois = d.index[(dias == 4) & (d.index >= 200)][0]
    seg_a_qui = d.index[(d.index > sexta_antes) & (d.index < sexta_depois)]
    assert len(seg_a_qui) == 4
    assert (d.loc[seg_a_qui, "rsi_semanal"] == d.at[sexta_antes, "rsi_semanal"]).all()
    assert d.at[sexta_depois, "rsi_semanal"] > d.at[sexta_antes, "rsi_semanal"]   # à sexta já conta a semana


def test_semana_forte_detetada():
    closes = np.r_[np.linspace(50, 100, 220), [101, 103, 105, 107, 110.0]]   # +10% em 5 dias, tendência
    vols = np.r_[np.full(220, 1e6), np.full(5, 2e6)]
    d = indicadores.calcular(_precos(closes, closes * 0.97, vols))
    assert sinais.detetar(d)["semana_forte"].iat[-1]
    assert "semana_forte" in yaml.safe_load(Path("config.yaml").read_text())["sinais"]["qualidade"]


def test_pesquisa_nao_usa_ticker_sozinho():
    from src import noticias
    t = noticias.termo_pesquisa("KIDS", "")
    assert '"KIDS stock"' in t and '"KIDS"' not in t.replace('"KIDS stock"', "").replace('"KIDS shares"', "")
    assert noticias.termo_pesquisa("SAP.DE", "SAP SE").startswith('"SAP"')
    assert noticias.noticias_sec("SAP.DE") == []     # SEC só para ações dos EUA
    assert noticias.noticias_nasdaq("SAP.DE") == []


def test_investigador_le_a_sua_pesquisa_de_risco():
    from src import investigador
    s = {"ticker": "A", "setup": "rompimento", "entrada": 10.0}
    riscos = pd.DataFrame({"titulo": ["A Corp announces $50M stock offering"], "fonte": ["Reuters"],
                           "data": [pd.Timestamp("2026-10-27", tz="UTC")]})
    p = investigador._pedido(s, pd.DataFrame(columns=["titulo", "fonte", "data"]), riscos)
    assert "pesquisa de risco" in p and "stock offering" in p and "(nada encontrado)" in p


def test_nasdaq_so_fica_com_artigos_da_empresa(monkeypatch):
    import requests
    from src import noticias
    rss = """<rss><channel>
      <item><title>Roku (ROKU) Q3 Earnings Preview</title><pubDate>Tue, 28 Oct 2026 10:00:00 GMT</pubDate></item>
      <item><title>Netflix's Advertising Business Expands</title><pubDate>Tue, 28 Oct 2026 10:00:00 GMT</pubDate></item>
    </channel></rss>"""
    monkeypatch.setattr(requests, "get", lambda *a, **k: type("R", (), {"text": rss})())
    out = noticias.noticias_nasdaq("ROKU", "Roku, Inc. Class A Common Stock")
    assert [i["titulo"] for i in out] == ["Roku (ROKU) Q3 Earnings Preview"]


def test_resultados_dentro_do_prazo_dao_aviso_com_data():
    from src import telegram
    s = pd.DataFrame({"ticker": ["ROKU", "A"], "pontuacao": [70.0, 70.0], "risco_evento": [False, False]})
    out = passo2_sinais.avisar_resultados(s, {"ROKU": "2026-11-02"})
    assert list(out["pontuacao"]) == [60.0, 70.0] and list(out["risco_evento"]) == [True, False]
    m = telegram.formatar_sinal({"ticker": "ROKU", "direcao": "compra", "entrada": 10.0, "objetivo": 11.0,
                                 "stop": 9.5, "dias_estimados": 3, "setup": "pullback",
                                 "resultados_em": "2026-11-02", "risco_evento": True})
    assert "Resultados a 02/11" in m


def test_peso_do_setup_ajusta_aos_resultados_reais():
    cfg = {"qualidade": {"pullback": {"retorno_base_pct": 0.4}, "rompimento": {"retorno_base_pct": 0.4}},
           "peso_backtest_casos": 30}
    b = sinais.base_ajustada(cfg, {"pullback": (30, 0.10), "rompimento": (30, -0.004)})
    assert abs(b["pullback"] - 0.012) < 1e-9          # média real limitada a +2%: (0,4% + 2%) / 2
    assert abs(b["rompimento"] - 0.0) < 1e-9          # (0,4% - 0,4%) / 2
    assert sinais.base_ajustada(cfg)["pullback"] == 0.004   # sem registo fica o backtest


def test_placar_por_estrategia():
    from src import acompanhamento
    h = pd.DataFrame({"data_sinal": "2026-10-20", "ticker": ["A", "B", "C"], "setup": ["pullback", "pullback", "rompimento"],
                      "direcao": "compra", "entrada": 10.0, "preco_fecho": [11.0, 9.5, 10.2],
                      "estado": "fechado", "data_fecho": ["2026-10-27", "2026-10-10", "2026-10-28"]})
    p = acompanhamento.placar(h, "2026-10-28")
    assert "Correção em tendência: 1 fechados, 100% a ganhar, +10.0% | 2 fechados, 50% a ganhar" in p
    assert "Rompimento: 1 fechados" in p


def test_vigia_so_pergunta_com_manchetes_novas_e_uma_vez():
    from src import investigador, vigia
    abertos = pd.DataFrame({"ticker": ["A", "B"], "data_sinal": ["2026-10-26", "2026-10-26"],
                            "entrada": 10.0, "setup": "pullback"})
    riscos = {"A": pd.DataFrame({"titulo": ["A announces stock offering", "Velha"], "fonte": "x",
                                 "data": pd.to_datetime(["2026-10-28", "2026-10-01"], utc=True)}),
              "B": pd.DataFrame({"titulo": ["Velha B"], "fonte": "x", "data": pd.to_datetime(["2026-10-01"], utc=True)})}
    perguntas = []

    def rever(pos, novos):
        perguntas.append((pos["ticker"], list(novos["titulo"])))
        return investigador.AlertaPosicao(sair=True, motivo="Emissão de ações anunciada.")
    cache = {}
    a = vigia.vigiar(abertos, {}, cache, "2026-10-28", pesquisar=lambda t, n: riscos[t], rever=rever)
    assert [x["ticker"] for x in a] == ["A"] and perguntas == [("A", ["A announces stock offering"])]
    assert vigia.vigiar(abertos, {}, cache, "2026-10-28", pesquisar=lambda t, n: riscos[t], rever=rever) == []
    assert len(perguntas) == 1 and "Notícia nova sobre A" in vigia.formatar(a[0])


def test_tendencias_no_alerta_e_resumo(monkeypatch):
    from src import telegram, tendencias
    rd = pd.DataFrame({"ticker": ["AAA", "BBB"], "rank": [3, 40], "mentions": [120, 25],
                       "mentions_24h_ago": [30, 25], "upvotes": [500, 10]})
    rd["variacao"] = rd["mentions"] / rd["mentions_24h_ago"]
    monkeypatch.setattr(tendencias, "reddit", lambda: rd.set_index("ticker"))
    monkeypatch.setattr(tendencias, "stocktwits_destaque", lambda: frozenset({"AAA"}))
    monkeypatch.setattr(tendencias, "stocktwits", lambda t: {"msgs": 30, "otimistas": 14, "pessimistas": 6,
                                                            "otimismo": 0.7})
    cache = {}
    s = tendencias.para_sinal("AAA", cache, "st1|AAA|14|2026-10-29")
    assert s["reddit_rank"] == 3 and s["st_otimismo"] == 0.7 and "st1|AAA|14|2026-10-29" in cache
    assert tendencias.para_sinal("SAP.DE") == {}
    linha = tendencias.linha_alerta(s)
    assert "#3 mais falada" in linha and "4.0x" in linha and "70% otimistas" in linha
    m = telegram.formatar_sinal({"ticker": "AAA", "direcao": "compra", "entrada": 10.0, "objetivo": 11.0,
                                 "stop": 9.5, "dias_estimados": 3, "setup": "pullback", **s})
    assert "📣 Redes" in m
    assert tendencias.em_alta({"AAA", "BBB"}) == ["AAA (120 menções, 4.0x)", "BBB (25 menções, 1.0x)"]


def test_trocas_valor_que_falta_e_decisao():
    from src import trocas
    modelo = {"limites": [-1.0, -0.25, 0.25, 1.0, 2.0], "custo": 0.003, "margem": 0.002,
              "media": {"pullback": 0.004, "rompimento": 0.007},
              "tabela": {"rompimento|1|2": 0.005, "pullback|2|0": 0.006}}
    # Comprada hoje: não se troca.
    assert trocas.valor_que_falta("rompimento", 0, 0.0, 0.02, modelo) is None
    # Dia 1, ganho ~0 ATR -> balde 2.
    assert trocas.valor_que_falta("rompimento", 1, 0.001, 0.02, modelo) == 0.005
    # A perder mais de 1 ATR no dia 2.
    assert trocas.valor_que_falta("pullback", 2, -0.05, 0.02, modelo) == 0.006
    # Sem célula na tabela -> média do setup; dias acima de 5 contam como 5.
    assert trocas.valor_que_falta("pullback", 9, 0.0, 0.02, modelo) == 0.004
    assert trocas.valor_que_falta("pullback", 1, 0.0, None, modelo) == 0.004
    # 0,7% - 0,1% - 0,3% = 0,3% > 0,2% -> troca; contra 0,3% que falta já não.
    assert trocas.vale_trocar("rompimento", 0.001, modelo)[0] is True
    assert trocas.vale_trocar("rompimento", 0.003, modelo)[0] is False
    assert trocas.vale_trocar("desconhecido", 0.0, modelo) == (False, None)


def test_trocas_atr_do_stop():
    from src import trocas
    cfg = {"qualidade": {"rompimento": {"saida": "atr_3_15"}}}
    # stop = entrada - 1,5 x ATR  ->  ATR = 3 / 1,5 = 2 em 100 = 2%.
    assert abs(trocas.atr_pct("rompimento", 100, 97, cfg) - 0.02) < 1e-9
    assert trocas.atr_pct("rompimento", 100, None, cfg) is None
