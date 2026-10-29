"""Execução completa: dados -> sinais -> acompanhamento -> Telegram.

O GitHub Actions corre isto de 30 em 30 minutos nos dias úteis. Só fala quando há
sinais novos ou fechos; os resumos de abertura e fecho estão à parte (resumos_diarios.py). A primeira execução
de cada dia reconstrói o universo; as restantes só atualizam os últimos preços.
"""
import logging
import os
from pathlib import Path

import pandas as pd
import yaml

import passo1_universo
import passo2_sinais
from src import acompanhamento, bot, cofre, cotacoes, grafico, precos as precos_mod, resultados, telegram, vigia

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

    if not novos.empty:
        cabecalho = (f"📊 <b>Novos sinais ({pd.Timestamp.now(tz='UTC'):%d/%m %H:%M} UTC)</b> ({len(novos)})\n"
                     f"Preços da última cotação; confirma na XTB antes de entrar.\n{acompanhamento.resumo(hist)}")
        registos = [{**s, "data_sinal": data} for s in novos.to_dict("records")]
        enviar_sinais(bot.mensagens_sinais(estado, registos, cabecalho, dono, hist, data), registos, precos, estado)

    vigiar_posicoes(hist, data, dono, config)
    hist = acompanhamento.registar(hist, novos, data, config["sinais"]["max_dias"])
    hist.to_csv(HIST, index=False)


if __name__ == "__main__":
    main()
