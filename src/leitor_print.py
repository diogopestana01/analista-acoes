"""Ler um print da XTB (posição aberta ou fechada) para preencher o talão sozinho.

O Claude só lê os números do print; o código confirma-os (volume × preço = valor) e nunca regista
nada sem a pessoa carregar em ✅ Confirmar no talão.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re

log = logging.getLogger(__name__)
MODELO = os.environ.get("MODELO_CLAUDE") or "claude-haiku-5-5"

PEDIDO = """Isto é um print da app da corretora XTB com uma posição em ações.
Lê só o que está escrito no print e responde apenas com um objeto JSON com estas chaves (null se não estiver no print):
"nome": nome da empresa como aparece,
"ticker": símbolo da ação se aparecer (ex. "UNH"), senão null,
"lado": "compra" se for uma posição Buy/Compra, "venda" se for Sell,
"fechada": true se a posição já foi fechada (há preço ou data de fecho), senão false,
"volume": volume da posição (número de ações),
"preco_abertura": preço de abertura,
"valor_abertura": valor de abertura em USD,
"preco_fecho": preço de fecho (só se fechada),
"lucro": lucro neto em USD (negativo se for perda),
"data_abertura": data de abertura no formato AAAA-MM-DD.
Números com ponto decimal, sem símbolos de moeda nem separadores de milhares."""


def ler(imagem: bytes, media_type: str = "image/jpeg", cliente=None) -> dict | None:
    """Pede ao Claude os números do print. None se falhar."""
    try:
        import anthropic
        cliente = cliente or anthropic.Anthropic()
        resp = cliente.messages.create(
            model=MODELO, max_tokens=1024,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                             "data": base64.standard_b64encode(imagem).decode()}},
                {"type": "text", "text": PEDIDO}]}])
        texto = "".join(b.text for b in resp.content if b.type == "text")
        m = re.search(r"\{.*\}", texto, re.S)
        return json.loads(m.group()) if m else None
    except Exception as e:  # noqa: BLE001  sem leitura, a pessoa usa o talão à mão
        log.warning("Leitura do print falhou: %s", e)
        return None


def _num(x) -> float | None:
    try:
        v = float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def verificar(d: dict | None) -> dict | None:
    """Confere o que o Claude leu. Volume, preço e valor têm de bater (até 1% ou 2 cêntimos);
    falta um deles, calcula-se dos outros dois. None se não der para confiar."""
    if not d:
        return None
    vol, preco, valor = _num(d.get("volume")), _num(d.get("preco_abertura")), _num(d.get("valor_abertura"))
    if vol is not None and preco and valor:
        if abs(vol * preco - valor) > max(0.02, valor * 0.01):
            return None
    elif preco and valor:
        vol = valor / preco
    elif vol and preco:
        valor = vol * preco
    else:
        return None
    if not vol or vol <= 0 or not preco or preco <= 0:
        return None
    fecho, lucro = _num(d.get("preco_fecho")), _num(d.get("lucro"))
    if fecho is not None and fecho <= 0:
        fecho = None
    ticker = re.sub(r"[^A-Z.\-]", "", str(d.get("ticker") or "").upper())
    ticker = re.sub(r"\.US$", "", ticker) or None  # a XTB escreve "UNH.US"
    return {"nome": str(d.get("nome") or "").strip(), "ticker": ticker, "volume": vol, "preco": preco,
            "valor": round(valor, 2), "fechada": bool(d.get("fechada")), "preco_fecho": fecho, "lucro": lucro,
            "lado": d.get("lado") or "compra", "data": str(d.get("data_abertura") or "")[:10] or None}


def _simples(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def achar_ticker(lido: dict, candidatos: dict[str, str]) -> str | None:
    """Ticker do print: o que vem escrito, se for um dos candidatos; senão pelo nome da empresa.
    `candidatos` = {ticker: nome} (posições, sinais em aberto, universo)."""
    t = lido.get("ticker")
    if t and t in candidatos:
        return t
    nome = _simples(lido.get("nome") or "")
    if len(nome) < 3:
        return t
    achados = [c for c, n in candidatos.items() if n and (_simples(n).startswith(nome) or nome.startswith(_simples(n)))]
    return achados[0] if len(achados) == 1 else (t if t else None)
