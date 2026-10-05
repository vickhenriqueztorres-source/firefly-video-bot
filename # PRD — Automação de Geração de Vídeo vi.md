# PRD — Automação de Geração de Vídeo via Navegador (Adobe Firefly)

**Versão:** 1.0
**Autor:** Brenda Costa Barbosa
**Status:** Draft para implementação
**Última atualização:** 21/07/2026

---

## 1. Resumo executivo

Ferramenta interna que automatiza a geração de vídeos no Adobe Firefly **via navegador** (sem uso de API oficial), a partir de uma lista de prompts, rodando de forma desatendida e resiliente contra as instabilidades de um site pesado com geração assíncrona lenta. O sistema entra numa sessão já autenticada manualmente, opera a UI de geração de vídeo, aguarda a conclusão de cada geração e baixa o resultado — processando um lote de prompts sem intervenção humana entre um e outro, mas nunca autônomo diante de falhas estruturais (login, quota, bloqueio).

## 2. Problema

Gerar vídeos manualmente no Firefly, um por um, clicando e esperando, não escala para o volume de produção de conteúdo (avatares AI, catálogos de clientes, UGC) que já é o núcleo operacional do negócio. Fazer isso via script simples e sequencial também não resolve — falha no meio de um lote de 200 prompts custa o lote inteiro, porque o site é pesado, tem geração lenta e ambígua, e está sujeito a anti-bot.

## 3. Objetivo

Processar uma fila de prompts de vídeo de forma durável — cada prompt tem seu progresso persistido, então uma falha (crash, travamento de página, logout) nunca custa o trabalho já feito, apenas retoma do ponto certo.

## 4. Fora de escopo (v1)

- Não usa API oficial da Adobe (Firefly Services) — é automação de UI, por decisão explícita do projeto.
- Não faz login automático com credencial/MFA — sessão é sempre logada manualmente antes de rodar.
- Não roda múltiplas contas no mesmo processo. Uma conta pode usar várias abas isoladas dentro de um único contexto persistente e coordenador.
- Não inclui geração/curadoria dos prompts em si (isso é responsabilidade de outro estágio do pipeline).
- Não contorna captcha/bot-detection de forma ativa — apenas detecta e pausa.

## 5. Usuário e contexto de uso

Usuária única (Brenda), operando a ferramenta como parte do pipeline de produção de conteúdo (DigiFluencer e clientes). Uso esperado: alimentar um lote de prompts, deixar rodando (headed, máquina própria) por um período de horas, e checar o resultado depois — com pausas automáticas em qualquer situação que exija decisão humana.

## 6. Requisitos funcionais

### RF1 — Fila de prompts
O sistema aceita uma lista de prompts de entrada e processa um por vez, na ordem, mantendo o status de cada um (pendente, em andamento, concluído, falho) persistido em disco.

### RF2 — Verificação de sessão
Antes de cada prompt, o sistema confirma que a sessão do Firefly está ativa. Se não estiver, pausa a fila inteira e sinaliza a necessidade de login manual — nunca tenta autenticar sozinho.

### RF3 — Geração de vídeo
Para cada prompt pendente, o sistema insere o texto no campo de geração de vídeo do Firefly, aciona a geração e aguarda a conclusão, usando verificação ativa de estado da tela (não tempo fixo).

### RF4 — Classificação de resultado
Ao final da espera, o sistema determina o resultado real da geração — sucesso, rejeição de conteúdo, erro técnico, ou estado desconhecido — e nunca assume sucesso apenas pela ausência de indicador de carregamento.

### RF5 — Download do vídeo
Em caso de sucesso, o sistema baixa o arquivo gerado, valida que o arquivo tem conteúdo real (não é vazio/corrompido), e registra o caminho local.

### RF6 — Tratamento de rejeição de conteúdo
Prompts rejeitados pelo filtro de conteúdo do Firefly são marcados numa categoria própria e não reprocessados automaticamente — ficam disponíveis para revisão ou reescrita manual.

### RF7 — Pausa em situações de bloqueio
O sistema detecta sinais de limite de quota/créditos e de possível bloqueio/anti-bot, e pausa a fila inteira nesses casos, em vez de insistir.

### RF8 — Recuperação após interrupção
Se o processo for interrompido no meio (crash, travamento, encerramento manual), ao ser retomado o sistema identifica prompts que ficaram "presos" em andamento e os recoloca na fila, sem duplicar trabalho já concluído.

### RF9 — Vigilância de travamento
Se a página parar de responder sem gerar nenhum erro visível, o sistema detecta a inatividade prolongada, reinicia o navegador e retoma a fila do ponto correto.

### RF10 — Registro e diagnóstico
Cada evento relevante (início, sucesso, falha, pausa, motivo) é registrado com timestamp, e falhas geram uma captura de tela para diagnóstico posterior.

## 7. Requisitos não-funcionais

| # | Requisito |
|---|---|
| RNF1 | Execução com interface visível (não headless) — requisito do próprio site alvo |
| RNF2 | Ritmo de execução com variação (não fixo) entre gerações, para reduzir risco de detecção como automação |
| RNF3 | Teto configurável de gerações por período de tempo |
| RNF4 | Persistência local, sem dependência de rede externa para continuar operando |
| RNF5 | Tolerância a demora — timeouts de espera dimensionados para minutos, não segundos |
| RNF6 | Nenhuma credencial de login é armazenada ou manipulada pelo sistema |

## 8. Critérios de sucesso

- Um lote de N prompts processa até o fim sem intervenção manual, exceto nos casos legítimos de pausa (login, quota, bloqueio).
- Uma interrupção no meio do lote (fechar o processo à força) não causa perda de prompts já concluídos nem duplicação de trabalho ao retomar.
- Nenhum prompt rejeitado ou com erro é reportado como sucesso.
- Todo arquivo baixado e marcado como concluído é um vídeo válido, não um arquivo vazio ou corrompido.

## 9. Riscos conhecidos

| Risco | Impacto | Mitigação |
|---|---|---|
| Expiração de sessão no meio do lote | Alto | Pausa automática + alerta, retomada manual |
| Detecção de automação / captcha | Alto | Ritmo humano, teto de volume, pausa em sinal ambíguo — sem garantia de contorno |
| Mudança de interface do Firefly | Médio | Seletores centralizados, log específico de qual ponto quebrou |
| Estouro de quota/créditos no meio do lote | Médio | Detecção de estado + pausa limpa, sem perda de progresso |
| Página travada sem erro visível | Médio | Vigilância externa por tempo-limite, reinício forçado |

## 10. Dependências

- Conta Adobe Firefly ativa, com créditos de geração de vídeo disponíveis.
- Sessão logada manualmente antes da execução.
- Máquina com interface gráfica disponível para rodar o navegador (local ou display virtual, se em servidor).

## 11. Roadmap (pós-v1, não escopo desta entrega)

- Reescrita automática de prompts rejeitados por filtro de conteúdo.
- Painel de acompanhamento remoto do andamento da fila.
- Suporte a múltiplas contas em paralelo, cada uma com sua própria fila serial.
