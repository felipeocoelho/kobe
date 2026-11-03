"""Documentos alcançáveis pela busca — Highlander v3, F7.2.

O PROBLEMA, EM UMA FRASE
------------------------
O `kobe-remember` alcançava a conversa e a tabela de fatos. Os documentos — planos
das salas, a base de anotações do agente, a base do tópico, o material de trabalho
do operador — só eram achados se alguém lembrasse que existiam e fizesse `grep`.

DOCUMENTO NÃO É VERDADE
-----------------------
Um plano que a sala escreveu e o operador recusou não é "verdade fraca": é proposta
recusada. Por isso nada daqui entra em `lucien_claims`, e toda leitura sai com a
NATUREZA na frente (e, para artefato de sala, com o DESFECHO — que nasce
`desconhecido` e só muda por fonte registrada). Ver `infra/migrations/015`.

UMA REGRA GERAL, NENHUM TÓPICO ESPECIAL (ajuste do operador, 03/11/2026)
----------------------------------------------------------------------
As raízes não são nomeadas por tópico nem por pasta: todo `topics/<slug>/` (qualquer
subpasta, presente ou futura) entra pela mesma regra, com as mesmas exclusões. Tópico
novo ou pasta nova entra sem mexer em código nem em configuração. E nada disto vai
para o prompt: o caminho para o documento é a busca, igual em todos os tópicos.

AS PEÇAS
--------
    raizes.py     o que entra (regra geral) e o que nunca entra
    indexer.py    varre, detecta mudança, preserva o texto, quebra e embedda
    producoes.py  o catálogo das salas e o desfecho de cada produção
    query.py      a busca (as mesmas três pernas da conversa) e o rótulo
    source.py     a fonte do Keyko (tick em background, nunca no turno)

O ARQUIVO NUNCA É TOCADO: o indexador só abre para leitura.
"""
