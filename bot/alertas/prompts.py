"""Prompts do Sistema de Alertas.

Diferente de Missões (orquestrador que age via arquivos), aqui o Claude
acordado tem UMA função de linguagem: redigir o lembrete e enviá-lo pelo
canal. Ele NÃO mexe em estado — o estado é todo do código (AlertasSource).

São templates `.format(...)` — sem jinja, overhead não compensa.
"""

from __future__ import annotations

# Prompt do DISPARO — o Keyko acorda um `claude -p` com isto quando um
# alerta vence (ações ABRIR/COBRAR/DISPARAR; EXPIRAR não acorda o Hal).
PROMPT_DISPARO = """\
Você é o agente do Kobe (Hal) acordado por um ALERTA programado. Sua
ÚNICA tarefa agora é redigir o lembrete e enviá-lo pelo canal certo.
Você NÃO conversa, NÃO mexe em estado, NÃO cria arquivos — escreve a
mensagem, envia, e encerra.

Você está em `{kobe_home}`. As envs KOBE_CHAT_ID, KOBE_THREAD_ID e
KOBE_TELEGRAM_BOT_TOKEN já estão setadas pro tópico de origem.

ALERTA: "{titulo}" (id: {alerta_id})

INSTRUÇÃO DO OPERADOR (o que ele pediu pra você fazer/lembrar):
\"\"\"
{instrucao}
\"\"\"

CONTEXTO DESTE DISPARO:
- Tipo de disparo: {acao_descricao}
- Aguarda confirmação do operador: {aguarda_confirmacao}
{bloco_confirmacao}{bloco_ciclo}

O QUE FAZER:

1. Se a instrução acima pede pra COLETAR dados (ver agenda do Google
   Calendar, tarefas do Todoist, buscar algo na web, rodar um script),
   FAÇA isso primeiro com as ferramentas disponíveis (MCP, WebFetch,
   Bash). Não declare limitação sem testar a ferramenta.

2. Redija a mensagem do lembrete em português, no SEU tom natural de
   sempre (conversacional, direto, brasileiro). Curta. Se é uma
   re-cobrança (tipo "cobrar"), seja progressivamente mais firme — o
   operador já foi lembrado antes neste ciclo e ainda não resolveu.

3. ENVIE pelo canal:
{bloco_canal}

4. Encerre. Não escreva mais nada depois de enviar.

IMPORTANTE: o lembrete tem que SAIR pelo canal (via o helper indicado).
Sua resposta de texto final NÃO chega ao operador — só o que você mandar
pelo helper chega. Se você só "responder" sem chamar o helper, o
operador não recebe nada.
"""


def _bloco_canal(tipo: str, destino: str | None) -> str:
    """Instrução de envio específica do canal."""
    if tipo == "whatsapp":
        # Envio WhatsApp pelo helper-seam do core (kobe-whatsapp). Os
        # Alertas não conhecem o backend — quem fala com ele é o helper.
        # Telegram vira FALLBACK só se o envio falhar (exit != 0).
        return (
            f"   - Canal: WhatsApp (número {destino}). Envie rodando no Bash:\n"
            f"     `bot/bin/kobe-whatsapp \"{destino}\" \"<sua mensagem>\"`\n"
            f"     Exit 0 = entregou; encerre. Se FALHAR (exit≠0), caia no\n"
            f"     FALLBACK: mande pelo Telegram via\n"
            f"     `bot/bin/kobe-notify \"<sua mensagem>\"`, abrindo com uma linha\n"
            f"     curta avisando que o WhatsApp {destino} falhou e segue por aqui."
        )
    # telegram (default)
    return (
        "   - Canal: Telegram (o tópico onde o alerta foi criado).\n"
        "     Envie com `bot/bin/kobe-notify \"<sua mensagem>\"`."
    )


def montar_prompt_disparo(
    *,
    kobe_home: str,
    alerta_id: str,
    titulo: str,
    instrucao: str,
    acao: str,
    acao_descricao: str,
    aguarda_confirmacao: bool,
    canal_tipo: str,
    canal_destino: str | None,
    fecha_quando: str | None,
    ciclo_iniciado_em: str | None,
) -> str:
    """Monta o prompt completo do disparo a partir do estado do alerta."""
    bloco_conf = ""
    if aguarda_confirmacao and fecha_quando:
        bloco_conf = (
            f"- Como o ciclo fecha: {fecha_quando}.\n"
            f"  O operador fecha por CONVERSA NORMAL (ele responde algo como\n"
            f"  \"já marquei\" no chat). Você NÃO precisa fechar nada agora —\n"
            f"  só lembrá-lo. Pode mencionar de leve que é só responder\n"
            f"  quando estiver resolvido.\n"
        )
    bloco_ciclo = ""
    if ciclo_iniciado_em:
        bloco_ciclo = f"- Ciclo atual aberto desde: {ciclo_iniciado_em}.\n"

    return PROMPT_DISPARO.format(
        kobe_home=kobe_home,
        alerta_id=alerta_id,
        titulo=titulo,
        instrucao=instrucao,
        acao_descricao=acao_descricao,
        aguarda_confirmacao="sim" if aguarda_confirmacao else "não",
        bloco_confirmacao=bloco_conf,
        bloco_ciclo=bloco_ciclo,
        bloco_canal=_bloco_canal(canal_tipo, canal_destino),
    )


# Descrição humana de cada ação, injetada no prompt.
ACAO_DESCRICAO = {
    "disparar": "lembrete agendado (dispara e segue)",
    "abrir": "1º lembrete do ciclo (a janela acabou de abrir)",
    "cobrar": "RE-COBRANÇA — o operador já foi lembrado neste ciclo e ainda não resolveu",
}
