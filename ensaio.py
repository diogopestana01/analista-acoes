"""Ensaio: corre os sinais como de verdade, mas sem enviar mensagens nem gravar nada no repositório.

Mostra, passo a passo, o que cada etapa faz: preços, sinais técnicos, leitura das notícias (com o que
o verificador cortou), advogado do diabo, conferência de preços com a Nasdaq, preços de agora das
posições abertas e o texto dos alertas tal como iriam para o Telegram.
Uso:  python ensaio.py [--com-cache]   (sem --com-cache o Claude volta a ler as notícias de hoje)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd

os.environ.pop("TELEGRAM_TOKEN", None)       # nada sai para o Telegram
os.environ.pop("TELEGRAM_CHAT_ID", None)

import passo1_universo  # noqa: E402
import passo2_sinais  # noqa: E402
from src import acompanhamento, precos_agora, saude, telegram  # noqa: E402

DADOS = Path(__file__).parent / "data"


def titulo(t: str) -> None:
    print(f"\n{'=' * 8} {t} {'=' * 8}")


def main() -> None:
    titulo("1. Preços")
    passo1_universo.main()
    precos = pd.read_parquet(DADOS / "precos.parquet")
    print(f"{precos['ticker'].nunique()} ações, último dia {precos['data'].max():%Y-%m-%d}")

    if "--com-cache" not in sys.argv:
        c = DADOS / "avaliacoes_cache.json"
        try:
            cache = json.loads(c.read_text())
            fora = [k for k in cache if k.startswith(("v4|", "inv3|", "saude|", "conferencia|"))]
            for k in fora:
                cache.pop(k)
            c.write_text(json.dumps(cache))
            print(f"Cache: {len(fora)} leituras de hoje apagadas para o Claude ler tudo de novo")
        except FileNotFoundError:
            pass

    titulo("2. Sinais, notícias e advogado do diabo (passo2_sinais)")
    saida = passo2_sinais.main()
    s = pd.read_csv(saida) if saida.stat().st_size > 2 else pd.DataFrame()
    print(f"{len(s)} sinais finais")
    for r in s.to_dict("records"):
        print(f"\n- {r['ticker']} ({r['setup']}) retorno esperado {r['retorno_esperado_liquido'] * 100:+.2f}%")
        print(f"  notícias: {r.get('impacto_noticias')} [{r.get('verif_noticias')}] {r.get('resumo_noticias')}")
        print(f"  advogado: {r.get('veredicto_inv')} risco {r.get('risco_inv')} [{r.get('verif_inv')}] "
              f"{r.get('argumento_inv')}")

    titulo("3. Auditoria (o que o Claude recebeu e o que o verificador mudou)")
    aud = sorted((DADOS / "auditoria").glob("*.json"))
    if aud:
        a = json.loads(aud[-1].read_text())
        mudou = {k: v for k, v in a.items() for papel in ("analista", "advogado")
                 if (v.get(papel) or {}).get("verificado", {}).get("verificacao") not in (None, "ok")}
        print(f"{aud[-1].name}: {len(a)} ações; {len(mudou)} com algo cortado ou descartado")
        for k, v in list(mudou.items())[:3] + list(a.items())[:1]:
            for papel in ("analista", "advogado"):
                x = v.get(papel) or {}
                if x:
                    print(f"\n[{k} | {papel}]\n{x.get('pedido', '')[:1500]}\nCLAUDE: {x.get('bruto')}\n"
                          f"DEPOIS DO VERIFICADOR: {x.get('verificado')}")

    titulo("4. Conferência dos preços com a Nasdaq")
    print(saude.conferir_precos(precos[precos["ticker"].isin(saude.REFERENCIA)]) or "Tudo bate certo")

    titulo("5. Posições em aberto ao preço de agora")
    hist = acompanhamento.carregar(DADOS / "historico_sinais.csv")
    abertos = hist[hist["estado"] == "aberto"]
    q = precos_agora.agora(abertos["ticker"].tolist())
    for r in abertos.to_dict("records"):
        p = q.get(r["ticker"])
        if not p:
            print(f"  {r['ticker']}: sem preço")
            continue
        dist = (p["preco"] / r["stop"] - 1) * 100 if pd.notna(r["stop"]) else float("nan")
        print(f"  {r['ticker']:6s} {p['preco']:9.2f} ({p['fonte']}, {p['hora']}) entrada {r['entrada']:.2f} "
              f"stop {r['stop']:.2f} -> {dist:+.1f}% acima do stop")
    _, fechos = acompanhamento.atualizar(hist, precos)
    print(f"Fechos que o registo faria agora: {[(f['ticker'], f['resultado']) for f in fechos] or 'nenhum'}")

    titulo("6. Alertas como iriam para o Telegram (não enviados)")
    for r in s.head(3).to_dict("records"):
        print(telegram.formatar_sinal(r), "\n")


if __name__ == "__main__":
    main()
