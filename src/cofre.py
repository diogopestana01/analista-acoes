"""Guarda o estado da carteira cifrado (o repositório é público).

A chave vem do segredo CARTEIRA_CHAVE; se não existir, deriva-se do TELEGRAM_TOKEN,
que já é segredo. Atenção: se mudares o token do bot sem criar CARTEIRA_CHAVE,
o ficheiro antigo deixa de abrir (o bot começa uma carteira nova).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import carteira


def chave() -> bytes | None:
    segredo = os.environ.get("CARTEIRA_CHAVE") or (
        "carteira:" + os.environ["TELEGRAM_TOKEN"] if os.environ.get("TELEGRAM_TOKEN") else None)
    if not segredo:
        return None
    return base64.urlsafe_b64encode(hashlib.sha256(segredo.encode()).digest())


def abrir(dados: bytes, k: bytes) -> dict:
    """Estado a partir dos bytes cifrados (ex. a versão mais recente do ficheiro no GitHub)."""
    return json.loads(Fernet(k).decrypt(dados))


def carregar(caminho: Path, k: bytes | None, dono: str | None = None) -> dict:
    """Estado guardado, ou um estado novo se o ficheiro não existir. Erro se a chave estiver errada."""
    if not caminho.exists():
        return carteira.novo_estado(dono)
    if k is None:
        raise RuntimeError("Sem chave para abrir a carteira (falta TELEGRAM_TOKEN ou CARTEIRA_CHAVE).")
    try:
        estado = json.loads(Fernet(k).decrypt(caminho.read_bytes()))
    except InvalidToken as e:
        raise RuntimeError("A chave não abre a carteira (mudou o TELEGRAM_TOKEN?).") from e
    if dono and not estado.get("dono"):
        estado["dono"] = str(dono)
    return estado


def guardar(caminho: Path, estado: dict, k: bytes) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(Fernet(k).encrypt(json.dumps(estado, ensure_ascii=False).encode()))
