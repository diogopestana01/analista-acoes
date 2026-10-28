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

    def falso(s, news, cliente=None):
        chamadas.append(s["ticker"])
        return investigador.Contraditorio(veredicto="rejeitar" if s["ticker"] == "A" else "manter",
                                          argumento="Subida já esticada.")
    monkeypatch.setattr(investigador, "contestar", falso)
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
