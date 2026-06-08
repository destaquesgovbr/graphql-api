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
        articles {
          uniqueId title url agency publishedAt
          # hierarquia de temas (L1→L3) + o tema mais específico atribuído
          theme1Level1Code theme1Level1Label
          theme1Level2Code theme1Level2Label
          theme1Level3Code theme1Level3Label
          mostSpecificThemeCode mostSpecificThemeLabel
        }
        page
        found
      }
    }
    ```

    !!! note "`filter.themes` faz OR entre níveis"
        Os codes em `themes` casam contra L1 **ou** L2 **ou** L3 — não só o nível
        topo. Use `themeLabel` (label de L1) para filtrar por rótulo legível, e
        `dedup: true` para agrupar variações do mesmo conteúdo por `content_hash`.

=== "Busca"

    ```graphql
    query Busca {
      # alpha = peso do vetor no ranking híbrido (0=keyword puro, 1=vetor puro)
      search(query: "vacinação", page: 1, semantic: true, alpha: 0.3, dedup: true) {
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
      # contagem de artigos por tema, no nível e janela escolhidos
      themeArticleCounts(days: 30, level: 1) { code label count }
    }
    ```

=== "Relacionados"

    ```graphql
    query Relacionados($id: String!) {
      # similares por theme-code (Typesense), excluindo o próprio artigo
      relatedArticles(uniqueId: $id, limit: 4) {
        uniqueId title url mostSpecificThemeLabel
      }
    }
    ```

    !!! tip "`relatedArticles` ≠ `similarArticles`"
        `relatedArticles` é **público** e similaridade por **theme-code** no
        Typesense (usa `mostSpecificThemeCode`, senão `theme1Level1Code`). O
        `similarArticles` é **interno** (`IsInternal`) e baseado em **embeddings
        no Postgres** — ver [Queries internas](#queries-internas-service-account).

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

=== "Estimativa (real)"

    ```graphql
    query Estimar {
      # contagem REAL por recorte nas últimas sinceHours; substitui o mock clippingEstimate
      estimateRecorteCount(
        themes: [], agencies: ["ms"], keywords: ["vacinação"], sinceHours: 24
      )
    }
    ```

    !!! note "`estimateRecorteCount` é o caminho atual"
        Retorna um `Int!` (contagem real no Typesense), não o `{ totalEstimate }`
        mock do antigo `clippingEstimate`. Para keywords, conta por keyword e
        devolve o MAX; sem keywords, uma única contagem filtro-only. É **público**.

=== "Listings seguidos"

    ```graphql
    query MeusSeguidos {
      # substitui o getFollows do portal; campos do listing + canais de entrega
      myFollowedListings {
        id name authorDisplayName followedAt
        deliveryChannels { email telegram push webhook }
        extraEmails webhookUrl
      }
    }
    ```

=== "Telegram vinculado"

    ```graphql
    query Telegram {
      # substitui o getHasTelegram; lê users/{id}/telegramLink/account
      currentUserHasTelegramLinked
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

## Releases (entregas)

Um **release** é uma entrega histórica de um clipping. A autorização é
**mista**: pública se o listing-fonte estiver ativo (conteúdo já é público),
senão restrita ao autor ou subscriber — ver [auth › Releases](auth.md#3-autorizacao-mista-releases).

=== "Release por id"

    ```graphql
    query Release($id: String!) {
      # busca por id; recortes/marketplaceListingId/digestPreview só são populados aqui
      release(id: $id) {
        id clippingName createdAt articlesCount
        digestPreview              # resumo ≤150 chars, computado
        marketplaceListingId       # id do listing ativo, ou null
        recortes { title themes agencies keywords }
      }
    }
    ```

=== "Artigos de um release"

    ```graphql
    query ArtigosDoRelease($id: String!) {
      # OR dos recortes (com keywords), dedup por uniqueId; auth espelha release(id)
      releaseArticles(id: $id) {
        uniqueId title url publishedAt mostSpecificThemeLabel
      }
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
    - **`estimateRecorteCount` é a estimativa real** (`Int!`) e substitui o mock
      `clippingEstimate` (que retorna `{ totalEstimate }` e é deprecated). O novo
      recebe `themes/agencies/keywords/sinceHours`. `sendClipping` segue mock.
    - **IDs de tema fazem OR entre L1/L2/L3.** `ArticleFilter.themes` casa contra
      qualquer nível; `relatedArticles`/`releaseArticles` usam `mostSpecificThemeCode`.
    - **`relatedArticles` (público, theme-code) ≠ `similarArticles` (interno, embeddings).**
    - **`cloneMarketplaceListing` retorna `Clipping!`** (precisa de selection set,
      ex.: `{ id name }`) — era `Boolean!` antes de um gap-fix pré-rollout.
    - **`clippings`** (não `myClippings`) é a query de listagem do usuário.
