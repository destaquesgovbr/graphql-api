# Plano: Adoção de GraphQL no Destaques Gov.BR

## Contexto

O projeto possui múltiplas camadas de dados desconexas: o portal Next.js acessa Typesense diretamente via Server Actions, mantém 25+ rotas REST fragmentadas para clippings/marketplace/widgets, e os workers Python acessam PostgreSQL diretamente sem camada de API unificada. Isso gera duplicação de lógica, over-fetching, tipos desalinhados entre backend/frontend, e 6+ serviços com connection strings independentes ao mesmo banco.

O objetivo: criar uma camada GraphQL unificada **em Python** (stack principal do projeto) que serve tanto o portal quanto os workers internos, seguindo TDD incremental — **tests first, cada passo verde antes do próximo**.

---

## Mapeamento: O Que Existe Hoje

### Portal (Next.js/TypeScript) — Fontes de dados

| Camada | Origem | Padrão atual |
|--------|--------|--------------|
| Artigos (listagem, busca, homepage) | Typesense SDK direto | Server Actions |
| Temas, Agências, Tags | Typesense facets | Server Actions |
| Clippings do usuário | Firestore direto | REST `/api/clipping/*` |
| Marketplace de clippings | Firestore direto | REST `/api/clippings/public/*` |
| Analytics editorial | Typesense facets agregados | Server Actions |
| Widgets (embed externo) | Typesense + configuração | REST `/api/widgets/*` |
| Embeddings (busca semântica) | Embeddings API externa | fetch() em Server Action |
| Feeds (RSS, JSON, Atom) | Typesense | Route handlers (mantém como está) |
| Push notifications prefs | Firestore | REST `/api/push/*` |

### Workers/DAGs (Python) — Acesso a dados

| Worker/DAG | Leitura | Escrita |
|------------|---------|---------|
| Typesense Sync Worker | PG (JOIN news+themes+features+embeddings) | Typesense upsert |
| Feature Worker | PG (news por unique_id) | PG news_features (JSONB merge) |
| Bronze Writer | PG (news+themes por unique_id) | GCS bronze layer |
| DAG sync_pg_to_bigquery | PG (news+themes+features JOIN) | GCS parquet + BigQuery |
| DAG aggregate_engagement | BigQuery (umami_pageviews) | PG news_features |
| DAG compute_trending | BigQuery (fato_noticias) | PG news_features |
| DAG compute_clusters | PG (pgvector similarity) | PG news_features |
| DAG verify_news_integrity | PG (news+features) | PG news_features + Typesense |

### Onde NÃO faz sentido GraphQL

| O que | Por quê não |
|-------|-------------|
| Workers POST /process (Pub/Sub push) | Event-driven, ponto de entrada mantém HTTP |
| Feeds RSS/JSON/Atom | Formato específico, não query language |
| Endpoints NextAuth (/api/auth/*) | OAuth redirect flows |
| Bronze Writer write (GCS) | Storage direto, sem query |

---

## Arquitetura Alvo

```
┌─────────────────────────────────────────────────────────────┐
│                     PORTAL (Next.js/TS)                      │
│  Server Actions + Client Components                          │
│  └─> graphql-request (server) / urql (client)               │
└────────────────────────────┬────────────────────────────────┘
                             │ POST /graphql (JWT auth)
                             ▼
┌─────────────────────────────────────────────────────────────┐
│        GRAPHQL API SERVICE (Cloud Run) — Python              │
│        destaquesgovbr-graphql-api                            │
│                                                              │
│  FastAPI + Strawberry GraphQL                               │
│                                                              │
│  ┌─ Public Resolvers ──────────────────────────────────┐    │
│  │  articles, search, themes, agencies, tags            │    │
│  │  analytics, widgets, marketplace (read)              │    │
│  └──────────────────────────────────────────────────────┘    │
│  ┌─ Authenticated Resolvers (JWT) ─────────────────────┐    │
│  │  clippings CRUD, marketplace mutations               │    │
│  │  push preferences                                    │    │
│  └──────────────────────────────────────────────────────┘    │
│  ┌─ Internal Resolvers (service account OIDC) ─────────┐    │
│  │  newsById, newsBatch, upsertFeatures                 │    │
│  │  newsForTypesense, newsForBigQuery                   │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  DataLoaders: themes, agencies (batch + per-request cache)  │
└──┬──────────────┬──────────────┬────────────────────────────┘
   │              │              │
   ▼              ▼              ▼
Typesense     Firestore     PostgreSQL
(search)      (users)       (news, features)

┌─────────────────────────────────────────────────────────────┐
│  WORKERS (Python/Cloud Run) + DAGs (Airflow)                 │
│  POST /process (Pub/Sub) → chama GraphQL internal API       │
│                                                              │
│  graphql_client.py (httpx + gql)                            │
│  Feature Worker:    mutation upsertFeatures(...)             │
│  Typesense Sync:    query newsForTypesense(uniqueId)        │
│  Bronze Writer:     query newsById(uniqueId)                │
│  DAG BQ Sync:       query newsBatchForBigQuery(dateRange)   │
│  DAG Engagement:    mutation batchUpsertFeatures(...)        │
│  DAG Trending:      mutation batchUpsertFeatures(...)        │
│  DAG Clusters:      query similarArticles(...) +            │
│                     mutation batchUpsertFeatures(...)        │
│  DAG Integrity:     mutation batchUpsertFeatures(...)        │
└─────────────────────────────────────────────────────────────┘
```

---

## Schema GraphQL Completo

### Types

```graphql
type Article {
  uniqueId: ID!
  title: String!
  url: String
  image: String
  videoUrl: String
  content: String
  summary: String
  subtitle: String
  editorialLead: String
  category: String
  tags: [String!]
  agency: String
  agencyName: String
  publishedAt: DateTime!
  extractedAt: DateTime
  theme: ThemeHierarchy
  features: ArticleFeatures
}

type ThemeHierarchy {
  level1: ThemeLevel
  level2: ThemeLevel
  level3: ThemeLevel
  mostSpecific: ThemeLevel
}

type ThemeLevel { code: String!, label: String! }

type ArticleFeatures {
  wordCount: Int
  charCount: Int
  paragraphCount: Int
  hasImage: Boolean
  hasVideo: Boolean
  imageBroken: Boolean
  readabilityFlesch: Float
  sentimentLabel: String
  sentimentScore: Float
  trendingScore: Float
  viewCount: Int
  uniqueSessions: Int
  similarArticles: [SimilarArticle!]
}

type SimilarArticle { uniqueId: ID!, similarity: Float! }
type ArticlesResult { articles: [Article!]!, page: Int!, found: Int! }
```

### Queries — Portal (público + autenticado)

```graphql
type Query {
  articles(filter: ArticleFilter, page: Int, limit: Int): ArticlesResult!
  article(uniqueId: ID!): Article
  search(query: String!, filter: ArticleFilter, page: Int, semantic: Boolean): ArticlesResult!
  searchSuggestions(query: String!): [SearchSuggestion!]!
  themes: [Theme!]!
  agencies: [Agency!]!
  popularTags(limit: Int): [String!]!
  analyticsKpis(range: DateRange!): AnalyticsKpis!
  topThemes(range: DateRange!, limit: Int): [ThemeStats!]!
  topAgencies(range: DateRange!, limit: Int): [AgencyStats!]!
  articlesTimeline(range: DateRange!): [DailyCount!]!
  clippings: [Clipping!]!           # autenticado
  clipping(id: ID!): Clipping        # autenticado
  clippingEstimate(recortes: [RecorteInput!]!): EstimateResult!
  marketplaceListings(page: Int): MarketplaceListingsResult!
  marketplaceListing(id: ID!): MarketplaceListing
  widgetArticles(config: WidgetConfigInput!, page: Int): WidgetArticlesResult!
  widgetConfig: WidgetConfig!
}
```

### Queries — Internal (service account, workers/DAGs)

```graphql
extend type Query {
  newsById(uniqueId: ID!): NewsRecord                     # @internal
  newsBatch(uniqueIds: [ID!]!): [NewsRecord!]!            # @internal
  newsForTypesense(uniqueId: ID!): TypesenseDocument       # @internal
  newsBatchForBigQuery(startDate: DateTime!, endDate: DateTime!, batchSize: Int): [BigQueryRecord!]!
  similarArticles(uniqueId: ID!, threshold: Float, limit: Int): [SimilarArticle!]!
  integrityBatch(batchSize: Int): [IntegrityCandidate!]!
}
```

### Mutations — Portal (autenticado)

```graphql
type Mutation {
  createClipping(input: ClippingInput!): Clipping!
  updateClipping(id: ID!, input: ClippingInput!): Clipping!
  deleteClipping(id: ID!): Boolean!
  sendClipping(id: ID!): Boolean!
  publishToMarketplace(clippingId: ID!, input: PublishInput!): MarketplaceListing!
  unpublishFromMarketplace(listingId: ID!): Boolean!
  likeMarketplaceListing(listingId: ID!): MarketplaceListing!
  followMarketplaceListing(listingId: ID!): Clipping!
  cloneMarketplaceListing(listingId: ID!): Clipping!
  syncPushSubscription(subscription: PushSubscriptionInput!): Boolean!
  updatePushPreferences(preferences: PushPreferencesInput!): Boolean!
}
```

### Mutations — Internal (service account)

```graphql
extend type Mutation {
  upsertFeatures(uniqueId: ID!, features: JSON!): Boolean!          # @internal
  batchUpsertFeatures(items: [FeatureUpsertInput!]!): BatchResult!  # @internal
  updateTypesenseField(uniqueId: ID!, field: String!, value: JSON!): Boolean!  # @internal
}
```

---

## Stack Tecnológica

| Componente | Tecnologia | Motivo |
|-----------|-----------|--------|
| **Server** | FastAPI + Strawberry GraphQL | Stack Python do projeto, async nativo, type-safe |
| **Schema** | Strawberry (code-first) | Dataclasses Python → schema GraphQL, zero SDL manual |
| **Test runner** | pytest + pytest-asyncio | Stack padrão Python do projeto |
| **Test fixtures** | factory-boy + Faker | Fixtures tipadas para artigos, clippings, etc. |
| **Datasource Typesense** | typesense SDK (Python) | Reutiliza SDK existente do data-platform |
| **Datasource Firestore** | firebase-admin (Python) | SDK Google nativo |
| **Datasource PostgreSQL** | asyncpg (async) | Pool async, performance superior ao psycopg2 sync |
| **DataLoader** | strawberry.dataloader | Built-in, integrado com context |
| **Auth (portal)** | PyJWT + jwcrypto (JWKS) | Verifica tokens NextAuth |
| **Auth (internal)** | google-auth (OIDC) | Verifica tokens de service account Cloud Run |
| **HTTP client (workers)** | httpx + gql | Async GraphQL client para Python |
| **Cliente portal (server)** | graphql-request (TS) | Leve para Server Actions |
| **Cliente portal (client)** | urql (TS) | Leve, cache normalizado, React bindings |
| **Codegen portal** | @graphql-codegen/cli | Gera tipos TS do schema Python |

---

## Fases de Implementação (TDD Incremental)

### Fase 1 — Fundação: Projeto, Schema Base e Health Check
**Objetivo**: Projeto Python rodando com CI verde, schema vazio compilando, health check respondendo.

**Tests first:**
```python
# tests/test_setup.py
def test_app_creates_without_error():
    from graphql_api.app import create_app
    app = create_app()
    assert app is not None

# tests/test_health.py
@pytest.mark.asyncio
async def test_health_endpoint(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

# tests/test_schema.py
@pytest.mark.asyncio
async def test_introspection_query(client):
    response = await client.post("/graphql", json={
        "query": "{ __schema { queryType { name } } }"
    })
    assert response.status_code == 200
    assert response.json()["data"]["__schema"]["queryType"]["name"] == "Query"
```

**Implementação:**
1. Criar `graphql-api/` com `pyproject.toml` (uv/poetry), `pytest.ini`
2. `src/graphql_api/app.py` — FastAPI app factory + Strawberry mount + `/health`
3. `src/graphql_api/schema/__init__.py` — Strawberry schema (Query placeholder)
4. `src/graphql_api/context.py` — Context dataclass (sem auth ainda)
5. `tests/conftest.py` — Fixture `client` com httpx.AsyncClient + TestClient
6. `Dockerfile`
7. CI: `pytest` no GitHub Actions

**Verificação:** `pytest` → 3 testes verdes. `uvicorn graphql_api.app:create_app --factory` → Playground abre em localhost:8000/graphql.

**Arquivos:**
- `graphql-api/pyproject.toml`
- `graphql-api/src/graphql_api/app.py`
- `graphql-api/src/graphql_api/schema/__init__.py`
- `graphql-api/src/graphql_api/context.py`
- `graphql-api/tests/conftest.py`
- `graphql-api/tests/test_setup.py`
- `graphql-api/tests/test_health.py`
- `graphql-api/tests/test_schema.py`
- `graphql-api/Dockerfile`

---

### Fase 2 — Datasource Typesense + Query `articles`
**Objetivo**: Primeiro resolver real conectando ao Typesense.

**Tests first:**
```python
# tests/datasources/test_typesense.py
@pytest.mark.asyncio
async def test_search_articles_returns_typed_list(mock_typesense):
    ds = TypesenseDatasource(mock_typesense)
    result = await ds.search_articles(page=1, limit=10)
    assert len(result.articles) <= 10
    assert all(isinstance(a.unique_id, str) for a in result.articles)

@pytest.mark.asyncio
async def test_search_articles_filters_by_agency(mock_typesense):
    ds = TypesenseDatasource(mock_typesense)
    result = await ds.search_articles(agencies=["mec"], page=1)
    # Verify filter_by was called with "agency:=mec"
    mock_typesense.collections["news"].documents.search.assert_called_once()
    call_args = mock_typesense.collections["news"].documents.search.call_args
    assert "agency:=mec" in call_args[1]["filter_by"]

@pytest.mark.asyncio
async def test_search_articles_date_range(mock_typesense):
    ds = TypesenseDatasource(mock_typesense)
    result = await ds.search_articles(start_date=1700000000, end_date=1700100000)
    call_args = mock_typesense.collections["news"].documents.search.call_args
    assert "published_at:>=" in call_args[1]["filter_by"]

# tests/resolvers/test_articles.py
@pytest.mark.asyncio
async def test_articles_query_returns_correct_shape(gql_client):
    result = await gql_client.execute("""
        query { articles(page: 1) { articles { uniqueId title agency } found } }
    """)
    assert "articles" in result.data
    assert "found" in result.data["articles"]

@pytest.mark.asyncio
async def test_article_by_id(gql_client):
    result = await gql_client.execute("""
        query { article(uniqueId: "abc123") { title content } }
    """)
    assert result.data["article"] is not None

@pytest.mark.asyncio
async def test_article_not_found_returns_null(gql_client):
    result = await gql_client.execute("""
        query { article(uniqueId: "nonexistent") { title } }
    """)
    assert result.data["article"] is None

@pytest.mark.asyncio
async def test_articles_pagination(gql_client):
    page1 = await gql_client.execute('query { articles(page:1, limit:5) { articles { uniqueId } } }')
    page2 = await gql_client.execute('query { articles(page:2, limit:5) { articles { uniqueId } } }')
    ids1 = {a["uniqueId"] for a in page1.data["articles"]["articles"]}
    ids2 = {a["uniqueId"] for a in page2.data["articles"]["articles"]}
    assert ids1.isdisjoint(ids2)
```

**Implementação:**
1. `src/graphql_api/datasources/typesense.py` — TypesenseDatasource class (search, get_by_id)
2. `src/graphql_api/schema/types/article.py` — Strawberry types: `Article`, `ArticlesResult`, `ArticleFilter`
3. `src/graphql_api/schema/resolvers/articles.py` — Resolvers `articles`, `article`

**Verificação:** `pytest` → todos verdes. Playground: `{ articles(page:1) { articles { title } } }`.

**Arquivos:**
- `graphql-api/src/graphql_api/datasources/typesense.py`
- `graphql-api/src/graphql_api/schema/types/article.py`
- `graphql-api/src/graphql_api/schema/resolvers/articles.py`
- `graphql-api/tests/datasources/test_typesense.py`
- `graphql-api/tests/resolvers/test_articles.py`

---

### Fase 3 — Busca: Keyword + Semântica
**Objetivo**: Resolver `search` com suporte a busca híbrida.

**Tests first:**
```python
# tests/datasources/test_embeddings.py
@pytest.mark.asyncio
async def test_generate_embedding_returns_vector(mock_httpx):
    ds = EmbeddingsDatasource(api_url="http://test", api_key="key")
    result = await ds.generate_embedding("saúde")
    assert len(result) == 768
    assert all(isinstance(v, float) for v in result)

@pytest.mark.asyncio
async def test_embedding_api_unavailable_returns_none(mock_httpx_error):
    ds = EmbeddingsDatasource(api_url="http://test")
    result = await ds.generate_embedding("saúde")
    assert result is None  # fallback to keyword-only

# tests/resolvers/test_search.py
@pytest.mark.asyncio
async def test_keyword_search(gql_client):
    result = await gql_client.execute("""
        query { search(query: "vacinação", page: 1) { articles { title } found } }
    """)
    assert result.data["search"]["found"] >= 0

@pytest.mark.asyncio
async def test_semantic_search_calls_embeddings(gql_client, mock_embeddings):
    result = await gql_client.execute("""
        query { search(query: "vacinação", semantic: true, page: 1) { found } }
    """)
    mock_embeddings.generate_embedding.assert_called_once_with("vacinação")

@pytest.mark.asyncio
async def test_search_with_filters(gql_client):
    result = await gql_client.execute("""
        query { search(query: "vacinação", filter: {agencies: ["saude"]}, page: 1) { found } }
    """)
    assert "errors" not in result

@pytest.mark.asyncio
async def test_search_suggestions(gql_client):
    result = await gql_client.execute('query { searchSuggestions(query: "vac") { uniqueId title } }')
    assert isinstance(result.data["searchSuggestions"], list)

@pytest.mark.asyncio
async def test_empty_query_returns_error(gql_client):
    result = await gql_client.execute('query { search(query: "", page: 1) { found } }')
    assert result.errors is not None
```

**Implementação:**
1. `src/graphql_api/datasources/embeddings.py` — httpx async client wrapper
2. `src/graphql_api/schema/resolvers/search.py` — Resolvers `search`, `searchSuggestions`
3. Extender `datasources/typesense.py` com `hybrid_search(query, embedding, filters)`

**Verificação:** `pytest` verdes.

**Arquivos:**
- `graphql-api/src/graphql_api/datasources/embeddings.py`
- `graphql-api/src/graphql_api/schema/resolvers/search.py`
- `graphql-api/tests/datasources/test_embeddings.py`
- `graphql-api/tests/resolvers/test_search.py`

---

### Fase 4 — Metadados: Themes, Agencies, Tags + DataLoaders
**Objetivo**: Resolvers de metadados com DataLoader para batch loading.

**Tests first:**
```python
# tests/dataloaders/test_themes.py
@pytest.mark.asyncio
async def test_theme_dataloader_batches(mock_typesense):
    loader = create_theme_loader(mock_typesense)
    t1, t2, t3 = await asyncio.gather(
        loader.load("SAUDE"), loader.load("EDUCACAO"), loader.load("ECONOMIA")
    )
    # Deve ter feito uma única chamada ao Typesense, não 3
    assert mock_typesense.call_count == 1

@pytest.mark.asyncio
async def test_theme_dataloader_caches(mock_typesense):
    loader = create_theme_loader(mock_typesense)
    await loader.load("SAUDE")
    await loader.load("SAUDE")
    assert mock_typesense.call_count == 1

# tests/resolvers/test_metadata.py
@pytest.mark.asyncio
async def test_themes_query(gql_client):
    result = await gql_client.execute("query { themes { code label } }")
    assert len(result.data["themes"]) > 0

@pytest.mark.asyncio
async def test_agencies_query(gql_client):
    result = await gql_client.execute("query { agencies }")
    assert isinstance(result.data["agencies"], list)

@pytest.mark.asyncio
async def test_popular_tags(gql_client):
    result = await gql_client.execute("query { popularTags(limit: 10) }")
    assert len(result.data["popularTags"]) <= 10
```

**Implementação:**
1. `src/graphql_api/dataloaders.py` — Strawberry DataLoader factory (themes, agencies)
2. `src/graphql_api/schema/types/theme.py` — Types `Theme`, `ThemeHierarchy`
3. `src/graphql_api/schema/resolvers/metadata.py` — Resolvers `themes`, `agencies`, `popularTags`

**Verificação:** `pytest` verdes.

**Arquivos:**
- `graphql-api/src/graphql_api/dataloaders.py`
- `graphql-api/src/graphql_api/schema/types/theme.py`
- `graphql-api/src/graphql_api/schema/resolvers/metadata.py`
- `graphql-api/tests/dataloaders/test_themes.py`
- `graphql-api/tests/resolvers/test_metadata.py`

---

### Fase 5 — Auth Context (JWT + Service Account)
**Objetivo**: Duas camadas de auth — JWT público (NextAuth) e OIDC interno (Google service account).

**Tests first:**
```python
# tests/auth/test_jwt.py
@pytest.mark.asyncio
async def test_valid_jwt_populates_user(jwt_factory):
    token = jwt_factory(sub="user123", email="test@gov.br", roles=["user"])
    user = await verify_jwt(token, jwks_url="http://test/.well-known/jwks.json")
    assert user.id == "user123"
    assert user.email == "test@gov.br"

@pytest.mark.asyncio
async def test_expired_jwt_returns_none(jwt_factory):
    token = jwt_factory(sub="user123", exp=0)
    user = await verify_jwt(token, jwks_url="http://test/.well-known/jwks.json")
    assert user is None

@pytest.mark.asyncio
async def test_no_token_returns_none():
    user = await verify_jwt(None, jwks_url="http://test/.well-known/jwks.json")
    assert user is None

@pytest.mark.asyncio
async def test_malformed_token_returns_none():
    user = await verify_jwt("not.a.jwt", jwks_url="http://test/.well-known/jwks.json")
    assert user is None

# tests/auth/test_service_account.py
@pytest.mark.asyncio
async def test_valid_oidc_sets_service_account(oidc_factory):
    token = oidc_factory(audience="https://graphql-api.run.app")
    sa = await verify_service_account(token, audience="https://graphql-api.run.app")
    assert sa.is_service_account is True

@pytest.mark.asyncio
async def test_invalid_oidc_rejects():
    sa = await verify_service_account("bad-token", audience="https://graphql-api.run.app")
    assert sa is None

# tests/auth/test_guards.py
@pytest.mark.asyncio
async def test_authenticated_resolver_without_token(gql_client_anonymous):
    result = await gql_client_anonymous.execute("query { clippings { id } }")
    assert any(e["extensions"]["code"] == "UNAUTHENTICATED" for e in result.errors)

@pytest.mark.asyncio
async def test_internal_resolver_without_service_account(gql_client_user):
    result = await gql_client_user.execute('query { newsById(uniqueId: "abc") { uniqueId } }')
    assert any(e["extensions"]["code"] == "FORBIDDEN" for e in result.errors)

@pytest.mark.asyncio
async def test_public_resolver_without_token(gql_client_anonymous):
    result = await gql_client_anonymous.execute("query { articles(page: 1) { found } }")
    assert result.errors is None
```

**Implementação:**
1. `src/graphql_api/auth/jwt.py` — Verificação JWT via JWKS (PyJWT + jwcrypto)
2. `src/graphql_api/auth/service_account.py` — Verificação Google OIDC (google-auth)
3. `src/graphql_api/auth/guards.py` — Decorators/permissions Strawberry
4. Atualizar `context.py` — Popula `user` e `service_account` no context

**Verificação:** `pytest` verdes.

**Arquivos:**
- `graphql-api/src/graphql_api/auth/jwt.py`
- `graphql-api/src/graphql_api/auth/service_account.py`
- `graphql-api/src/graphql_api/auth/guards.py`
- `graphql-api/tests/auth/test_jwt.py`
- `graphql-api/tests/auth/test_service_account.py`
- `graphql-api/tests/auth/test_guards.py`

---

### Fase 6 — Analytics Resolvers
**Objetivo**: Resolvers de analytics com Typesense facets.

**Tests first:**
```python
# tests/resolvers/test_analytics.py
@pytest.mark.asyncio
async def test_analytics_kpis(gql_client):
    result = await gql_client.execute("""
        query { analyticsKpis(range: {days: 30}) { total activeThemes activeAgencies dailyAverage } }
    """)
    kpis = result.data["analyticsKpis"]
    assert kpis["total"] >= 0
    assert kpis["dailyAverage"] >= 0

@pytest.mark.asyncio
async def test_top_themes(gql_client):
    result = await gql_client.execute("""
        query { topThemes(range: {days: 7}, limit: 5) { label count } }
    """)
    assert len(result.data["topThemes"]) <= 5

@pytest.mark.asyncio
async def test_top_agencies(gql_client):
    result = await gql_client.execute("""
        query { topAgencies(range: {days: 7}, limit: 5) { name count } }
    """)
    assert len(result.data["topAgencies"]) <= 5

@pytest.mark.asyncio
async def test_articles_timeline(gql_client):
    result = await gql_client.execute("""
        query { articlesTimeline(range: {days: 30}) { date count } }
    """)
    assert len(result.data["articlesTimeline"]) > 0

@pytest.mark.asyncio
async def test_invalid_range_returns_error(gql_client):
    result = await gql_client.execute("""
        query { analyticsKpis(range: {days: 0}) { total } }
    """)
    assert result.errors is not None
```

**Implementação:**
1. `src/graphql_api/schema/types/analytics.py`
2. `src/graphql_api/schema/resolvers/analytics.py`
3. Extender `datasources/typesense.py` com `faceted_count`, `group_by_field`

**Verificação:** `pytest` verdes.

**Arquivos:**
- `graphql-api/src/graphql_api/schema/types/analytics.py`
- `graphql-api/src/graphql_api/schema/resolvers/analytics.py`
- `graphql-api/tests/resolvers/test_analytics.py`

---

### Fase 7 — Deploy Staging + Migração Portal (Artigos/Busca/Analytics)
**Objetivo**: Primeiro deploy real. Portal consome GraphQL para artigos, busca e analytics em staging.

**Tests first:**
```python
# tests/integration/test_portal_parity.py (no graphql-api)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_articles_parity_with_typesense_direct(typesense_client, gql_client_staging):
    """GraphQL retorna mesmos resultados que Typesense direto."""
    direct = typesense_client.collections["news"].documents.search({"q": "*", "per_page": 10})
    graphql = await gql_client_staging.execute('query { articles(page:1, limit:10) { found } }')
    assert graphql.data["articles"]["found"] == direct["found"]

@pytest.mark.integration
@pytest.mark.asyncio
async def test_latency_acceptable(gql_client_staging):
    """p95 ≤ 200ms para articles query."""
    import time
    latencies = []
    for _ in range(20):
        start = time.monotonic()
        await gql_client_staging.execute('query { articles(page:1, limit:10) { found } }')
        latencies.append(time.monotonic() - start)
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95)]
    assert p95 <= 0.200
```

**Implementação:**
1. `infra/terraform/graphql-api.tf` — Cloud Run service (Python image)
2. `infra/terraform/iam.tf` — Service account + roles (Secret Manager, Firestore, Cloud SQL)
3. Deploy staging via CI
4. `portal/src/services/graphql/client.ts` — graphql-request client (TS)
5. Migrar `portal/src/app/(public)/actions.ts` → GraphQL queries
6. Migrar `portal/src/app/(public)/artigos/actions.ts` → GraphQL
7. Migrar `portal/src/app/(public)/busca/actions.ts` → GraphQL
8. Migrar `portal/src/app/(analytics)/dados-editoriais/actions.ts` → GraphQL
9. Feature flag: `NEXT_PUBLIC_USE_GRAPHQL=true` para rollback seguro

**Verificação:** Portal staging end-to-end com GraphQL. Testes de integração e latência verdes.

**Arquivos modificados no portal:**
- `portal/src/services/graphql/client.ts` (novo)
- `portal/src/app/(public)/actions.ts`
- `portal/src/app/(public)/artigos/actions.ts`
- `portal/src/app/(public)/busca/actions.ts`
- `portal/src/app/(analytics)/dados-editoriais/actions.ts`

**Arquivos infra:**
- `infra/terraform/graphql-api.tf` (novo)
- `infra/terraform/iam.tf` (modificar)

---

### Fase 8 — Datasource Firestore + Clippings CRUD
**Objetivo**: Resolver clippings com Firestore como backend.

**Tests first:**
```python
# tests/datasources/test_firestore.py (com Firestore emulator)
@pytest.mark.asyncio
async def test_get_clippings(firestore_ds, seed_clippings):
    clippings = await firestore_ds.get_clippings(user_id="user1")
    assert len(clippings) == 3

@pytest.mark.asyncio
async def test_create_clipping(firestore_ds):
    clipping = await firestore_ds.create_clipping("user1", ClippingInput(name="Test", ...))
    assert clipping.id is not None
    assert clipping.name == "Test"

@pytest.mark.asyncio
async def test_update_clipping(firestore_ds, seed_clippings):
    updated = await firestore_ds.update_clipping("user1", "clip1", ClippingInput(name="Updated"))
    assert updated.name == "Updated"

@pytest.mark.asyncio
async def test_delete_clipping(firestore_ds, seed_clippings):
    await firestore_ds.delete_clipping("user1", "clip1")
    clippings = await firestore_ds.get_clippings("user1")
    assert all(c.id != "clip1" for c in clippings)

@pytest.mark.asyncio
async def test_max_10_clippings(firestore_ds, seed_10_clippings):
    with pytest.raises(MaxClippingsError):
        await firestore_ds.create_clipping("user1", ClippingInput(name="11th"))

# tests/resolvers/test_clippings.py
@pytest.mark.asyncio
async def test_clippings_unauthenticated(gql_client_anonymous):
    result = await gql_client_anonymous.execute("query { clippings { id } }")
    assert any(e["extensions"]["code"] == "UNAUTHENTICATED" for e in result.errors)

@pytest.mark.asyncio
async def test_clippings_authenticated(gql_client_user):
    result = await gql_client_user.execute("query { clippings { id name } }")
    assert result.errors is None
    assert isinstance(result.data["clippings"], list)

@pytest.mark.asyncio
async def test_create_clipping_mutation(gql_client_user):
    result = await gql_client_user.execute("""
        mutation { createClipping(input: {name: "Test", recortes: [], prompt: "test", ...}) { id name } }
    """)
    assert result.data["createClipping"]["name"] == "Test"

@pytest.mark.asyncio
async def test_delete_clipping_mutation(gql_client_user, seed_clipping):
    result = await gql_client_user.execute(f'mutation {{ deleteClipping(id: "{seed_clipping.id}") }}')
    assert result.data["deleteClipping"] is True

@pytest.mark.asyncio
async def test_send_clipping_calls_worker(gql_client_user, seed_clipping, mock_clipping_worker):
    await gql_client_user.execute(f'mutation {{ sendClipping(id: "{seed_clipping.id}") }}')
    mock_clipping_worker.assert_called_once()

@pytest.mark.asyncio
async def test_clipping_estimate(gql_client_user):
    result = await gql_client_user.execute("""
        query { clippingEstimate(recortes: [{title: "test", themes: ["SAUDE"]}]) { totalEstimate } }
    """)
    assert result.data["clippingEstimate"]["totalEstimate"] >= 0
```

**Implementação:**
1. `src/graphql_api/datasources/firestore.py` — CRUD async Firestore
2. `src/graphql_api/schema/types/clipping.py` — Strawberry types + inputs
3. `src/graphql_api/schema/resolvers/clippings.py` — Queries + mutations
4. `src/graphql_api/schema/validators/clipping.py` — Validação de negócio

**Verificação:** `pytest` com Firestore emulator → todos verdes.

**Arquivos:**
- `graphql-api/src/graphql_api/datasources/firestore.py`
- `graphql-api/src/graphql_api/schema/types/clipping.py`
- `graphql-api/src/graphql_api/schema/resolvers/clippings.py`
- `graphql-api/src/graphql_api/schema/validators/clipping.py`
- `graphql-api/tests/datasources/test_firestore.py`
- `graphql-api/tests/resolvers/test_clippings.py`

---

### Fase 9 — Marketplace Resolvers + Mutations
**Objetivo**: Consolidar as 8 rotas REST de marketplace.

**Tests first:**
```python
# tests/resolvers/test_marketplace.py
@pytest.mark.asyncio
async def test_marketplace_listings_paginated(gql_client):
    result = await gql_client.execute("""
        query { marketplaceListings(page: 1) { listings { id name } total } }
    """)
    assert "listings" in result.data["marketplaceListings"]

@pytest.mark.asyncio
async def test_marketplace_listing_detail(gql_client):
    result = await gql_client.execute('query { marketplaceListing(id: "x") { id recortes likeCount } }')
    assert result.data["marketplaceListing"] is not None

@pytest.mark.asyncio
async def test_listing_personalization_authenticated(gql_client_user):
    result = await gql_client_user.execute('query { marketplaceListing(id: "x") { hasLiked hasFollowed } }')
    assert "hasLiked" in result.data["marketplaceListing"]

@pytest.mark.asyncio
async def test_listing_no_personalization_anonymous(gql_client_anonymous):
    result = await gql_client_anonymous.execute('query { marketplaceListing(id: "x") { hasLiked } }')
    assert result.data["marketplaceListing"]["hasLiked"] is None

# tests/resolvers/test_marketplace_mutations.py
@pytest.mark.asyncio
async def test_publish_to_marketplace(gql_client_user, seed_clipping):
    result = await gql_client_user.execute(f"""
        mutation {{ publishToMarketplace(clippingId: "{seed_clipping.id}", input: {{...}}) {{ id }} }}
    """)
    assert result.data["publishToMarketplace"]["id"] is not None

@pytest.mark.asyncio
async def test_unpublish_only_owner(gql_client_other_user, seed_listing):
    result = await gql_client_other_user.execute(f"""
        mutation {{ unpublishFromMarketplace(listingId: "{seed_listing.id}") }}
    """)
    assert any(e["extensions"]["code"] == "FORBIDDEN" for e in result.errors)

@pytest.mark.asyncio
async def test_like_toggles(gql_client_user, seed_listing):
    r1 = await gql_client_user.execute(f'mutation {{ likeMarketplaceListing(listingId: "{seed_listing.id}") {{ likeCount }} }}')
    count1 = r1.data["likeMarketplaceListing"]["likeCount"]
    r2 = await gql_client_user.execute(f'mutation {{ likeMarketplaceListing(listingId: "{seed_listing.id}") {{ likeCount }} }}')
    count2 = r2.data["likeMarketplaceListing"]["likeCount"]
    assert count2 == count1 - 1  # toggle unlike

@pytest.mark.asyncio
async def test_clone_creates_independent_copy(gql_client_user, seed_listing):
    result = await gql_client_user.execute(f"""
        mutation {{ cloneMarketplaceListing(listingId: "{seed_listing.id}") {{ id clonedFrom }} }}
    """)
    assert result.data["cloneMarketplaceListing"]["clonedFrom"] == seed_listing.id
```

**Implementação:**
1. Extender `datasources/firestore.py` com marketplace ops
2. `src/graphql_api/schema/types/marketplace.py`
3. `src/graphql_api/schema/resolvers/marketplace.py`

**Verificação:** `pytest` → todos verdes.

**Arquivos:**
- `graphql-api/src/graphql_api/schema/types/marketplace.py`
- `graphql-api/src/graphql_api/schema/resolvers/marketplace.py`
- `graphql-api/tests/resolvers/test_marketplace.py`
- `graphql-api/tests/resolvers/test_marketplace_mutations.py`

---

### Fase 10 — Widgets + Push Notifications
**Objetivo**: Completar resolvers voltados ao portal.

**Tests first:**
```python
# tests/resolvers/test_widgets.py
@pytest.mark.asyncio
async def test_widget_config(gql_client):
    result = await gql_client.execute("query { widgetConfig { agencies themes } }")
    assert len(result.data["widgetConfig"]["agencies"]) > 0

@pytest.mark.asyncio
async def test_widget_articles(gql_client):
    result = await gql_client.execute("""
        query { widgetArticles(config: {agencies: ["mec"], layout: LIST}, page: 1) {
            articles { title } pagination { total hasMore }
        }}
    """)
    assert "articles" in result.data["widgetArticles"]

@pytest.mark.asyncio
async def test_widget_max_per_page_clamped(gql_client):
    result = await gql_client.execute("""
        query { widgetArticles(config: {articlesPerPage: 100}, page: 1) { pagination { total } } }
    """)
    # Deve clampar a 50 sem erro
    assert result.errors is None

# tests/resolvers/test_push.py
@pytest.mark.asyncio
async def test_sync_push_unauthenticated(gql_client_anonymous):
    result = await gql_client_anonymous.execute('mutation { syncPushSubscription(subscription: {...}) }')
    assert any(e["extensions"]["code"] == "UNAUTHENTICATED" for e in result.errors)

@pytest.mark.asyncio
async def test_sync_push_authenticated(gql_client_user):
    result = await gql_client_user.execute('mutation { syncPushSubscription(subscription: {...}) }')
    assert result.data["syncPushSubscription"] is True
```

**Implementação:**
1. `src/graphql_api/schema/types/widget.py`
2. `src/graphql_api/schema/resolvers/widgets.py`
3. `src/graphql_api/schema/types/push.py`
4. `src/graphql_api/schema/resolvers/push.py`

**Arquivos:**
- `graphql-api/src/graphql_api/schema/types/widget.py`
- `graphql-api/src/graphql_api/schema/resolvers/widgets.py`
- `graphql-api/src/graphql_api/schema/types/push.py`
- `graphql-api/src/graphql_api/schema/resolvers/push.py`
- `graphql-api/tests/resolvers/test_widgets.py`
- `graphql-api/tests/resolvers/test_push.py`

---

### Fase 11 — Migração Portal: Clippings, Marketplace, Push + Codegen
**Objetivo**: Portal 100% no GraphQL. Rotas REST removidas. Tipos gerados automaticamente.

**Tests first (portal, TS):**
```typescript
// portal/tests/integration/clippings.test.ts
test("criar clipping via GraphQL mutation", async () => { ... })
test("listar clippings via GraphQL query", async () => { ... })
test("deletar clipping via mutation", async () => { ... })

// portal/tests/integration/marketplace.test.ts
test("listar marketplace via GraphQL", async () => { ... })
test("like/unlike via mutation", async () => { ... })

// portal/tests/integration/regression.test.ts
test("homepage renderiza", async () => { ... })
test("busca semântica funciona", async () => { ... })
test("infinite scroll funciona", async () => { ... })
```

**Implementação:**
1. Portal: migrar clipping pages → urql mutations
2. Portal: migrar marketplace pages → urql queries/mutations
3. Portal: migrar push preferences → urql mutation
4. Portal: setup `codegen.ts` + `@graphql-codegen/cli`
5. Portal: remover `/api/clipping/*`, `/api/clippings/public/*`, `/api/widgets/*`, `/api/push/*`
6. Portal: remover `src/types/article.ts`, `src/types/clipping.ts`, `src/types/widget.ts` (gerados)

**Verificação:** Portal staging totalmente no GraphQL. Tipos gerados automaticamente no CI.

**Arquivos no portal:**
- `portal/codegen.ts` (novo)
- `portal/src/app/(logged-in)/minha-conta/clipping/**` (migrar)
- `portal/src/app/(public)/clippings/**` (migrar)
- Remover: `portal/src/app/api/clipping/**`, `/api/clippings/**`, `/api/widgets/**`, `/api/push/**`
- Remover: `portal/src/types/article.ts`, `clipping.ts`, `widget.ts`

---

### Fase 12 — Datasource PostgreSQL + Internal Queries
**Objetivo**: Conectar GraphQL ao PostgreSQL para servir workers internos.

**Tests first:**
```python
# tests/datasources/test_postgres.py (com banco de teste via docker-compose/testcontainers)
@pytest.mark.asyncio
async def test_get_news_by_id(pg_ds, seed_news):
    news = await pg_ds.get_news_by_id("abc123")
    assert news.unique_id == "abc123"
    assert news.title is not None
    assert news.theme_l1 is not None  # JOIN resolvido

@pytest.mark.asyncio
async def test_get_news_by_id_not_found(pg_ds):
    news = await pg_ds.get_news_by_id("nonexistent")
    assert news is None

@pytest.mark.asyncio
async def test_get_news_batch(pg_ds, seed_news):
    batch = await pg_ds.get_news_batch(["id1", "id2"])
    assert len(batch) == 2

@pytest.mark.asyncio
async def test_get_news_batch_empty(pg_ds):
    batch = await pg_ds.get_news_batch([])
    assert batch == []

@pytest.mark.asyncio
async def test_get_news_for_typesense(pg_ds, seed_news_with_embeddings):
    doc = await pg_ds.get_news_for_typesense("abc123")
    assert doc.content_embedding is not None
    assert doc.theme_1_level_1_label is not None
    assert doc.sentiment_label is not None  # features join

@pytest.mark.asyncio
async def test_connection_pool_concurrent(pg_ds, seed_news):
    """10 queries simultâneas sem timeout."""
    results = await asyncio.gather(*[pg_ds.get_news_by_id("abc123") for _ in range(10)])
    assert all(r is not None for r in results)

# tests/resolvers/test_internal_queries.py
@pytest.mark.asyncio
async def test_news_by_id_forbidden_without_sa(gql_client_user):
    result = await gql_client_user.execute('query { newsById(uniqueId: "abc") { uniqueId } }')
    assert any(e["extensions"]["code"] == "FORBIDDEN" for e in result.errors)

@pytest.mark.asyncio
async def test_news_by_id_with_service_account(gql_client_internal):
    result = await gql_client_internal.execute('query { newsById(uniqueId: "abc123") { uniqueId title } }')
    assert result.data["newsById"]["uniqueId"] == "abc123"

@pytest.mark.asyncio
async def test_news_batch(gql_client_internal):
    result = await gql_client_internal.execute("""
        query { newsBatch(uniqueIds: ["id1", "id2"]) { uniqueId } }
    """)
    assert len(result.data["newsBatch"]) == 2

@pytest.mark.asyncio
async def test_news_for_typesense(gql_client_internal):
    result = await gql_client_internal.execute("""
        query { newsForTypesense(uniqueId: "abc123") { uniqueId contentEmbedding sentimentLabel } }
    """)
    assert result.data["newsForTypesense"]["contentEmbedding"] is not None
```

**Implementação:**
1. `src/graphql_api/datasources/postgres.py` — asyncpg pool + queries (port das queries do `postgres_manager.py`)
2. `src/graphql_api/schema/types/internal.py` — Types `NewsRecord`, `TypesenseDocument`
3. `src/graphql_api/schema/resolvers/internal_queries.py` — Resolvers internos com guard `@internal`

**Verificação:** `pytest` com PostgreSQL de teste (testcontainers ou docker-compose) → todos verdes.

**Arquivos:**
- `graphql-api/src/graphql_api/datasources/postgres.py`
- `graphql-api/src/graphql_api/schema/types/internal.py`
- `graphql-api/src/graphql_api/schema/resolvers/internal_queries.py`
- `graphql-api/tests/datasources/test_postgres.py`
- `graphql-api/tests/resolvers/test_internal_queries.py`
- `graphql-api/docker-compose.test.yml` (PostgreSQL + seed data)

---

### Fase 13 — Internal Mutations: `upsertFeatures` + `batchUpsertFeatures`
**Objetivo**: Workers escrevem features via GraphQL.

**Tests first:**
```python
# tests/datasources/test_postgres_features.py
@pytest.mark.asyncio
async def test_upsert_features_merges_jsonb(pg_ds, seed_news):
    await pg_ds.upsert_features("abc123", {"word_count": 150})
    await pg_ds.upsert_features("abc123", {"trending_score": 1.5})
    features = await pg_ds.get_features("abc123")
    assert features["word_count"] == 150  # preservado
    assert features["trending_score"] == 1.5  # adicionado

@pytest.mark.asyncio
async def test_batch_upsert_features(pg_ds, seed_news):
    items = [
        {"unique_id": "id1", "features": {"word_count": 100}},
        {"unique_id": "id2", "features": {"word_count": 200}},
    ]
    result = await pg_ds.batch_upsert_features(items)
    assert result.processed == 2
    assert result.failed == 0

@pytest.mark.asyncio
async def test_batch_upsert_empty(pg_ds):
    result = await pg_ds.batch_upsert_features([])
    assert result.processed == 0

@pytest.mark.asyncio
async def test_upsert_nonexistent_news_handled(pg_ds):
    # FK violation → graceful error, não crash
    result = await pg_ds.upsert_features("nonexistent", {"word_count": 1})
    assert result is False

# tests/resolvers/test_internal_mutations.py
@pytest.mark.asyncio
async def test_upsert_features_mutation(gql_client_internal, seed_news):
    result = await gql_client_internal.execute("""
        mutation { upsertFeatures(uniqueId: "abc123", features: "{\"word_count\": 150}") }
    """)
    assert result.data["upsertFeatures"] is True

@pytest.mark.asyncio
async def test_batch_upsert_features_mutation(gql_client_internal, seed_news):
    result = await gql_client_internal.execute("""
        mutation { batchUpsertFeatures(items: [
            {uniqueId: "id1", features: "{\"word_count\": 100}"},
            {uniqueId: "id2", features: "{\"word_count\": 200}"}
        ]) { processed failed } }
    """)
    assert result.data["batchUpsertFeatures"]["processed"] == 2

@pytest.mark.asyncio
async def test_upsert_features_forbidden_without_sa(gql_client_user):
    result = await gql_client_user.execute("""
        mutation { upsertFeatures(uniqueId: "abc", features: "{}") }
    """)
    assert any(e["extensions"]["code"] == "FORBIDDEN" for e in result.errors)

@pytest.mark.asyncio
async def test_update_typesense_field(gql_client_internal, seed_news):
    result = await gql_client_internal.execute("""
        mutation { updateTypesenseField(uniqueId: "abc123", field: "image_broken", value: "true") }
    """)
    assert result.data["updateTypesenseField"] is True
```

**Implementação:**
1. Extender `datasources/postgres.py` com `upsert_features`, `batch_upsert_features`
2. `src/graphql_api/datasources/typesense_admin.py` — Typesense write (update field)
3. `src/graphql_api/schema/resolvers/internal_mutations.py`

**Arquivos:**
- `graphql-api/src/graphql_api/datasources/typesense_admin.py`
- `graphql-api/src/graphql_api/schema/resolvers/internal_mutations.py`
- `graphql-api/tests/datasources/test_postgres_features.py`
- `graphql-api/tests/resolvers/test_internal_mutations.py`

---

### Fase 14 — Internal Queries: BigQuery export, Similarity, Integrity
**Objetivo**: Cobrir todas as queries que as DAGs precisam.

**Tests first:**
```python
# tests/datasources/test_postgres_bigquery.py
@pytest.mark.asyncio
async def test_news_batch_for_bigquery(pg_ds, seed_news_month):
    records = await pg_ds.get_news_batch_for_bigquery(start_date, end_date, batch_size=100)
    assert len(records) <= 100
    assert all(r.theme_l1_code is not None for r in records if r.theme_l1_id)

@pytest.mark.asyncio
async def test_bigquery_pagination(pg_ds, seed_news_500):
    batch1 = await pg_ds.get_news_batch_for_bigquery(start, end, batch_size=100, cursor=None)
    batch2 = await pg_ds.get_news_batch_for_bigquery(start, end, batch_size=100, cursor=batch1[-1].unique_id)
    ids1 = {r.unique_id for r in batch1}
    ids2 = {r.unique_id for r in batch2}
    assert ids1.isdisjoint(ids2)

@pytest.mark.asyncio
async def test_bigquery_empty_range(pg_ds):
    records = await pg_ds.get_news_batch_for_bigquery(far_future, far_future_plus_1)
    assert records == []

# tests/datasources/test_postgres_similarity.py
@pytest.mark.asyncio
async def test_similar_articles(pg_ds, seed_news_with_embeddings):
    similar = await pg_ds.get_similar_articles("abc123", threshold=0.8, limit=5)
    assert all(s.similarity >= 0.8 for s in similar)
    assert len(similar) <= 5

@pytest.mark.asyncio
async def test_similar_articles_no_embedding(pg_ds, seed_news_no_embedding):
    similar = await pg_ds.get_similar_articles("no_emb", threshold=0.8)
    assert similar == []

# tests/datasources/test_postgres_integrity.py
@pytest.mark.asyncio
async def test_integrity_batch_prioritized(pg_ds, seed_news_various_ages):
    batch = await pg_ds.get_integrity_batch(batch_size=50)
    assert len(batch) <= 50
    # Artigos mais recentes devem aparecer primeiro (tier 1)
    assert batch[0].published_age_hours < batch[-1].published_age_hours

@pytest.mark.asyncio
async def test_integrity_excludes_recently_checked(pg_ds, seed_news_recently_checked):
    batch = await pg_ds.get_integrity_batch(batch_size=50)
    assert all(r.unique_id != "recently_checked" for r in batch)

# tests/resolvers/test_internal_queries_extended.py
@pytest.mark.asyncio
async def test_gql_news_batch_for_bigquery(gql_client_internal):
    result = await gql_client_internal.execute("""
        query { newsBatchForBigQuery(startDate: "2026-03-01", endDate: "2026-03-02", batchSize: 10) {
            uniqueId title themL1Code wordCount
        }}
    """)
    assert len(result.data["newsBatchForBigQuery"]) <= 10

@pytest.mark.asyncio
async def test_gql_similar_articles(gql_client_internal):
    result = await gql_client_internal.execute("""
        query { similarArticles(uniqueId: "abc123", threshold: 0.8, limit: 5) { uniqueId similarity } }
    """)
    assert all(s["similarity"] >= 0.8 for s in result.data["similarArticles"])

@pytest.mark.asyncio
async def test_gql_integrity_batch(gql_client_internal):
    result = await gql_client_internal.execute("""
        query { integrityBatch(batchSize: 20) { uniqueId url imageUrl } }
    """)
    assert len(result.data["integrityBatch"]) <= 20
```

**Implementação:**
1. Extender `datasources/postgres.py` com queries BigQuery export, similarity, integrity
2. Extender `schema/resolvers/internal_queries.py`

**Arquivos:**
- `graphql-api/tests/datasources/test_postgres_bigquery.py`
- `graphql-api/tests/datasources/test_postgres_similarity.py`
- `graphql-api/tests/datasources/test_postgres_integrity.py`
- `graphql-api/tests/resolvers/test_internal_queries_extended.py`

---

### Fase 15 — GraphQL Client Python + Migração Typesense Sync Worker
**Objetivo**: Primeiro worker migrado. Leitura de dados via GraphQL.

**Tests first:**
```python
# data-platform/tests/clients/test_graphql_client.py
@pytest.mark.asyncio
async def test_graphql_client_query(mock_httpx):
    client = GraphQLClient(url="http://graphql:8000/graphql", service_account=True)
    result = await client.query("query { newsById(uniqueId: $id) { title } }", variables={"id": "abc"})
    assert result["newsById"]["title"] is not None

@pytest.mark.asyncio
async def test_graphql_client_mutation(mock_httpx):
    client = GraphQLClient(url="http://graphql:8000/graphql", service_account=True)
    result = await client.mutate(
        "mutation { upsertFeatures(uniqueId: $id, features: $f) }",
        variables={"id": "abc", "f": '{"word_count": 100}'}
    )
    assert result["upsertFeatures"] is True

@pytest.mark.asyncio
async def test_graphql_client_auth_header(mock_httpx):
    client = GraphQLClient(url="http://graphql:8000/graphql", service_account=True)
    await client.query("query { newsById(uniqueId: \"abc\") { title } }")
    # Verifica que Authorization header com OIDC token foi enviado
    assert "Authorization" in mock_httpx.last_request.headers

# data-platform/tests/workers/test_typesense_sync_graphql.py
@pytest.mark.asyncio
async def test_typesense_sync_via_graphql(mock_graphql, mock_typesense):
    """Worker recebe unique_id, chama GraphQL newsForTypesense, upserta no Typesense."""
    handler = TypesenseSyncHandler(graphql_client=mock_graphql, typesense_client=mock_typesense)
    await handler.process("abc123")
    mock_graphql.query.assert_called_once()
    mock_typesense.upsert.assert_called_once()

@pytest.mark.asyncio
async def test_typesense_sync_not_found(mock_graphql_not_found, mock_typesense):
    """Artigo inexistente → ACK sem retry."""
    handler = TypesenseSyncHandler(graphql_client=mock_graphql_not_found, typesense_client=mock_typesense)
    result = await handler.process("nonexistent")
    assert result.status == "not_found"
    mock_typesense.upsert.assert_not_called()

@pytest.mark.asyncio
async def test_typesense_sync_document_parity(mock_graphql, mock_typesense, pg_manager):
    """Documento via GraphQL é idêntico ao gerado pela query SQL direta."""
    graphql_doc = await mock_graphql.query(NEWS_FOR_TYPESENSE_QUERY, {"uniqueId": "abc123"})
    direct_doc = await pg_manager.get_news_for_typesense("abc123")
    assert graphql_doc == direct_doc  # mesma shape e dados
```

**Implementação:**
1. `data-platform/src/data_platform/clients/graphql_client.py` — httpx + google-auth OIDC
2. Modificar `workers/typesense_sync/handler.py` → usar graphql_client
3. Feature flag: env var `USE_GRAPHQL=true` para rollback

**Arquivos:**
- `data-platform/src/data_platform/clients/graphql_client.py` (novo)
- `data-platform/src/data_platform/workers/typesense_sync/handler.py` (modificar)
- `data-platform/tests/clients/test_graphql_client.py`
- `data-platform/tests/workers/test_typesense_sync_graphql.py`
- `infra/terraform/workers.tf` (adicionar GRAPHQL_API_URL)

---

### Fase 16 — Migração Workers: Feature Worker + Bronze Writer
**Objetivo**: Todos os workers Pub/Sub migrados.

**Tests first:**
```python
# data-platform/tests/workers/test_feature_worker_graphql.py
@pytest.mark.asyncio
async def test_feature_worker_reads_via_graphql(mock_graphql):
    handler = FeatureHandler(graphql_client=mock_graphql)
    await handler.process("abc123")
    # Lê artigo via GraphQL
    mock_graphql.query.assert_called_once()
    # Escreve features via GraphQL
    mock_graphql.mutate.assert_called_once()

@pytest.mark.asyncio
async def test_feature_worker_computes_same_features(mock_graphql):
    """Features computadas são idênticas ao método direto."""
    handler = FeatureHandler(graphql_client=mock_graphql)
    features = await handler.compute_features(sample_article)
    assert features["word_count"] == expected_word_count
    assert features["readability_flesch"] == pytest.approx(expected_flesch, rel=0.01)

# data-platform/tests/workers/test_bronze_writer_graphql.py
@pytest.mark.asyncio
async def test_bronze_writer_reads_via_graphql(mock_graphql, mock_gcs):
    handler = BronzeWriterHandler(graphql_client=mock_graphql, gcs_client=mock_gcs)
    await handler.process("abc123")
    mock_graphql.query.assert_called_once()
    mock_gcs.write.assert_called_once()

@pytest.mark.asyncio
async def test_bronze_writer_json_parity(mock_graphql, mock_gcs, pg_manager):
    """JSON via GraphQL é idêntico ao gerado pela query SQL direta."""
    # ... comparação de output
```

**Implementação:**
1. Modificar `workers/feature_worker/app.py` → graphql_client
2. Modificar `workers/bronze_writer/app.py` → graphql_client

**Arquivos:**
- `data-platform/src/data_platform/workers/feature_worker/app.py` (modificar)
- `data-platform/src/data_platform/workers/bronze_writer/app.py` (modificar)
- `data-platform/tests/workers/test_feature_worker_graphql.py`
- `data-platform/tests/workers/test_bronze_writer_graphql.py`

---

### Fase 17 — Migração DAGs: BigQuery Sync + Engagement + Trending
**Objetivo**: DAGs que leem/escrevem PostgreSQL passam a usar GraphQL.

**Tests first:**
```python
# data-platform/tests/dags/test_bigquery_sync_graphql.py
@pytest.mark.asyncio
async def test_sync_facts_reads_via_graphql(mock_graphql):
    """DAG usa newsBatchForBigQuery query em vez de SQL direto."""
    await sync_facts_task(graphql_client=mock_graphql, start_date=..., end_date=...)
    mock_graphql.query.assert_called()
    # Verifica que a query usada é newsBatchForBigQuery
    assert "newsBatchForBigQuery" in mock_graphql.query.call_args[0][0]

@pytest.mark.asyncio
async def test_sync_facts_pagination(mock_graphql_paginated):
    """Dataset grande é processado em batches."""
    await sync_facts_task(graphql_client=mock_graphql_paginated, ...)
    assert mock_graphql_paginated.query.call_count > 1  # múltiplos batches

# data-platform/tests/dags/test_engagement_graphql.py
@pytest.mark.asyncio
async def test_engagement_writes_via_graphql(mock_graphql, mock_bigquery):
    """DAG escreve features via batchUpsertFeatures mutation."""
    await aggregate_and_sync(graphql_client=mock_graphql, bq_client=mock_bigquery)
    assert "batchUpsertFeatures" in mock_graphql.mutate.call_args[0][0]

# data-platform/tests/dags/test_trending_graphql.py
@pytest.mark.asyncio
async def test_trending_writes_via_graphql(mock_graphql, mock_bigquery):
    await compute_and_sync_trending(graphql_client=mock_graphql, bq_client=mock_bigquery)
    assert "batchUpsertFeatures" in mock_graphql.mutate.call_args[0][0]
```

**Implementação:**
1. Modificar `dags/sync_pg_to_bigquery.py` → leitura via GraphQL
2. Modificar `dags/aggregate_engagement.py` → escrita via GraphQL mutation
3. Modificar `dags/compute_trending.py` → escrita via GraphQL mutation
4. Nota: BigQuery queries continuam diretas (SDK próprio)

**Arquivos:**
- `data-platform/src/data_platform/dags/sync_pg_to_bigquery.py`
- `data-platform/src/data_platform/dags/aggregate_engagement.py`
- `data-platform/src/data_platform/dags/compute_trending.py`
- `data-platform/tests/dags/test_bigquery_sync_graphql.py`
- `data-platform/tests/dags/test_engagement_graphql.py`
- `data-platform/tests/dags/test_trending_graphql.py`

---

### Fase 18 — Migração DAGs: Clusters + Integrity
**Objetivo**: Últimas DAGs migradas.

**Tests first:**
```python
# data-platform/tests/dags/test_clusters_graphql.py
@pytest.mark.asyncio
async def test_clusters_reads_similarity_via_graphql(mock_graphql):
    await find_and_store_clusters(graphql_client=mock_graphql)
    assert "similarArticles" in mock_graphql.query.call_args[0][0]
    assert "batchUpsertFeatures" in mock_graphql.mutate.call_args[0][0]

# data-platform/tests/dags/test_integrity_graphql.py
@pytest.mark.asyncio
async def test_integrity_reads_batch_via_graphql(mock_graphql):
    batch = await fetch_integrity_batch(graphql_client=mock_graphql, batch_size=50)
    assert "integrityBatch" in mock_graphql.query.call_args[0][0]

@pytest.mark.asyncio
async def test_integrity_writes_results_via_graphql(mock_graphql):
    await save_integrity_results(graphql_client=mock_graphql, results=[...])
    assert "batchUpsertFeatures" in mock_graphql.mutate.call_args[0][0]

@pytest.mark.asyncio
async def test_integrity_updates_typesense_via_graphql(mock_graphql):
    await sync_image_status(graphql_client=mock_graphql, broken_ids=["id1"])
    assert "updateTypesenseField" in mock_graphql.mutate.call_args[0][0]
```

**Implementação:**
1. Modificar `dags/compute_clusters.py` → GraphQL queries/mutations
2. Modificar `dags/verify_news_integrity.py` → GraphQL queries/mutations

**Arquivos:**
- `data-platform/src/data_platform/dags/compute_clusters.py`
- `data-platform/src/data_platform/dags/verify_news_integrity.py`
- `data-platform/tests/dags/test_clusters_graphql.py`
- `data-platform/tests/dags/test_integrity_graphql.py`

---

### Fase 19 — Cleanup: Remover Acesso Direto ao PG dos Workers
**Objetivo**: Só o GraphQL API tem acesso direto ao PostgreSQL. Reduz blast radius.

**Tests first:**
```python
# data-platform/tests/integration/test_no_direct_pg.py
def test_workers_have_no_database_url():
    """Terraform dos workers não contém DATABASE_URL."""
    tf_content = open("infra/terraform/workers.tf").read()
    # Workers devem ter GRAPHQL_API_URL, não DATABASE_URL
    worker_blocks = extract_worker_env_blocks(tf_content)
    for worker in ["feature-worker", "typesense-sync", "bronze-writer"]:
        assert "DATABASE_URL" not in worker_blocks[worker]
        assert "GRAPHQL_API_URL" in worker_blocks[worker]

def test_workers_no_psycopg2_import():
    """Workers não importam psycopg2 diretamente."""
    import ast
    for worker_dir in ["feature_worker", "typesense_sync", "bronze_writer"]:
        path = f"src/data_platform/workers/{worker_dir}/app.py"
        tree = ast.parse(open(path).read())
        imports = [n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)]
        assert "psycopg2" not in imports
```

**Implementação:**
1. Remover `DATABASE_URL` do Terraform dos workers
2. Limpar imports psycopg2/asyncpg dos workers
3. Revogar Cloud SQL IAM roles dos service accounts dos workers
4. Manter `postgres_manager.py` para uso em scripts CLI e testes

**Arquivos:**
- `infra/terraform/workers.tf` (remover DATABASE_URL dos workers)
- `infra/terraform/iam.tf` (revogar Cloud SQL roles)
- `data-platform/tests/integration/test_no_direct_pg.py`

---

### Fase 20 — Produção: Rollout Gradual + Monitoring
**Objetivo**: Deploy em produção com rollback seguro.

**Tests first:**
```python
# tests/e2e/test_production_smoke.py
@pytest.mark.e2e
@pytest.mark.asyncio
async def test_graphql_health_prod():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{PROD_URL}/health")
        assert r.status_code == 200

@pytest.mark.e2e
@pytest.mark.asyncio
async def test_articles_query_prod():
    result = await prod_gql_client.execute("query { articles(page:1) { found } }")
    assert result.data["articles"]["found"] > 0

@pytest.mark.e2e
@pytest.mark.asyncio
async def test_internal_query_prod():
    result = await prod_internal_client.execute('query { newsById(uniqueId: "test") { uniqueId } }')
    assert result.errors is None

@pytest.mark.e2e
@pytest.mark.asyncio
async def test_latency_prod():
    latencies = []
    for _ in range(50):
        start = time.monotonic()
        await prod_gql_client.execute("query { articles(page:1, limit:10) { found } }")
        latencies.append(time.monotonic() - start)
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95)]
    assert p95 <= 0.200  # 200ms
```

**Implementação:**
1. Deploy GraphQL API em produção
2. Portal: habilitar `NEXT_PUBLIC_USE_GRAPHQL=true` em produção
3. Workers: apontar `GRAPHQL_API_URL` para produção
4. DAGs: apontar para GraphQL produção
5. Monitoring: Cloud Run metrics + structured logging
6. Alertas: error rate > 1%, latência p95 > 500ms
7. Após 48h estável: remover feature flags e código de fallback

---

## Oportunidades de Simplificação (Resumo)

| Antes | Depois | Ganho |
|-------|--------|-------|
| 25+ rotas REST no portal | ~12 queries + ~10 mutations | **Consolidação** |
| Tipos manuais em 3 repos | graphql-codegen no portal, Strawberry types no server | **Type safety end-to-end** |
| Auth repetida em cada handler | Um middleware de contexto | **DRY + segurança** |
| 6+ serviços com DATABASE_URL | 1 serviço (graphql-api) com PG | **Blast radius reduzido** |
| Over-fetching (ArticleRow 20+ campos) | Field selection por query | **Performance** |
| N+1 em themes/agencies | Strawberry DataLoader batch | **Performance** |
| Queries SQL duplicadas (workers vs DAGs) | Resolvers compartilhados | **DRY** |
| Zero documentação de API | Introspection + Playground | **Developer experience** |
| Busca semântica acoplada no portal | Resolver abstrai complexidade | **Encapsulamento** |
| Workers com acesso direto ao PG | API layer com auth OIDC | **Segurança + observabilidade** |
