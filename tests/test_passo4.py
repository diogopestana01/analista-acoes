"""Testes do acompanhamento de sinais e da formatação das mensagens."""
import pandas as pd

from src import acompanhamento, telegram

SINAL = {"ticker": "AAPL", "setup": "rompimento", "direcao": "compra", "entrada": 100.0,
         "objetivo": 104.0, "stop": 98.0, "dias_estimados": 3.0, "pontuacao": 72.0,
         "n_historico": 12, "taxa_acerto": 0.58, "resumo_noticias": "Resultados acima do esperado.",
         "risco_evento": False}


def _precos(lows, highs, closes):
    return pd.DataFrame({"data": pd.bdate_range("2026-10-02", periods=len(lows)), "ticker": "AAPL",
                         "low": lows, "high": highs, "close": closes})


def test_mensagem_tem_entrada_objetivo_e_prazo():
    m = telegram.formatar_sinal(SINAL)
    assert "COMPRA AAPL" in m and "100.00" in m and "104.00" in m and "3 dias úteis" in m
    assert "58% em 12 casos" in m


def test_acompanhamento_fecha_no_objetivo_e_nao_duplica():
    hist = acompanhamento.registar(acompanhamento.carregar(__import__("pathlib").Path("/nao/existe")),
                                   pd.DataFrame([SINAL]), "2026-10-01", 5)
    assert len(acompanhamento.registar(hist, pd.DataFrame([SINAL]), "2026-10-02", 5)) == 1
    precos = _precos([99, 101], [101, 105], [100, 104.5])
    hist, fechos = acompanhamento.atualizar(hist, precos)
    assert hist.loc[0, "estado"] == "objetivo" and hist.loc[0, "dias"] == 2
    assert fechos[0]["resultado"] == "objetivo"
    assert "Objetivo atingido" in telegram.formatar_fecho(fechos[0])
    assert "100% no objetivo" in acompanhamento.resumo(hist)


def test_acompanhamento_stop_e_expira():
    base = acompanhamento.registar(acompanhamento.carregar(__import__("pathlib").Path("/x")),
                                   pd.DataFrame([SINAL]), "2026-10-01", 2)
    h, f = acompanhamento.atualizar(base, _precos([97], [101], [97.5]))
    assert h.loc[0, "estado"] == "stop"
    h, f = acompanhamento.atualizar(base, _precos([99, 99], [101, 101], [100, 100.5]))
    assert h.loc[0, "estado"] == "expirou" and f[0]["preco_atual"] == 100.5


def test_acompanhamento_fecha_depois_de_ler_csv(tmp_path):
    # Bug de 2026-10-22: lido do CSV com colunas de fecho vazias, o fecho rebentava.
    caminho = tmp_path / "hist.csv"
    acompanhamento.registar(acompanhamento.carregar(caminho), pd.DataFrame([SINAL]),
                            "2026-10-01", 5).to_csv(caminho, index=False)
    h, f = acompanhamento.atualizar(acompanhamento.carregar(caminho), _precos([97], [101], [97.5]))
    assert h.loc[0, "estado"] == "stop" and h.loc[0, "data_fecho"] == "2026-10-02"
