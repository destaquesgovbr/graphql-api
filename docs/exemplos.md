# Exemplos

Operações reais contra o schema. A referência completa é o
[SDL gerado](reference/schema.md).

## Playground (GraphiQL)

A API serve o **GraphiQL** nativo (Strawberry) em `GET /graphql` — um playground
no browser com introspecção do schema, autocomplete e execução de queries. É a
forma mais rápida de explorar a API.

| Ambiente | URL do playground |
|----------|-------------------|
| **Staging** | [`…/graphql`](https://destaquesgovbr-graphql-api-klvx64dufq-rj.a.run.app/graphql) |
| **Dev local** | `http://localhost:8000/graphql` (após `make dev`) |

!!! tip "Como autenticar no GraphiQL"
    Queries públicas (notícias, temas, busca, widgets) rodam sem nada. Para
    **queries/mutations autenticadas** (clippings, marketplace, push), adicione o
    header na aba **Headers** do GraphiQL:

    ```json
    { "Authorization": "Bearer <SEU_JWT_DO_KEYCLOAK>" }
    ```

    O JWT é o `access_token` da sua sessão no portal (visível em
    `/api/auth/session`). Sem ele, campos guardados por `IsAuthenticated` falham.

!!! warning "Subscriptions não rodam no GraphiQL"
    O GraphiQL fala com `/graphql` (queries/mutations). A subscription
    `generateRecortes` é SSE em `/graphql/stream` — ver
    [Subscriptions & SSE](subscriptions-sse.md).

## Queries públicas (sem auth)

=== "Artigos"

    ```graphql
    query Artigos {
      articles(page: 1, limit: 5, filter: { agencies: ["ms"], themes: [] }) {
        articles { uniqueId title url agency publishedAt }
        page
        found
      }
    }
    ```

=== "Busca"

    ```graphql
    query Busca {
      search(query: "vacinação", page: 1, semantic: false) {
        articles { uniqueId title }
        found
      }
    }
    ```

=== "Temas e órgãos"

    ```graphql
    query Metadados {
      themes { code label }
      agencies { code label }
      popularTags(limit: 10) { label count }
    }
    ```

=== "Analytics"

    ```graphql
    query Painel {
      analyticsKpis(range: { days: 30 }) { total activeThemes activeAgencies dailyAverage }
      topThemes(range: { days: 30 }, limit: 8) { label count }
      articlesTimeline(range: { days: 30 }) { date count }
    }
    ```

## Queries e mutations autenticadas

Exigem `Authorization: Bearer <JWT do Keycloak>`.

=== "Listar clippings"

    ```graphql
    query MeusClippings {
      clippings {            # autorados + inscritos do usuário autenticado
        id
        name
        isAuthor
        publishedToMarketplace
        mySubscription { role deliveryChannels { email telegram push webhook } }
      }
    }
    ```

=== "Criar clipping"

    ```graphql
    mutation Criar($input: ClippingInput!) {
      createClipping(input: $input) { id name recortes { title themes } }
    }
    ```

    ```json
    {
      "input": {
        "name": "Saúde — vacinação",
        "schedule": "0 8 * * *",
        "recortes": [
          { "title": "Vacinas", "themes": [], "agencies": ["ms"], "keywords": ["vacinação"] }
        ],
        "deliveryChannels": { "email": true, "telegram": false, "push": false, "webhook": false }
      }
    }
    ```

=== "Estimativa"

    ```graphql
    query Estimar {
      clippingEstimate(themes: [], agencies: ["ms"], keywords: ["vacinação"]) {
        totalEstimate
      }
    }
    ```

=== "Marketplace"

    ```graphql
    mutation Publicar($clippingId: String!, $input: PublishInput!) {
      publishToMarketplace(clippingId: $clippingId, input: $input) { id name }
    }

    mutation Clonar($listingId: String!) {
      cloneMarketplaceListing(listingId: $listingId) { id name }   # retorna o Clipping novo
    }
    ```

## Subscription (SSE)

Via `/graphql/stream` — ver [Subscriptions & SSE](subscriptions-sse.md).

```graphql
subscription Gerar($prompt: String!) {
  generateRecortes(prompt: $prompt) {
    __typename
    ... on AgentEventThinking { message }
    ... on AgentEventDone { suggestedName recortes { title themes agencies keywords } }
    ... on AgentEventError { message }
  }
}
```

## Queries internas (service account)

Usadas pelos workers de dados com OIDC do Google. **Não** são para o portal.

```graphql
query ParaTypesense($id: String!) { newsForTypesense(uniqueId: $id) { uniqueId title } }
query ParaBigquery { newsBatchForBigquery(startDate: "2026-01-01", endDate: "2026-01-31") { uniqueId } }
mutation Features($id: String!, $f: JSON!) { upsertFeatures(uniqueId: $id, features: $f) }
```

!!! info "Estes endpoints existem hoje"
    O schema **já expõe** a superfície interna que os workers precisam
    (`newsById`, `newsBatch`, `newsForTypesense`, `newsBatchForBigquery`,
    `upsertFeatures`, `batchUpsertFeatures`, `updateTypesenseField`). A migração
    dos workers (remover acesso direto ao Postgres) é o próximo passo de
    desacoplamento — ver a documentação central / blog.

## Gotchas

!!! danger "Compilado de armadilhas (todas vieram do drift da R1)"
    - **Scalar de IDs é `String`, não `ID`.** `clipping(id: String!)`,
      `article(uniqueId: String!)`. Usar `ID!` → `Unknown type 'ID'`.
    - **Subscriptions em `/graphql/stream`**, não `/graphql`. URL errada → 404 /
      "Failed to fetch".
    - **CSP do portal** precisa listar a origin deste serviço em `connect-src`,
      senão o browser bloqueia (CORS aqui não basta).
    - **`Agency` é `{ code, label }`** — não `{ key, name, type }`.
    - **`clippingEstimate` e `sendClipping` são placeholders** (mock) na R1 —
      `clippingEstimate` recebe `themes/agencies/keywords` (não uma lista de
      recortes) e retorna `{ totalEstimate }`.
    - **`cloneMarketplaceListing` retorna `Clipping!`** (precisa de selection set,
      ex.: `{ id name }`) — era `Boolean!` antes de um gap-fix pré-rollout.
    - **`clippings`** (não `myClippings`) é a query de listagem do usuário.
