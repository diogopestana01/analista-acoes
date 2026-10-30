"""Conselho de esperar quando o spread está alto (src/momento.py) e onde aparece no bot."""
import pandas as pd

from src import bot, momento

# Sexta 30/10/2026: NY abre às 13:30 UTC (ainda horário de verão nos EUA), Lisboa está em UTC.
CEDO = pd.Timestamp("2026-10-30 13:40", tz="UTC")     # 10 min depois da abertura
CALMO = pd.Timestamp("2026-10-30 15:30", tz="UTC")
FECHADO = pd.Timestamp("2026-10-30 21:00", tz="UTC")


def test_minutos_e_hora_calma():
    assert momento.minutos_desde_abertura(CEDO) == 10
    assert momento.minutos_desde_abertura(FECHADO) is None
    assert momento.hora_calma_lisboa(CEDO) == "14:00"
    # depois do fecho de sexta: segunda 2/11, já com a hora de inverno nos EUA (abre 14:30 UTC)
    assert momento.hora_calma_lisboa(FECHADO) == "15:00"


def test_normais_so_com_medidas_calmas(tmp_path):
    f = tmp_path / "spreads.csv"
    linhas = [("2026-10-30 13:35", "ABC", 0.02)]  # primeira meia hora: não conta
    linhas += [(f"2026-10-30 1{h}:00", "ABC", v) for h, v in ((4, 0.004), (5, 0.006), (6, 0.005))]
    linhas += [("2026-10-30 14:00", "XYZ", 0.01)]  # uma só medida: não chega
    pd.DataFrame(linhas, columns=["hora", "ticker", "spread_pct"]).to_csv(f, index=False)
    assert momento.normais(f) == {"ABC": 0.005}


def test_conselho():
    assert momento.conselho(0.002, 0.001, CEDO) is None              # barato: não vale esperar
    assert momento.conselho(0.012, 0.004, FECHADO) is None           # bolsa fechada
    c = momento.conselho(0.012, 0.004, CEDO)
    assert c["porque"] == "normal" and abs(c["poupa"] - 0.004) < 1e-12 and c["ate"] == "14:00"
    assert "o normal desta ação é 0,40%" in momento.texto(c, 0.012) and "poupas ~0,40%" in momento.texto(c, 0.012)
    assert momento.conselho(0.005, 0.004, CALMO) is None             # perto do normal
    assert momento.conselho(0.008, None, CEDO)["porque"] == "abertura"
    assert momento.conselho(0.008, None, CALMO) is None              # sem histórico e já calmo
    assert momento.curto(c) == "⏳ espera até 14:00"


def test_bot_mostra_espera_na_venda_do_objetivo(monkeypatch, tmp_path):
    f = tmp_path / "spreads.csv"
    pd.DataFrame([(f"2026-10-30 1{h}:00", "ABC", 0.004) for h in (4, 5, 6)],
                 columns=["hora", "ticker", "spread_pct"]).to_csv(f, index=False)
    monkeypatch.setattr(bot, "SPREADS", f)
    monkeypatch.setattr(bot, "COTAR_LADO", lambda ts, lado: {"ABC": 9.95 if lado == "venda" else 10.07})
    monkeypatch.setattr(momento.pd.Timestamp, "now", classmethod(lambda cls, tz=None: CEDO))
    linha = bot._espera_venda("ABC")
    assert linha.startswith("⏳ Spread alto agora: 1,20%") and "para vender" in linha
