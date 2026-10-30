"""Risco de um sinal pela volatilidade (estudo passo3i_risco.py)."""
from src import lista_sinais, risco
from tests.test_lista_sinais import CFG_S, HIST, HOJE, MODELO, PRECOS


def test_niveis_de_risco():
    assert risco.nivel(0.015) == "baixo"          # dia mau ~3,3%
    assert risco.nivel(0.03) == "médio"           # ~6,6%
    assert risco.nivel(0.06) == "alto"            # ~13%
    assert risco.nivel(None) is None and risco.texto(None) == ""
    assert risco.texto(0.03) == "🟡 Risco médio: num dia mau (1 em 10) perde ~7%"


def test_lista_traz_o_risco():
    itens = {s["ticker"]: s for s in lista_sinais.avaliar(HIST, PRECOS, HOJE, MODELO, CFG_S)}
    # CEDO: rompimento, entrada 50, stop 47,5 = 1,5 ATR -> ATR 3,3% -> dia mau ~7%
    assert itens["CEDO"]["risco"] == "médio" and round(itens["CEDO"]["perda_dia_mau"], 3) == 0.073
