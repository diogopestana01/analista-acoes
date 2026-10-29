"""Verificador: o que o Claude diz só passa se estiver nas manchetes (offline, sem Claude)."""
import pandas as pd

from src import investigador, sentimento, verificador

AGORA = pd.Timestamp("2026-10-29 15:00", tz="UTC")
NEWS = pd.DataFrame({"titulo": ["Cactus Q3 revenue $430.9 million beats estimates", "Old lawsuit settled"],
                     "resumo": ["", ""], "fonte": ["Reuters", "X"],
                     "data": [pd.Timestamp("2026-10-28", tz="UTC"), pd.Timestamp("2026-08-01", tz="UTC")]})


def test_datas_contadas_pelo_codigo():
    # Caso PVLA: resultados a 5/11 vistos a 29/10 são 5 dias úteis, dentro do prazo.
    t = verificador.contexto_datas(AGORA, "2026-11-05", 5)
    assert "Hoje é 2026-10-29 (quinta-feira)" in t and "faltam 5 dias úteis, DENTRO" in t


def test_manchetes_numeradas_com_idade():
    t = verificador.linhas_numeradas(NEWS, 10, agora=AGORA)
    assert t.startswith("[1] (há 1 dias) Reuters") and "[2] (há 89 dias)" in t


def test_leitura_sem_citacao_passa_a_neutra():
    l = sentimento._Leitura(sentimento=0.8, impacto="apoia", risco_evento=False, resumo="Resultados fortes.", fontes=[])
    av = sentimento.verificar(l, NEWS, "", AGORA)
    assert av.impacto == "neutro" and av.sentimento == 0 and av.verificacao == "sem_citacao"
    # Citar uma manchete que não existe ou só uma antiga também não serve.
    l2 = l.model_copy(update={"fontes": [7]})
    assert sentimento.verificar(l2, NEWS, "", AGORA).verificacao == "sem_citacao"
    l3 = l.model_copy(update={"fontes": [2]})
    assert sentimento.verificar(l3, NEWS, "", AGORA).verificacao == "sem_citacao"


def test_numero_inventado_corta_a_frase():
    l = sentimento._Leitura(sentimento=0.8, impacto="apoia", risco_evento=False, fontes=[1],
                            resumo="Receita de 430,9 milhões acima do esperado. Lucro subiu 59%.")
    av = sentimento.verificar(l, NEWS, "", AGORA)
    assert av.impacto == "apoia" and av.fontes == [1]
    assert av.resumo == "Receita de 430,9 milhões acima do esperado." and av.verificacao == "numeros_cortados"


def test_numeros_do_pedido_contam_como_fonte():
    l = sentimento._Leitura(sentimento=0.5, impacto="apoia", risco_evento=False, fontes=[1],
                            resumo="Subiu 31% em 8 dias com resultados.")
    assert sentimento.verificar(l, NEWS, "Últimos 5 dias +31.0%", AGORA).verificacao == "ok"


def test_advogado_sem_facto_citado_passa_a_manter():
    s = {"ticker": "A", "setup": "rompimento", "entrada": 10.0, "max_dias": 5}
    r = investigador._Resposta(veredicto="rejeitar", risco=7, argumento="Subiu demasiado.", fontes=[])
    c = investigador.verificar(r, s, NEWS, "", AGORA)
    assert c.veredicto == "manter" and c.risco == 2 and c.verificacao == "sem_citacao"
    # Resultados dentro do prazo: o código conhece-os, por isso o aviso fica mesmo sem manchete.
    c = investigador.verificar(r, {**s, "resultados_em": "2026-11-03"}, NEWS, "", AGORA)
    assert c.veredicto == "rejeitar"
    c = investigador.verificar(r.model_copy(update={"fontes": [1]}), s, NEWS, "", AGORA)
    assert c.veredicto == "rejeitar" and c.fontes == [1]


def test_pedido_do_advogado_numera_as_duas_listas():
    s = {"ticker": "A", "setup": "rompimento", "entrada": 10.0, "max_dias": 5, "resultados_em": "2026-11-05"}
    riscos = pd.DataFrame({"titulo": ["A Corp stock offering"], "fonte": ["Reuters"],
                           "data": [pd.Timestamp("2026-10-27", tz="UTC")]})
    p = investigador._pedido(s, NEWS, riscos, AGORA)
    assert "[1] (há 1 dias) Reuters: Cactus" in p and "[3] (há 2 dias) Reuters: A Corp stock offering" in p
    assert "faltam 5 dias úteis, DENTRO" in p


class _Cliente:
    """Cliente falso: devolve sempre a mesma resposta."""
    def __init__(self, out):
        self.beta = self
        self.messages = self
        self.out = out

    def parse(self, **kw):
        return type("R", (), {"parsed_output": self.out, "stop_reason": "end_turn"})()


def test_vigia_so_manda_vender_com_facto_citado():
    pos = {"ticker": "A", "entrada": 10.0, "data_sinal": "2026-10-27", "setup": "pullback"}
    sem = investigador.AlertaPosicao(sair=True, motivo="Vai cair.", fontes=[])
    assert investigador.rever_posicao(pos, NEWS, _Cliente(sem), AGORA).sair is False
    com = investigador.AlertaPosicao(sair=True, motivo="Receita de 430,9 milhões.", fontes=[1])
    assert investigador.rever_posicao(pos, NEWS, _Cliente(com), AGORA).sair is True


def test_claude_a_falhar_fica_neutro():
    class Falha:
        beta = property(lambda self: self)
        messages = property(lambda self: self)

        def parse(self, **kw):
            raise RuntimeError("API em baixo")
    s = {"ticker": "A", "direcao": "compra", "setup": "pullback", "entrada": 10.0}
    av = sentimento.avaliar(s, NEWS, Falha(), AGORA)
    assert av.impacto == "neutro" and av.verificacao == "falhou"


def test_saude_avisa_uma_vez_por_dia():
    from src import saude
    agora = pd.Timestamp("2026-10-29 15:00", tz="UTC")
    p = saude.problemas(pd.Timestamp("2026-10-28"), ["falhou", "falhou", "ok"], agora)
    assert [c for c, _ in p] == ["precos_velhos", "claude_falha"]
    assert saude.problemas(pd.Timestamp("2026-10-29"), ["ok", "falhou", "ok"], agora) == []
    enviados, cache = [], {}
    assert saude.avisar(p, cache, "2026-10-29", enviados.append) == 2
    assert saude.avisar(p, cache, "2026-10-29", enviados.append) == 0 and len(enviados) == 2


def test_conferencia_de_precos_com_segunda_fonte():
    from src import saude
    precos = pd.DataFrame({"ticker": ["AAPL", "MSFT"], "data": pd.to_datetime(["2026-10-28", "2026-10-28"]),
                           "close": [200.0, 400.0]})
    fonte = {"AAPL": ("2026-10-28", 200.5), "MSFT": ("2026-10-28", 360.0)}
    p = saude.conferir_precos(precos, lambda t: fonte.get(t))
    assert len(p) == 1 and "MSFT 11.1%" in p[0][1] and "AAPL" not in p[0][1]
    assert saude.conferir_precos(precos, lambda t: None) == []   # segunda fonte em baixo: sem alarme
