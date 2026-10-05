# ADR-0002: SQLite com WAL mode como Job Store

**Data:** 21/07/2026
**Status:** Aceito

---

## Contexto

O sistema precisa de persistencia duravel para a fila de jobs. Cada prompt e um job com estado (`pending -> claimed -> generating -> done/failed`). Se o processo morrer no meio de um lote de 200 prompts, o sistema retoma do ponto certo sem perda nem duplicacao.

Requisitos:
- Atomicidade em transicoes de estado (CAS — Compare-And-Swap)
- Tolerancia a crash (durabilidade em disco)
- Leitura concorrente pelo watchdog (processo separado)
- Sem dependencia de infraestrutura externa (RNF4)
- Migracao de schema versionada e manual (AI Guard 1.5)

## Decisao

Usar **SQLite** com **WAL mode** via `sqlite3` da stdlib (sem ORM).

### Pragmas obrigatorios no boot de toda conexao:

```sql
PRAGMA journal_mode=WAL;       -- Reads concorrentes durante writes
PRAGMA synchronous=NORMAL;     -- Balanceia durabilidade e velocidade
PRAGMA temp_store=MEMORY;      -- Temp tables em RAM
PRAGMA busy_timeout=5000;      -- Espera 5s antes de SQLITE_BUSY
PRAGMA foreign_keys=ON;        -- Integridade referencial
```

### Schema da tabela `jobs`:

| Campo | Tipo | Descricao |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | Identificador do job |
| prompt | TEXT NOT NULL | Texto de entrada |
| status | TEXT NOT NULL DEFAULT pending | Estado atual |
| attempts | INTEGER DEFAULT 0 | Contador de tentativas |
| output_path | TEXT | Caminho do arquivo baixado |
| error | TEXT | Ultima mensagem de erro |
| claimed_at | REAL | Timestamp de claim |
| generation_started_at | REAL | Timestamp de inicio de geracao (distinto de claimed_at) |
| updated_at | REAL | Ultima atualizacao |

### Estados validos:

```
pending -> claimed -> generating -> done
                       -> stale_generating -> pending (apos expirar budget)
                                  -> failed-content
                                  -> failed-infra
                                  -> dead
```

## Alternativas consideradas

| Alternativa | Por que descartada |
|---|---|
| JSON em arquivo | Sem atomicidade, sem CAS, corrompe em crash parcial |
| PostgreSQL | Infraestrutura externa, viola RNF4 (persistencia local sem rede) |
| Redis | Volatil por padrao, persistencia RDB/AOF e fragil, overhead |
| SQLAlchemy + SQLite | ORM adiciona abstracao desnecessaria, esconde SQL, dificulta CAS atomico |
| Supabase | Dependencia de rede (RNF4), ja previsto no roadmap pos-v1 como espelhamento |

## Consequencias

**Positivas:**
- ACID nativo, sem configuracao
- WAL mode permite reads concorrentes do watchdog durante writes do worker
- Sem infraestrutura externa — funciona offline
- UPDATE ... WHERE status=? RETURNING ... faz CAS atomico no SQL
- Arquivo unico, facil backup

**Negativas:**
- Single-writer — so um processo escreve por vez (aceitavel: worker unico)
- Sem replicacao — se o disco corromper, perde tudo (mitigacao: backup manual do .db)
- Sem visibilidade remota — roadmap pos-v1 prevê espelhamento para Supabase

## Edge case: reconciliacao com geracao ativa

O campo `generation_started_at` (distinto de `claimed_at`) resolve o edge case onde:
1. Job marcado generating ha 2 min
2. Budget e 10 min
3. Processo morre
4. Se volta pra pending imediatamente, Firefly ainda pode estar gerando -> geracao duplicada

Solucao: jobs dentro do budget viram `stale_generating` (nao voltam pra pending). So expirados voltam pra pending.
