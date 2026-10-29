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
    assert "58% a ganhar em 12 casos" in m


def test_acompanhamento_fecha_no_objetivo_e_nao_duplica():
    hist = acompanhamento.registar(acompanhamento.carregar(__import__("pathlib").Path("/nao/existe")),
                                   pd.DataFrame([SINAL]), "2026-10-01", 5)
    assert len(acompanhamento.registar(hist, pd.DataFrame([SINAL]), "2026-10-02", 5)) == 1
    precos = _precos([99, 101], [101, 105], [100, 104.5])
    hist, fechos = acompanhamento.atualizar(hist, precos)
    assert hist.loc[0, "estado"] == "objetivo" and hist.loc[0, "dias"] == 2
    assert fechos[0]["resultado"] == "objetivo"
    assert "Objetivo atingido" in telegram.formatar_fecho(fechos[0])
    assert "100% a ganhar" in acompanhamento.resumo(hist)


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


def test_acompanhamento_sai_pela_media_de_5_dias():
    sinal = {**SINAL, "objetivo": float("nan"), "stop": 90.0, "saida": "sma5", "max_dias": 5}
    hist = acompanhamento.registar(acompanhamento.carregar(__import__("pathlib").Path("/x")),
                                   pd.DataFrame([sinal]), "2026-10-07", 5)
    datas = pd.bdate_range("2026-10-01", periods=8)
    closes = [100, 100, 100, 100, 100, 95, 94, 101]   # entra a 05/10 (95)... sai a 12/10 acima da média
    precos = pd.DataFrame({"data": datas, "ticker": "AAPL", "low": [c - 1 for c in closes],
                           "high": [c + 1 for c in closes], "close": closes})
    h, f = acompanhamento.atualizar(hist, precos)
    assert h.loc[0, "estado"] == "regra" and f[0]["preco_atual"] == 101
    assert "Sinal de saída" in telegram.formatar_fecho(f[0])
    m = telegram.formatar_sinal({**sinal, "entrada": 100.0})
    assert "média de 5 dias" in m and "Objetivo" not in m and "Stop-loss" in m


def test_stop_com_gap_sai_a_abertura_e_pausa():
    hist = acompanhamento.registar(acompanhamento.carregar(__import__("pathlib").Path("/x")),
                                   pd.DataFrame([SINAL]), "2026-10-01", 5)
    precos = _precos([95], [97], [96]).assign(open=96.0)   # abre a 96, abaixo do stop de 98
    h, f = acompanhamento.atualizar(hist, precos)
    assert h.loc[0, "estado"] == "stop" and h.loc[0, "preco_fecho"] == 96.0
    assert acompanhamento.em_pausa(h, "2026-10-05", 3) == {"AAPL"}
    assert acompanhamento.em_pausa(h, "2026-10-12", 3) == set()


def test_saida_ao_fecho_espera_pelo_fecho_da_bolsa():
    """Às 14:00 UTC a vela de hoje dos EUA está incompleta: a regra da média de 5 dias não fecha."""
    import pandas as pd
    from src import acompanhamento
    from pathlib import Path
    hist = acompanhamento.carregar(Path("/nao/existe"))
    hist = pd.concat([hist, pd.DataFrame([{"data_sinal": "2026-10-28", "ticker": "AAA", "setup": "pullback",
                                           "direcao": "compra", "entrada": 100.0, "objetivo": None, "stop": 90.0,
                                           "max_dias": 5, "estado": "aberto", "saida": "sma5"}])], ignore_index=True)
    dias = pd.bdate_range("2026-10-20", "2026-10-29")
    precos = pd.DataFrame({"ticker": "AAA", "data": dias, "open": 100.0, "high": 106.0, "low": 99.0,
                           "close": [100.0] * (len(dias) - 1) + [105.0]})
    h, f = acompanhamento.atualizar(hist, precos, pd.Timestamp("2026-10-29 14:00", tz="UTC"))
    assert h.iloc[-1]["estado"] == "aberto" and not f
    h, f = acompanhamento.atualizar(hist, precos, pd.Timestamp("2026-10-29 20:15", tz="UTC"))
    assert h.iloc[-1]["estado"] == "regra" and len(f) == 1
