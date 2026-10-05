# ADR-0004: Abas paralelas em um único contexto persistente

**Data:** 05/08/2026  
**Status:** Aceito

## Contexto

A versão inicial executava um processo e uma aba por vídeo. O tempo de geração do
Kling é muito maior que o tempo de configuração da página, portanto lotes grandes
ficavam limitados pela espera serial mesmo quando a conta e a máquina tinham
capacidade para mais de uma geração.

Abrir vários processos com o mesmo `user_data_dir` não é válido: o Chrome bloqueia
o perfil e a proteção do projeto encerra processos concorrentes que tentem usá-lo.

## Decisão

O processo filho supervisionado abre exatamente um `launch_persistent_context` e
cria até N páginas novas, configuradas por `--concurrency N`. Cada página recebe
uma instância própria de `Worker`, sem compartilhar `_page`, `HumanInput` ou job.

Os jobs são reivindicados atomicamente antes da abertura das páginas. Uma onda
termina quando todas as suas páginas terminam; então o processo filho sai e o
watchdog inicia a próxima onda. O padrão é 1 e o limite inicial é 6.

Os slots são escalonados por poucos segundos. Navegação, configuração, upload,
preenchimento do prompt e clique em Gerar são protegidos por um lock de primeiro
plano, pois o teste real mostrou que o Firefly deixa abas ocultas sem materializar
controles. Depois do clique, o lock é liberado: geração e polling permanecem
concorrentes. A exportação readquire o lock apenas durante o download.

Prompts são preenchidos atomicamente. A digitação tecla por tecla foi removida
porque o editor ProseMirror rerenderizava no meio de prompts longos, causando
texto parcial e chamadas presas.

## Falhas e pausas

- Uma falha de página marca apenas seu job e não cancela as outras páginas.
- Leituras de estado têm timeout próprio; após uma leitura presa, a aba é trazida
  ao primeiro plano e lida novamente antes de escalar a falha.
- Logout, quota ou seletor não confirmado continuam pausando a fila global.
- Uma página que ainda não começou devolve seu job a `pending` se outra página
  tiver pausado a fila.
- O watchdog continua fora do processo do navegador e pode encerrar toda a árvore
  se o contexto inteiro travar.
- A reconciliação ocorre uma vez antes da onda. Não há múltiplos processos
  concorrentes escrevendo ou reconciliando o mesmo perfil e a mesma fila.

## Consequências

- Há paralelismo real com uma única sessão autenticada.
- Downloads continuam associados ao evento da página e usam `job_id` no nome.
- Uma falha total do Chrome afeta todos os jobs da onda, que são recuperados pelo
  contrato existente de reconciliação.
- Consumo de memória, créditos e risco de rate limit crescem com N; por isso o
  operador precisa habilitar a concorrência explicitamente.

Este ADR substitui as restrições de execução estritamente serial presentes nos
documentos da versão 1, sem autorizar múltiplos processos no mesmo perfil,
automação de login ou evasão de bloqueios.
