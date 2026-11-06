import numpy as np

from src import estatistica


def test_sharpe_deflacionado_sorte_vs_vantagem():
    rng = np.random.default_rng(1)
    sorte = rng.normal(0.0, 0.02, 2000)
    vantagem = rng.normal(0.004, 0.02, 2000)
    a = estatistica.sharpe_deflacionado(sorte, 250, 0.002)
    b = estatistica.sharpe_deflacionado(vantagem, 250, 0.002)
    assert a["prob"] < 0.5
    assert b["prob"] > 0.95


def test_mais_tentativas_exige_mais():
    assert estatistica.sharpe_esperado_maximo(1000, 0.01) > estatistica.sharpe_esperado_maximo(10, 0.01)
    r = np.random.default_rng(2).normal(0.002, 0.02, 500)
    assert estatistica.sharpe_deflacionado(r, 10, 0.002)["prob"] > estatistica.sharpe_deflacionado(r, 1000, 0.002)["prob"]


def test_poucos_dados():
    assert estatistica.sharpe_deflacionado([0.01, 0.02], 10, 0.01)["prob"] == 0.0
