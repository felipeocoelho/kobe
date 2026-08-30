#!/usr/bin/env python3
"""As três pernas, a fusão e o piso do "não tenho registro" (Highlander v3, F2).

O CENÁRIO QUE REPROVA A FASE INTEIRA
-------------------------------------
Assunto que não existe tem que produzir *"não tenho registro disso"*. Vermelho
nisso reprova a F2 mesmo com todo o resto verde — é a trava anti-invenção, e é o
único critério que só passa se a fase estiver certa de verdade.

Por isso a maior parte deste arquivo não testa "achar": testa **não achar**, e
testa as três formas de errar nisso:

1. achar quando não existe (`SEM_REGISTRO` virando resposta);
2. **dizer que não existe quando o instrumento é que falhou** (`FALHA` virando
   `SEM_REGISTRO`) — o falso negativo silencioso que este sistema já cometeu
   duas vezes;
3. costurar menção literal solta numa resposta (`MENCAO_LITERAL_SEM_APOIO`
   virando `ACHOU`).

POR QUE A PERNA DE PALAVRA NÃO VOTA — e por que há teste disso
---------------------------------------------------------------
Medido sobre 16 perguntas, a massa de IDF da perna de palavra deu **zero** para
duas perguntas legítimas e **entre 7,5 e 9** para quatro perguntas sobre
assuntos que nunca existiram — porque "Japão", "piano" e "maratona" existem no
acervo, soltos, fora de contexto. **Raridade não é relevância.** Um desenho em
OU entre as três pernas deixaria passar exatamente a classe "Salesforce".

COMO RODAR
----------
    .venv/bin/python -m pytest tests/test_search_query.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.search import embedder, query  # noqa: E402


# ── Ponte de mentira, roteirizada por perna ───────────────────────────────


class _DB:
    """Devolve o que o roteiro mandar, por tipo de consulta.

    Reconhecer a consulta pelo SQL é feio, e é de propósito: o alternativo seria
    injetar três funções e aí o teste passaria a exercitar os dublês em vez da
    montagem real das consultas, que é justamente onde mora o risco.
    """

    def __init__(self, *, literal=None, palavra=None, sentido=None, df=None, n=1000):
        self._literal = literal or []
        self._palavra = palavra or []
        self._sentido = sentido or []
        self._df = df or {}
        self._n = n

    def query(self, sql, params=()):
        if "string_to_array" in sql:
            texto = params[0]
            return [{"w": f"'{t}'"} for t in _lexemas_de_mentira(texto)]
        if "unnest(%s::text[]) AS w" in sql:
            # o mapeamento palavra crua -> radical; aqui o "radical" é a
            # própria palavra em minúsculas, que basta pro que se testa
            return [{"w": w, "lex": f"'{w.lower()}'"} for w in params[0]]
        if "search_lexeme_df" in sql:
            pedidos = params[0]
            return [{"word": w, "ndoc": d} for w, d in self._df.items() if w in pedidos]
        if "ILIKE" in sql:
            return list(self._literal)
        if "search_tsv @@" in sql:
            return list(self._palavra)
        if "<=>" in sql:
            return list(self._sentido)
        if "DISTINCT ON" in sql:
            return []
        return []

    def one(self, sql, params=()):
        return {"trechos": 0, "idade_segundos": 0}

    def scalar(self, sql, params=()):
        return self._n

    def execute(self, sql, params=()):
        return []


def _lexemas_de_mentira(texto: str) -> list[str]:
    """Aproximação do `to_tsvector('portuguese', …)` — corta e minúsculas."""
    import re

    return sorted({p.lower() for p in re.findall(r"[A-Za-zÀ-ÿ]{3,}", texto)})


def _msg(seq, corpo="", cos=None, idf=0.0):
    linha = {
        "seq": seq,
        "message_id": f"00000000-0000-0000-0000-{seq:012d}",
        "role": "user",
        "created_at": "2026-07-13 14:22:00+00",
        "topico": "Dev Kobe",
    }
    if corpo:
        linha["body"] = corpo
    if cos is not None:
        linha["cos"] = cos
    if idf:
        linha["idf"] = idf
    return linha


@pytest.fixture(autouse=True)
def _vetor_de_mentira(monkeypatch):
    monkeypatch.setattr(embedder, "embed_um", lambda *a, **k: [0.0] * embedder.DIM)
    monkeypatch.setenv("SEARCH_PISO_COS", "0.57")
    monkeypatch.setenv("SEARCH_DF_MAX", "0.05")


# ── O que sai da pergunta ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pergunta,esperado",
    [
        ("o que a gente falou sobre o compat_gate", "compat_gate"),
        ("me lembra o working_set.py", "working_set.py"),
        ("a discussão sobre kobe-recall-since", "kobe-recall-since"),
        ("o que rolou com HINDSIGHT_RECALL", "HINDSIGHT_RECALL"),
        ("aquilo de bot/db.py", "bot/db.py"),
    ],
)
def test_identificador_vai_pra_perna_literal(pergunta, esperado):
    """É o que o dicionário `portuguese` destrói: `kobe-recall-since` vira
    `kobe-recall-sinc` + `recall` + `sinc`, e `sinc` casa com "sincronizar"."""
    assert esperado in query.literais(pergunta)


def test_nome_proprio_no_meio_da_frase_tambem_e_literal():
    assert "Salesforce" in query.literais("decidimos algo sobre o Salesforce?")


def test_palavra_comum_nao_vira_literal():
    """Se toda palavra virasse busca literal, a perna perderia a seletividade
    que é a razão de ela existir."""
    achados = query.literais("o que a gente decidiu sobre a arquitetura de borda")
    assert achados == []


def test_a_primeira_palavra_da_frase_nao_vira_nome_proprio():
    """Toda frase começa com maiúscula. Sem esta guarda, "Quando" e "Depois"
    virariam nome próprio e a perna literal viveria cheia de lixo."""
    assert query.literais("Quando a gente decidiu isso?") == []


def test_radical_banal_sai_da_consulta():
    """"a gente" está em 24% do acervo e "sobre" em 23%. São elas que faziam uma
    pergunta sobre assunto inexistente devolver 30 resultados com nota de
    pergunta legítima."""
    db = _DB(df={"gente": 240, "sobre": 230, "borda": 21}, n=1000)
    pesos, banais, ausentes = query.radicais(db, "o que a gente decidiu sobre borda")
    assert "borda" in pesos
    assert "gente" in banais and "sobre" in banais


def test_radical_mais_raro_pesa_mais():
    db = _DB(df={"borda": 21, "arquitetura": 49}, n=1000)
    pesos, _, _ = query.radicais(db, "arquitetura de borda")
    assert pesos["borda"] > pesos["arquitetura"]


def test_termo_que_ficou_de_fora_por_pouco_e_repescado():
    """"arquitetura" está em 5,2% do acervo e o corte é 5% — ela é sinal
    legítimo e cairia por dois décimos."""
    db = _DB(df={"borda": 21, "arquitetura": 52}, n=1000)
    pesos, _, _ = query.radicais(db, "arquitetura de borda")
    assert "arquitetura" in pesos


def test_se_TODO_radical_e_banal_a_perna_de_palavra_fica_de_fora():
    """A tentação é repescar os "menos comuns" mesmo assim, e ela produz o
    oposto do que se quer: em "o que a gente falou sobre o working_set.py" os
    três menos comuns são `sobre`, `a gente` e `falou`. Perna vazia é a resposta
    honesta — quem carrega a pergunta é a literal e a de sentido."""
    db = _DB(df={"gente": 240, "sobre": 230, "falou": 140}, n=1000)
    pesos, banais, _ = query.radicais(db, "o que a gente falou sobre isso")
    assert pesos == {}
    assert set(banais) >= {"gente", "sobre", "falou"}


def test_radical_ausente_do_acervo_e_reportado_como_ausente():
    db = _DB(df={"gente": 240}, n=1000)
    pesos, _, ausentes = query.radicais(db, "a gente falou de flibbertigibbet")
    assert "flibbertigibbet" not in pesos
    assert "flibbertigibbet" in ausentes


# ── Os dois consertos que a BATERIA achou (30/08) ─────────────────────────


def test_termo_cru_raro_vai_pra_busca_literal():
    """O bug que a bateria da F2 pegou: `kobe-remember "rsync"` devolvia
    SEM REGISTRO para um termo que está em 116 mensagens.

    A causa era de desenho: a perna de palavra não vota (medido, e certo), mas
    todas as 16 perguntas com que eu medi isso eram FRASES. Num termo cru, o
    termo É a pergunta — uma frase de uma palavra embeda mal, o sentido fica
    abaixo do piso, e não sobrava ninguém para votar. E `SEM REGISTRO` é
    justamente o carimbo que o `CLAUDE.md` manda tratar como afirmável: o selo
    mais forte da ferramenta estava mentindo.
    """
    db = _DB(df={"rsync": 30}, n=1000)
    assert "rsync" in query.literais_raros(db, "rsync")


def test_palavra_banal_NAO_vai_pra_busca_literal():
    """É o corte de raridade que impede o conserto acima de virar o problema
    anterior: "a gente" (24%) e "sobre" (23%) casariam com quase tudo."""
    db = _DB(df={"gente": 240, "sobre": 230, "rsync": 30}, n=1000)
    achados = query.literais_raros(db, "o que a gente falou sobre rsync")
    assert "rsync" in achados
    assert "gente" not in achados and "sobre" not in achados


def test_palavra_curta_nao_vira_busca_literal():
    """Abaixo de 4 letras o casamento por substring acontece DENTRO de outras
    palavras e vira ruído."""
    db = _DB(df={"dia": 2}, n=1000)
    assert query.literais_raros(db, "que dia foi") == []


def test_termo_cru_raro_faz_a_busca_afirmar_que_existe():
    """O efeito de ponta a ponta do conserto: deixa de ser SEM REGISTRO."""
    db = _DB(
        literal=[_msg(2146, "o rsync apagou arquivo")],
        sentido=[_msg(9, "outra coisa", cos=0.40)],
        df={"rsync": 30},
        n=1000,
    )
    r = query.buscar(db, "rsync")
    assert r.veredito == "MENCAO_LITERAL"
    assert r.achados


def test_termo_nomeado_e_AUSENTE_veta_o_voto_da_perna_literal():
    """A régua da fase, e o caso que derrubou a versão anterior do conserto.

    Em *"o que a gente decidiu sobre integração com o Salesforce?"*, `integração`
    (radical `integr`, 79 mensagens) é MAIS RARO no acervo que `rsync` (116) —
    então nenhum limiar de raridade separa os dois casos. O que separa é que
    `Salesforce` **não existe**. Deixar a palavra genérica carregar o voto
    transformaria a recusa do cenário que reprova a fase numa "menção literal".
    """
    class _SoIntegracao(_DB):
        def query(self, sql, params=()):
            if "ILIKE" in sql:
                # `integração` acha; `Salesforce` não acha nada
                return [_msg(2029)] if "integra" in str(params[0]).lower() else []
            return super().query(sql, params)

    db = _SoIntegracao(df={"integração": 79, "salesforce": 0}, n=3558)
    r = query.buscar(db, "o que a gente decidiu sobre integração com o Salesforce?")
    assert r.veredito == "SEM_REGISTRO"
    assert "Salesforce" in r.literais_ausentes


def test_o_veto_nao_bloqueia_quando_o_sentido_passa():
    """O veto só torna a perna LITERAL mais conservadora. A de sentido continua
    votando sozinha — senão uma pergunta longa com uma palavra inédita no meio
    ficaria sem resposta mesmo tendo registro."""
    class _SoIntegracao(_DB):
        def query(self, sql, params=()):
            if "ILIKE" in sql:
                return []
            return super().query(sql, params)

    db = _SoIntegracao(
        sentido=[_msg(2146, "rsync", cos=0.64)],
        df={"rsync": 116, "zendesk": 0},
        n=3558,
    )
    r = query.buscar(db, "o que a gente decidiu sobre rsync e Zendesk?")
    assert r.veredito == "ACHOU"


def test_mensagem_sem_trecho_nao_sai_com_citacao_vazia():
    """Visto ao vivo: um acerto literal numa mensagem que o indexador ainda não
    tinha quebrado saiu com o trecho EM BRANCO. Citação vazia é pior que citação
    nenhuma — ela ocupa a vaga de um resultado e não diz nada."""

    class _SemTrecho(_DB):
        def query(self, sql, params=()):
            if "DISTINCT ON" in sql:
                return []          # nenhum trecho para esta mensagem
            if "SELECT id, content FROM messages" in sql:
                return [{"id": params[0][0], "content": "o conteudo cru da mensagem"}]
            if "ILIKE" in sql:
                return [_msg(3600)]
            return super().query(sql, params)

    r = query.buscar(_SemTrecho(df={"rsync": 30}, n=1000), "rsync")
    assert r.achados
    assert r.achados[0].corpo == "o conteudo cru da mensagem"


def test_a_pergunta_repetida_nao_ocupa_as_vagas_do_resultado():
    """O caso ESTRUTURAL, que a janela de 90 s não cobre.

    Uma pergunta já feita antes — dez minutos ou dez meses — está gravada em
    `messages`, e a semelhança de uma pergunta com ela mesma é ~1. Medido na 3ª
    execução da bateria: as duas repetições vieram em 1º e 2º lugar com
    **0,825**, contra **0,614** do melhor resultado de verdade, e sobraram 3
    vagas úteis de 8.

    O teto de 0,75 fica ENTRE os dois números medidos: 0,693 (o melhor resultado
    verdadeiro em todo o acervo) e 0,825 (o eco observado). A primeira versão
    dele foi 0,90, escolhida com margem sobre o 0,693 e sem olhar o outro lado —
    e não teria pego o caso real.
    """
    db = _DB(
        sentido=[
            _msg(3583, "a propria pergunta, de novo", cos=0.83),
            _msg(2077, "a resposta de verdade", cos=0.61),
        ],
        df={"borda": 21},
        n=1000,
    )
    r = query.buscar(db, "borda")
    assert r.ecos_descartados == 1
    # sem o descarte, o topo seria 0,83 (o eco) em vez de 0,61
    assert r.cos_topo == 0.61
    assert [a.seq for a in r.achados] in ([], [2077])


def test_o_descarte_de_eco_respeita_o_agora(monkeypatch):
    """`--agora` desliga a janela inteira, e com ela o descarte: quem pede
    explicitamente o que acabou de ser dito quer ver tudo."""
    db = _DB(sentido=[_msg(3583, "a propria pergunta", cos=0.83)], df={}, n=1000)
    r = query.buscar(db, "borda", janela_eco=0.0)
    assert r.ecos_descartados == 0
    assert r.cos_topo == 0.83


def test_a_janela_de_eco_ignora_a_pergunta_que_originou_o_turno():
    """O bot grava a mensagem do operador em `messages` ANTES de rodar o turno.
    Sem a janela, a busca acha a PRÓPRIA pergunta e responde com ela — visto ao
    vivo na bateria, no cenário do Salesforce, onde a única "menção" encontrada
    era a mensagem que o operador tinha acabado de mandar."""
    sql_visto = {}

    class _Espiao(_DB):
        def query(self, sql, params=()):
            if "ILIKE" in sql:
                sql_visto["literal"] = sql
            return super().query(sql, params)

    query.buscar(_Espiao(df={}, n=1000), "Salesforce")
    assert "make_interval" in sql_visto.get("literal", "")


def test_a_janela_de_eco_pode_ser_desligada():
    sql_visto = {}

    class _Espiao(_DB):
        def query(self, sql, params=()):
            if "ILIKE" in sql:
                sql_visto["literal"] = sql
            return super().query(sql, params)

    query.buscar(_Espiao(df={}, n=1000), "Salesforce", janela_eco=0.0)
    assert "make_interval" not in sql_visto.get("literal", "")


def test_o_que_a_janela_escondeu_e_CONTADO_e_nao_escondido():
    """Esconder em silêncio é o mesmo defeito, de outro lado."""

    class _ComRecentes(_DB):
        def scalar(self, sql, params=()):
            return 3 if "created_at >" in sql else self._n

    r = query.buscar(_ComRecentes(df={}, n=1000), "qualquer coisa")
    assert r.ignoradas_pelo_eco == 3
    assert r.janela_eco_s == query.JANELA_ECO_S


def test_a_calibragem_ignora_a_propria_sonda():
    """A calibragem escreve as perguntas em `messages` ao rodar. Medir logo
    depois é medir o ECO: na primeira vez, três perguntas "com resposta"
    pontuaram 0,992 / 1,000 / 1,000 — similaridade 1,0 é a pergunta encontrando
    a si mesma — e a folga virou -0,386, um falso alarme de "o modelo parou de
    separar"."""
    from bot.search import calibrar

    visto = {}

    class _Espiao(_DB):
        def query(self, sql, params=()):
            if "<=>" in sql:
                visto["sql"] = sql
                visto["params"] = params
            return super().query(sql, params)

    calibrar._topo(_Espiao(df={}, n=1000), "qualquer coisa")
    assert "make_interval" in visto["sql"]
    assert calibrar.JANELA_S in visto["params"]


# ── O veredito ────────────────────────────────────────────────────────────


def test_sentido_acima_do_piso_e_ACHOU():
    db = _DB(
        sentido=[_msg(3059, "arquitetura de borda", cos=0.63)],
        df={"borda": 21},
        n=1000,
    )
    r = query.buscar(db, "o que ficou decidido sobre borda")
    assert r.veredito == "ACHOU"
    assert r.achados and r.achados[0].seq == 3059


def test_assunto_inexistente_e_SEM_REGISTRO():
    """O cenário que reprova a fase inteira. O sentido fica abaixo do piso e a
    perna literal não acha o token — então não existe, e ponto."""
    db = _DB(sentido=[_msg(2028, "integrações disponíveis", cos=0.527)], df={}, n=1000)
    r = query.buscar(db, "o que a gente decidiu sobre integração com o Salesforce?")
    assert r.veredito == "SEM_REGISTRO"
    assert r.achados == []


def test_a_perna_de_palavra_SOZINHA_nao_faz_existir():
    """O caso "piano"/"Japão": massa de IDF alta, sentido abaixo do piso, nenhum
    identificador. Um desenho em OU entre as três pernas responderia aqui — e
    responderia errado."""
    db = _DB(
        palavra=[_msg(1276, idf=8.9), _msg(200, idf=5.9)],
        sentido=[_msg(3346, "outra coisa", cos=0.44)],
        df={"maratona": 3},
        n=1000,
    )
    r = query.buscar(db, "me lembra o que a gente falou sobre treinar para a maratona")
    assert r.veredito == "SEM_REGISTRO"
    assert r.achados == []


def test_mencao_literal_nao_e_ACHOU():
    """A perna literal responde "a palavra aparece", não "existe decisão sobre
    isso": `Japão` dá 7 ocorrências soltas no acervo. Quem lê tem que receber o
    rótulo, nunca uma resposta costurada com as menções."""
    db = _DB(
        literal=[_msg(1747, "o workflow de deploy")],
        sentido=[_msg(1747, "o workflow de deploy", cos=0.42)],
        df={},
        n=1000,
    )
    r = query.buscar(db, "o que a gente decidiu sobre a viagem para o Japão?")
    assert r.veredito == "MENCAO_LITERAL"
    assert r.achados and all(a.literal for a in r.achados)


def test_identificador_achado_verbatim_faz_existir_mesmo_com_sentido_baixo():
    """Achar o token exato que o operador escreveu é prova por construção."""
    db = _DB(
        literal=[_msg(3312, "o working_set.py compara created_at como string")],
        sentido=[_msg(9, "qualquer coisa", cos=0.30)],
        df={},
        n=1000,
    )
    r = query.buscar(db, "o que a gente falou sobre o working_set.py")
    assert r.veredito == "MENCAO_LITERAL"
    assert r.achados


# ── FALHA nunca vira SEM_REGISTRO ─────────────────────────────────────────


def test_embedding_fora_nao_vira_sem_registro(monkeypatch):
    """O falso negativo silencioso. A busca por sentido é a árbitra; sem ela, um
    "não achei" é PARCIAL e tem que ser dito assim."""
    monkeypatch.setattr(
        embedder,
        "embed_um",
        lambda *a, **k: (_ for _ in ()).throw(
            embedder.EmbeddingIndisponivel("OPENAI_API_KEY não configurada")
        ),
    )
    db = _DB(df={}, n=1000)
    r = query.buscar(db, "o que a gente decidiu sobre a borda?")
    assert r.sentido_ativo is False
    assert r.parcial is True
    assert r.motivo_sentido_fora and "OPENAI_API_KEY" in r.motivo_sentido_fora


def test_banco_fora_e_FALHA_e_nao_sem_registro():
    class _Quebrado(_DB):
        def query(self, sql, params=()):
            raise RuntimeError("connection refused")

    r = query.buscar(_Quebrado(), "qualquer coisa")
    assert r.veredito == "FALHA"
    assert r.erro and "connection refused" in r.erro


def test_falha_inesperada_na_busca_por_sentido_e_FALHA(monkeypatch):
    monkeypatch.setattr(
        embedder, "embed_um", lambda *a, **k: (_ for _ in ()).throw(ValueError("torto"))
    )
    r = query.buscar(_DB(df={}, n=1000), "qualquer coisa")
    assert r.veredito == "FALHA"
    assert "ValueError" in (r.erro or "")


# ── Fusão ─────────────────────────────────────────────────────────────────


def test_a_fusao_nao_repete_mensagem():
    """Duas pernas achando a mesma mensagem é o caso BOM — mas ele não pode
    virar duas citações da mesma coisa.

    A pergunta usa um identificador de propósito: `rsync` sozinho NÃO vai pra
    perna literal (é palavra comum, sem separador, e o stemmer a preserva
    inteira), então uma pergunta com ele exercitaria só duas pernas.
    """
    db = _DB(
        literal=[_msg(2146, "sync-prod.sh")],
        palavra=[_msg(2146, idf=7.1)],
        sentido=[_msg(2146, "rsync", cos=0.64)],
        df={"rolou": 30},
        n=1000,
    )
    r = query.buscar(db, "o que rolou com o sync-prod.sh")
    assert [a.seq for a in r.achados] == [2146]
    assert set(r.achados[0].pernas) == {"literal", "palavra", "sentido"}


def test_termo_comum_sem_separador_nao_vai_pra_perna_literal():
    """`rsync` é palavra: o stemmer a preserva inteira e a perna de palavra dá
    conta. Mandar toda palavra pra busca literal faria a perna perder a
    seletividade que é a razão dela existir."""
    assert query.literais("me lembra o que foi conversado sobre rsync") == []


def test_mensagem_achada_por_duas_pernas_sobe():
    db = _DB(
        palavra=[_msg(111, idf=9.0)],
        sentido=[_msg(222, "a", cos=0.70), _msg(111, "b", cos=0.62)],
        df={"borda": 21},
        n=1000,
    )
    r = query.buscar(db, "borda")
    assert r.achados[0].seq == 111


def test_o_piso_e_configuravel(monkeypatch):
    """Ele envelhece conforme o acervo cresce — a folga medida é de 0,061. Por
    isso mora no `.env`, e não como literal no código."""
    monkeypatch.setenv("SEARCH_PISO_COS", "0.90")
    db = _DB(sentido=[_msg(1, "x", cos=0.63)], df={}, n=1000)
    assert query.buscar(db, "borda").veredito == "SEM_REGISTRO"
    monkeypatch.setenv("SEARCH_PISO_COS", "0.50")
    assert query.buscar(db, "borda").veredito == "ACHOU"


def test_piso_torto_no_env_cai_no_default(monkeypatch):
    monkeypatch.setenv("SEARCH_PISO_COS", "abacaxi")
    assert query.piso_cos() == query.PISO_COS_PADRAO
    monkeypatch.setenv("SEARCH_DF_MAX", "abacaxi")
    assert query.df_max() == query.DF_MAX_PADRAO
