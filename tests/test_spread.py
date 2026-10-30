"""Spread: comprar ao ask, e o sinal tem de pagar o spread medido (não só os 0,3% fixos)."""
import pandas as pd
import pytest

from src import lista_sinais, spread
from tests.test_lista_sinais import CFG_S, HIST, HOJE, MODELO, PRECOS


def test_spread_em_percentagem_do_meio():
    assert spread.pct(9.9, 10.1) == pytest.approx(0.02)
    assert spread.pct(None, 10) is None and spread.pct(10.2, 10.0) is None and spread.pct(0, 1) is None


def test_medir_so_guarda_quem_tem_os_dois_lados():
    cot = {"A": {"preco": 10, "bid": 9.95, "ask": 10.05}, "B": {"preco": 5}, "C": None}
    m = spread.medir(["A", "B", "C"], buscar=cot.get)
    assert list(m) == ["A"] and m["A"]["spread_pct"] == pytest.approx(0.01)


def test_compra_ao_ask_e_spread_caro_tira_da_compra():
    base = {s["ticker"]: s for s in lista_sinais.avaliar(HIST, PRECOS, HOJE, MODELO, CFG_S)}
    assert base["CEDO"]["estado_lista"] == "comprar" and base["CEDO"]["spread_pct"] is None
    # CEDO: limite 52,5. Bid 50,5 mas o ask já está acima do limite -> já não se compra.
    acima = {s["ticker"]: s for s in lista_sinais.avaliar(
        HIST, PRECOS, HOJE, MODELO, CFG_S, {"CEDO": {"ask": 52.6, "spread_pct": 0.002}})}
    assert acima["CEDO"]["estado_lista"] == "andamento"
    # Spread de 1%: o setup costuma render 0,4% -> não paga a entrada.
    caro = {s["ticker"]: s for s in lista_sinais.avaliar(
        HIST, PRECOS, HOJE, MODELO, CFG_S, {"CEDO": {"ask": 50.75, "spread_pct": 0.01}})}
    assert caro["CEDO"]["estado_lista"] == "andamento"
    assert caro["CEDO"]["esperado_sem_spread"] == pytest.approx(0.0)
    # Spread de 0,2% (abaixo dos 0,3% fixos): continua a dar para comprar.
    barato = {s["ticker"]: s for s in lista_sinais.avaliar(
        HIST, PRECOS, HOJE, MODELO, CFG_S, {"CEDO": {"ask": 50.55, "spread_pct": 0.002}})}
    assert barato["CEDO"]["estado_lista"] == "comprar" and barato["CEDO"]["ask"] == 50.55


def test_registo_guarda_o_primeiro_spread(tmp_path):
    h = HIST.copy()
    h = spread.registar(h, {"CEDO": {"spread_pct": 0.004}})
    h = spread.registar(h, {"CEDO": {"spread_pct": 0.009}})
    assert h.loc[h["ticker"] == "CEDO", "spread_sinal"].iat[0] == 0.004
    f = tmp_path / "spreads.csv"
    spread.guardar_historico(f, pd.Timestamp("2026-10-30 14:30"), {"CEDO": {"spread_pct": 0.004}}, ["CEDO", "X"])
    spread.guardar_historico(f, pd.Timestamp("2026-10-30 14:40"), {"CEDO": {"spread_pct": 0.003}}, ["CEDO"])
    assert pd.read_csv(f)["spread_pct"].tolist() == [0.004, 0.003]
