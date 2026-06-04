# graphql-api — Guia para Claude

GraphQL API unificada do Destaques Gov.BR. Substitui a colcha de retalhos REST/Firestore Admin/Typesense direta que o portal usava em parts. Single endpoint, schema tipado (Strawberry), context com JWT validado, datasources separados por backend.

**Stack:** Python 3.12 · FastAPI · Strawberry GraphQL · Uvicorn · asyncpg · firebase-admin · typesense-python · PyJWT.

**Deploy:** Cloud Run (`destaquesgovbr-graphql-api`). Imagem buildada por CI; env vars vêm do Terraform (`infra/terraform/graphql-api.tf`).

## Layout

```
graphql-api/
├── src/graphql_api/
│   ├── app.py              # create_app() — FastAPI + GraphQLRouter + /graphql/stream (SSE)
│   ├── lifespan.py         # startup: instancia datasources a partir de env vars
│   ├── context.py          # get_context(): valida JWT, popula ctx.user, injeta datasources
│   ├── auth/jwt.py         # JWKS fetch + valida JWT (Keycloak realm)
│   ├── dataloaders.py      # Strawberry DataLoaders (batch + cache por request)
│   ├── datasources/
│   │   ├── firestore.py    # FirestoreDatasource — clippings, users, marketplace
│   │   ├── postgres.py     # PostgresDatasource — themes, agencies
│   │   ├── typesense.py    # read-only — articles, search
│   │   └── typesense_admin.py  # writes (indexação)
│   ├── schema/             # Strawberry types + resolvers, organizados por feature
│   └── lib/                # helpers internos
└── tests/                  # pytest + respx (mocks de HTTP) + pytest-asyncio
```

## Comandos principais

```bash
make help              # lista targets
make bootstrap-env     # regenera .env.local puxando secrets do GCP
make dev               # uvicorn local em :8000 com .env.local carregado
make test              # pytest
make lint              # ruff check
make format            # ruff format
```

## Desenvolvimento local

Modo padrão para iterar sem redeploy. Os datasources falam com Firestore/Typesense/Postgres reais do GCP, autenticação por JWT validado contra o Keycloak Cloud Run. Ver também `portal/CLAUDE.md` seção "Modo dev local com graphql-api real" para o lado portal.

### Pré-requisitos

```bash
# Application Default Credentials — firebase-admin usa para Firestore
gcloud auth application-default login

# Venv + deps (idempotente)
make .venv  # ou apenas `make dev` que cria sob demanda
```

Sua conta GCP precisa de `roles/secretmanager.secretAccessor` em `inspire-7-finep` para `make bootstrap-env` ler `typesense-{read,write}-conn` e `govbrnews-postgres-connection-string`.

### Subir o servidor

```bash
make bootstrap-env  # uma vez (e sempre que um secret rotacionar)
make dev            # uvicorn --reload em :8000
```

`make dev` carrega `.env.local` via `set -a; . ./.env.local; set +a` antes de invocar o uvicorn — não usamos `python-dotenv`, mantendo o módulo agnóstico de fonte de env vars (Cloud Run injeta direto via Terraform; local injeta via shell).

### Variáveis de ambiente (todas lidas via `os.environ`)

| Var | Datasource | Origem em dev |
|-----|-----------|----------------|
| `GCP_PROJECT_ID` | Firestore (ADC) | hardcoded `inspire-7-finep` |
| `TYPESENSE_READ_CONN` | TypesenseDatasource | secret `typesense-read-conn` (JSON: host/port/protocol/apiKey) |
| `TYPESENSE_WRITE_CONN` | TypesenseAdminDatasource | secret `typesense-write-conn` |
| `DATABASE_URL` | PostgresDatasource (asyncpg) | secret `govbrnews-postgres-connection-string` |
| `AUTH_JWKS_URL` | validação JWT inbound | Keycloak `/realms/destaquesgovbr/protocol/openid-connect/certs` |
| `AUTH_ISSUER` | validação JWT inbound | Keycloak `/realms/destaquesgovbr` |
| `CORS_ALLOW_ORIGINS` | CORS middleware | `http://localhost:3000` em dev; restrita em prod |

Datasource ausente vira `app.state.<ds>=None` (warning no log); resolvers que dependem retornam erro de runtime. Permite subir o app mesmo sem todas as integrações configuradas.

### Smoke checks

```bash
curl -sf http://localhost:8000/health
# {"status":"ok"}

curl -sf -X POST http://localhost:8000/graphql -H "Content-Type: application/json" \
  -d '{"query":"{ themes { code label } }"}'
# 25 themes vindos do Postgres do govbrnews

curl -sf -X POST http://localhost:8000/graphql -H "Content-Type: application/json" \
  -d '{"query":"{ articles(page: 1, limit: 2) { articles { uniqueId title } found } }"}'
# bate no Typesense; found = total no índice (~117k)
```

## Troubleshooting

**`Address already in use` no :8000** — algum processo do dia anterior. `lsof -nP -iTCP:8000 -sTCP:LISTEN` revela; mate com `kill <PID>`.

**`google.auth.exceptions.DefaultCredentialsError`** — ADC ausente/expirado. `gcloud auth application-default login`.

**`falha ao construir typesense.Client`** — `TYPESENSE_READ_CONN` mal-formado. Re-rode `make bootstrap-env`.

**`AUTH_JWKS_URL ausente — validacao JWT desabilitada`** — só warning; queries públicas funcionam mas qualquer query autenticada retorna `user=None`. Garanta que `.env.local` tem `AUTH_JWKS_URL`.

**Resolver retorna `'NoneType' object has no attribute ...`** — o datasource que esse resolver precisa não subiu (env var ausente ou inválida). Olhe o log do lifespan startup pra ver qual.

## Validação E2E (Playwright via portal)

O gate de validação **real** do graphql-api é a suíte E2E que vive no portal
(`portal/e2e/graphql/`), não `curl` headless. Curl bate só no graphql-api e
mascara o caminho real **browser → portal → graphql-api**; foi assim que um
drift sistêmico de schema (portal usando operations que o schema não expõe)
passou despercebido na R1. Catálogo do drift: `_plan/R1-DRIFT-CATALOG.md`.

### Como a suíte exercita este serviço

- As fixtures (`portal/e2e/fixtures/`) falam direto no `/graphql` deste serviço
  com `Authorization: Bearer <JWT>` do bot `e2e-bot@destaquesgovbr.gov.br`,
  obtido via Direct Access Grant no Keycloak Cloud Run (client `portal-e2e`).
  O JWT é validado aqui contra o JWKS do **mesmo** realm (`destaquesgovbr`).
- Os specs dirigem a UI do portal (que chama este serviço) e, além disso,
  conferem o estado final no backend via GraphQL direto (asserção forte).
- `seed.ts` faz pré-flight: `themes` (Postgres) e `articles` (Typesense) têm
  que responder, senão falha com mensagem acionável (sem `test.skip` mudo).

### Subir para rodar os testes

```bash
# Terminal A — este serviço em :8000
make dev

# Terminal B — portal + suíte (ver portal/CLAUDE.md › "Suíte E2E GraphQL local")
cd ../portal
source scripts/e2e/load-creds.sh    # E2E_BOT_PASSWORD + AUTH_SECRET
pnpm dev                            # portal em :3000 (outro terminal)
PLAYWRIGHT_BASE_URL=http://localhost:3000 \
KC_URL=https://destaquesgovbr-keycloak-klvx64dufq-rj.a.run.app \
  pnpm exec playwright test e2e/graphql --project=chromium-authed --project=chromium
```

Override opcional: `E2E_GRAPHQL_URL` (default `http://localhost:8000/graphql`)
aponta as fixtures para outra instância deste serviço.

### Implicação para mudanças de schema

Qualquer alteração de schema aqui (renomear campo, mudar arg, scalar) **quebra
o portal** se as operations dele não forem atualizadas em conjunto. Rode
`e2e/graphql` localmente antes de mergear mudanças de schema. O scalar de IDs
neste serviço é `String` (não `ID`); resolvers de id usam `str`.

## Convenções

- **Idioma:** comentários e mensagens de log em português; identificadores em inglês.
- **Commits:** português, sem `Co-Authored-By`. Prefixos `feature:` / `fix:` / `refactor:` / `docs:` / `chore:`.
- **Branch flow:** `feat/*` → `main` (não há `development` neste repo — diferente do portal).
- **Async/sync mix:** resolvers podem ser sync ou async; `PostgresDatasource` é async (asyncpg), `TypesenseDatasource` e `FirestoreDatasource` são sync (libs upstream sync). Strawberry lida com ambos — não force `async` sem razão.
- **Falha silenciosa de datasources no startup:** padrão deliberado (ver `lifespan.py`). Não troque por hard-fail.
