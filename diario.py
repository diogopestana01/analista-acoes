"""Execução completa: dados -> sinais -> acompanhamento -> Telegram.

O GitHub Actions corre isto de 30 em 30 minutos nos dias úteis. Só fala quando há
sinais novos ou fechos; os resumos de abertura e fecho estão à parte (resumos_diarios.py). A primeira execução
de cada dia reconstrói o universo; as restantes só atualizam os últimos preços.
"""
import json
import logging
import os
from pathlib import Path

import pandas as pd
import yaml

import passo1_universo
import passo2_sinais
from src import acompanhamento, bot, cofre, cotacoes, grafico, lista_sinais, precos as precos_mod, resultados, telegram, vigia

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RAIZ = Path(__file__).parent
DADOS = RAIZ / "data"
HIST = DADOS / "historico_sinais.csv"
CARTEIRA = DADOS / "carteira.enc"
COTACOES = DADOS / "cotacoes.json"


def ler_carteira() -> dict | None:
    """Estado da Carteira, só para leitura (quem escreve é o bot). Se falhar, os sinais seguem sem ela."""
    try:
        return cofre.carregar(CARTEIRA, cofre.chave()) if CARTEIRA.exists() else None
    except Exception as e:  # noqa: BLE001
        logging.warning("Carteira indisponível: %s", e)
        return None


def vigiar_posicoes(hist: pd.DataFrame, data: str, dono: str | None, config: dict) -> None:
    """Notícias de risco novas sobre posições abertas, para quem as tem (e para o dono)."""
    if not config.get("vigia", {}).get("ativo", False):
        return
    import json
    caminho = DADOS / "avaliacoes_cache.json"
    try:
        cache = json.loads(caminho.read_text())
        # Uma vez por hora (a primeira corrida de cada hora) para não abusar do Google Notícias.
        hora = f"vigia_hora|{pd.Timestamp.now(tz='UTC'):%H}|{data}"
        if hora in cache:
            return
        cache[hora] = True
        nomes = pd.read_csv(DADOS / "universo_filtrado.csv").set_index("ticker")["nome"].fillna("").to_dict()
        alertas = vigia.vigiar(hist[hist["estado"] == "aberto"], nomes, cache, data)
        caminho.write_text(json.dumps(cache))
    except Exception as e:  # noqa: BLE001 — a vigia nunca pode parar os sinais
        logging.warning("Vigia das posições falhou: %s", e)
        return
    # Cada pessoa que tem a ação recebe o alerta com botões de venda; o dono recebe sempre.
    for chat, texto, botoes in bot.mensagens_vigia(ler_carteira(), alertas, dono, vigia.formatar):
        telegram.enviar(texto, chat, botoes)


def guardar_cotacoes(precos: pd.DataFrame, hist: pd.DataFrame, estado: dict | None) -> dict:
    em_carteira = {t for u in (_ativos(estado)) for t in u["posicoes"]}
    quero = em_carteira | set(hist.loc[hist["estado"] == "aberto", "ticker"])
    falta = sorted(t for t in em_carteira if t not in set(precos["ticker"]))
    if falta:  # compradas à mão fora do universo
        try:
            precos = pd.concat([precos, precos_mod.descarregar(falta, "1mo")], ignore_index=True)
        except Exception as e:  # noqa: BLE001
            logging.warning("Sem preços para %d ações fora do universo: %s", len(falta), e)
    dados = cotacoes.construir(precos, quero, pd.Timestamp.now(tz="UTC"))
    cotacoes.guardar(COTACOES, dados)
    logging.info("Cotações guardadas: %d ações", len(dados["acoes"]))
    return dados


def _ativos(estado: dict | None) -> list:
    return [u for u in (estado or {}).get("utilizadores", {}).values() if u.get("estado") == "ativo"]


def _ler_cache() -> dict:
    import json
    try:
        return json.loads((DADOS / "avaliacoes_cache.json").read_text())
    except (OSError, ValueError):
        return {}


def _guardar_avisados(avisados: set) -> None:
    import json
    cache = _ler_cache()
    cache["saidas_avisadas"] = sorted(avisados)[-500:]
    (DADOS / "avaliacoes_cache.json").write_text(json.dumps(cache))


def _guardar_trocas(avisadas: set) -> None:
    import json
    cache = _ler_cache()
    cache["trocas_avisadas"] = sorted(avisadas, key=lambda k: k.rsplit("|", 1)[-1])[-300:]
    (DADOS / "avaliacoes_cache.json").write_text(json.dumps(cache))


def avisar_saidas(estado: dict | None, dados: dict, avisados: set) -> None:
    """Quem tem uma posição que tocou no stop ou no objetivo recebe o aviso (uma vez)."""
    msgs = bot.mensagens_saidas(estado, dados, avisados)
    for chat, texto, botoes in msgs:
        telegram.enviar(texto, chat, botoes)
    logging.info("Avisos de stop/objetivo: %d", len(msgs))


def avisar_resultados(estado: dict | None) -> None:
    """Posições com resultados hoje ou no próximo dia útil: aviso a quem as tem, uma vez.
    O calendário da Nasdaq é pedido uma vez por dia; cada corrida vê as posições novas."""
    import json
    em_carteira = sorted({t for u in _ativos(estado) for t in u["posicoes"]})
    if not em_carteira:
        return
    caminho = DADOS / "avaliacoes_cache.json"
    try:
        cache = json.loads(caminho.read_text()) if caminho.exists() else {}
    except ValueError:
        cache = {}
    agora = pd.Timestamp.now(tz="UTC")
    hoje = agora.strftime("%Y-%m-%d")
    chave = f"agenda_resultados|{hoje}"
    agenda = cache.get(chave, {})
    falta = [t for t in em_carteira if t not in cache.get(chave + "|vistos", [])]
    if falta:
        agenda.update(resultados.agenda(agora, falta, 1))
        cache[chave] = agenda
        cache[chave + "|vistos"] = sorted(set(cache.get(chave + "|vistos", [])) | set(falta))
    avisados = set(cache.get("resultados_avisados", []))
    msgs = bot.mensagens_resultados(estado, agenda, avisados, hoje)
    for chat, texto, botoes in msgs:
        telegram.enviar(texto, chat, botoes)
    cache["resultados_avisados"] = sorted(avisados)[-500:]
    caminho.write_text(json.dumps(cache))
    logging.info("Avisos de resultados nas posições: %d", len(msgs))


def enviar_sinais(msgs: list, registos: list[dict], precos: pd.DataFrame, estado: dict | None) -> None:
    """Envia as mensagens dos sinais; cada sinal leva o gráfico (entrada, objetivo, stop). A imagem é feita
    uma vez por ação e reenviada às outras pessoas pelo file_id do Telegram."""
    por_ticker = {s["ticker"]: s for s in registos}
    imagens: dict[str, bytes | str | None] = {}

    def imagem(t: str):
        if t not in imagens:
            try:
                imagens[t] = grafico.grafico_sinal(precos[precos["ticker"] == t], por_ticker[t])
            except Exception as e:  # noqa: BLE001  sem gráfico, o sinal segue só com texto
                logging.warning("Gráfico de %s falhou: %s", t, e)
                imagens[t] = None
        return imagens[t]

    for chat, texto, botoes in msgs:
        mudo = bot.sem_som(estado, chat)
        t = bot.ticker_do_sinal(botoes)
        img = imagem(t) if t in por_ticker else None
        if img is None:
            telegram.enviar(texto, chat, botoes, silencioso=mudo)
            continue
        if len(texto) <= 1000:  # cabe na legenda da imagem (máx. 1024)
            fid = telegram.enviar_foto(img, texto, chat, botoes, mudo)
            if fid is None:
                telegram.enviar(texto, chat, botoes, silencioso=mudo)
        else:
            fid = telegram.enviar_foto(img, "", chat, None, mudo)
            telegram.enviar(texto, chat, botoes, silencioso=mudo)
        if fid:
            imagens[t] = fid


PENDENTES = DADOS / "sinais_pendentes.json"


def guardar_pendentes(sinais: pd.DataFrame) -> None:
    """Sinais criados com a bolsa fechada: guardados (com as notícias e o resto) para a abertura."""
    if sinais.empty:
        return
    try:
        atual = json.loads(PENDENTES.read_text()) if PENDENTES.exists() else {}
        for r in json.loads(sinais.to_json(orient="records", date_format="iso")):
            atual.setdefault(r["ticker"], r)
        PENDENTES.write_text(json.dumps(atual, ensure_ascii=False))
        logging.info("Sinais com a bolsa fechada, guardados para a abertura: %s", sorted(atual))
    except Exception as e:  # noqa: BLE001
        logging.warning("Sinais pendentes não guardados: %s", e)


def enviar_pendentes(hist: pd.DataFrame, agora: pd.Timestamp, estado: dict | None, dono: str | None,
                     precos: pd.DataFrame, trocas_avisadas: set | None = None, cfg: dict | None = None) -> None:
    """Na primeira corrida com a bolsa aberta e a entrada já à abertura no registo, envia os sinais da
    noite numa ronda só, com o preço de abertura, stop e objetivo atualizados. Os que abriram já acima
    do limite de compra (src/lista_sinais.limite) não são enviados: ficam só na lista, em andamento."""
    try:
        pend = json.loads(PENDENTES.read_text()) if PENDENTES.exists() else {}
    except (ValueError, OSError):
        pend = {}
    if not pend:
        return
    prontos, restam = [], {}
    abertos = hist[hist["estado"] == "aberto"].set_index("ticker")
    for t, r in pend.items():
        if t not in abertos.index:          # já fechou (ex.: abriu abaixo do stop) ou saiu do registo
            continue
        h = abertos.loc[[t]].iloc[-1]
        resolvido = str(h.get("entrada_na_abertura")).strip().lower() in ("false", "0", "0.0")
        if not acompanhamento.bolsa_aberta(t, agora) or not resolvido:
            restam[t] = r
            continue
        lim = lista_sinais.limite(h["entrada"], h["objetivo"], h["stop"], h.get("setup"), cfg, h.get("preco_sinal"))
        if lim is not None and h["entrada"] > lim:
            logging.info("%s abriu a %.2f, acima do limite de compra %.2f: não enviado", t, h["entrada"], lim)
            continue
        prontos.append({**r, "entrada": h["entrada"], "stop": h["stop"], "objetivo": h["objetivo"],
                        "data_sinal": h["data_sinal"]})
    PENDENTES.write_text(json.dumps(restam, ensure_ascii=False))
    if prontos:
        hoje = f"{agora:%Y-%m-%d}"
        cabecalho = (f"🌅 <b>Sinais formados com a bolsa fechada</b> ({len(prontos)})\n"
                     "Entrada ao preço de abertura de hoje; confirma na XTB antes de entrar.")
        enviar_sinais(bot.mensagens_sinais(estado, prontos, cabecalho, dono, hist, hoje, trocas_avisadas),
                      prontos, precos, estado)


def main() -> None:
    config = yaml.safe_load((RAIZ / "config.yaml").read_text())
    passo1_universo.main()
    saida = passo2_sinais.main()

    precos = pd.read_parquet(DADOS / "precos.parquet")
    hist = acompanhamento.carregar(HIST)
    estado, dono = ler_carteira(), os.environ.get("TELEGRAM_CHAT_ID")
    # Preço de agora das ações em carteira e com sinal aberto: o bot mostra a carteira com ele e
    # cada posição é comparada com o seu stop e objetivo a cada corrida.
    dados = guardar_cotacoes(precos, hist, estado)
    bot.COTAR = lambda ts: cotacoes.precos_agora(dados, ts)
    # Stop/objetivo: pela posição de cada pessoa e pelo fecho do sinal no registo; a mesma lista de avisos
    # já enviados serve aos dois, para ninguém ficar sem aviso nem recebê-lo duas vezes.
    # inclui os avisos que o bot da Carteira já deu na vigia de 2 em 2 min
    avisados = set(_ler_cache().get("saidas_avisadas", [])) | set((estado or {}).get("saidas_avisadas", []))
    avisar_saidas(estado, dados, avisados)
    hist, fechos = acompanhamento.atualizar(hist, precos)
    for chat, texto, botoes in bot.mensagens_fechos(estado, fechos, dono, avisados):
        telegram.enviar(texto, chat, botoes)
    _guardar_avisados(avisados)
    avisar_resultados(estado)

    try:
        sinais = pd.read_csv(saida)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        sinais = pd.DataFrame()
    data = saida.stem.removeprefix("sinais_") if saida else pd.Timestamp.today().strftime("%Y-%m-%d")
    abertos = set(hist.loc[hist["estado"] == "aberto", "ticker"])
    # Pausa depois de fechar: evita voltar a comprar logo a seguir a um objetivo ou stop.
    pausa = acompanhamento.em_pausa(hist, data, config["sinais"].get("pausa_apos_fecho_dias", 0))
    novos = sinais[~sinais["ticker"].isin(abertos | pausa)] if not sinais.empty else sinais
    agora = pd.Timestamp.now(tz="UTC")
    # Com a bolsa da ação fechada não se pode comprar: o sinal fica registado para a abertura seguinte
    # (entrada = preço de abertura) e só é enviado nessa altura, numa ronda só.
    aberta = novos["ticker"].map(lambda t: acompanhamento.bolsa_aberta(t, agora)) if not novos.empty \
        else pd.Series(dtype=bool)
    agora_ja, para_depois = (novos[aberta], novos[~aberta]) if not novos.empty else (novos, novos)
    guardar_pendentes(para_depois)

    trocas_avisadas = set(_ler_cache().get("trocas_avisadas", []))
    if not agora_ja.empty:
        cabecalho = (f"📊 <b>Novos sinais ({agora:%d/%m %H:%M} UTC)</b> ({len(agora_ja)})\n"
                     f"Preços da última cotação; confirma na XTB antes de entrar.\n{acompanhamento.resumo(hist)}")
        registos = [{**s, "data_sinal": data} for s in agora_ja.to_dict("records")]
        enviar_sinais(bot.mensagens_sinais(estado, registos, cabecalho, dono, hist, data, trocas_avisadas),
                      registos, precos, estado)
    # Os sinais de dias anteriores são reavaliados a cada corrida: se uma posição já deu o que tinha a dar
    # e um sinal que ainda dá para comprar rende mais, propõe a troca (uma vez, só com a bolsa de NY aberta).
    if acompanhamento.bolsa_aberta("SPY", agora):
        for chat, texto, botoes in bot.mensagens_trocas(estado, hist, f"{agora:%Y-%m-%d}", trocas_avisadas):
            telegram.enviar(texto, chat, botoes)
    _guardar_trocas(trocas_avisadas)

    vigiar_posicoes(hist, data, dono, config)
    hist = acompanhamento.registar(hist, novos, data, config["sinais"]["max_dias"], agora)
    # Depois de registar: os sinais da noite guardados nesta corrida já estão no registo (antes, eram
    # tomados por fechados e apagados da lista de espera sem nunca serem enviados).
    enviar_pendentes(hist, agora, estado, dono, precos, trocas_avisadas, config["sinais"])
    _guardar_trocas(trocas_avisadas)
    hist.to_csv(HIST, index=False)


if __name__ == "__main__":
    main()
