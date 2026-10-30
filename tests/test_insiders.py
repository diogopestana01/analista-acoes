"""Compras de insiders e short interest (src/insiders.py), sem internet."""
import pandas as pd

from src import insiders

HOJE = pd.Timestamp("2026-10-30", tz="UTC")
LINHAS = [
    {"insider": "A", "lastDate": "10/20/2026", "transactionType": "Buy", "sharesTraded": "1,000", "lastPrice": "$10.00"},
    {"insider": "B", "lastDate": "10/01/2026", "transactionType": "Buy", "sharesTraded": "500", "lastPrice": "$11.00"},
    {"insider": "A", "lastDate": "09/15/2026", "transactionType": "Buy", "sharesTraded": "100", "lastPrice": "$9.00"},
    {"insider": "C", "lastDate": "09/10/2026", "transactionType": "Acquisition (Non Open Market)", "sharesTraded": "9"},
    {"insider": "D", "lastDate": "10/10/2026", "transactionType": "Automatic Sell", "sharesTraded": "9"},
    {"insider": "E", "lastDate": "10/11/2026", "transactionType": "Sell", "sharesTraded": "9"},
    {"insider": "F", "lastDate": "05/01/2026", "transactionType": "Buy", "sharesTraded": "9", "lastPrice": "$1"},
]


def test_resumo_so_conta_compras_no_mercado_nos_90_dias():
    r = insiders.resumir(LINHAS, HOJE)
    assert r == {"compradores": 2, "compras": 3, "vendas": 1, "valor_compras": 16400, "ultima_compra": "2026-10-20"}


def test_texto():
    assert insiders.texto({"compradores": 3}, None).endswith("(compra em grupo)")
    assert insiders.texto({"compradores": 1}, None).startswith("👔 1 insider comprou")
    assert insiders.texto({"compradores": 0}, {"dias": 2.0}) == ""
    assert "6.7 dias para cobrir (a subir)" in insiders.texto(None, {"dias": 6.74, "dias_antes": 2.3})
    assert insiders.insiders("ASML.AS", HOJE) is None and insiders.short("ASML.AS") is None
