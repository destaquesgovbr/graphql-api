# Datasources

Cada backend tem um datasource dedicado em `src/graphql_api/datasources/`. Eles
são instanciados no `lifespan` (startup) a partir de env vars e injetados no
`GraphQLContext` por request. Resolvers acessam via `info.context.<ds>`.

| Datasource | Backend | Lib | Modelo | Usado por |
|-----------|---------|-----|--------|-----------|
| `PostgresDatasource` | PostgreSQL `govbrnews` | `asyncpg` (pool) | **async** | themes, agencies, news (internal), `agencyAnalytics`, `articlesTimeline`, entidades (`entity`, `entityCoverage`, `trendingEntities`, `relatedEntities`, `entityNetwork`, `entityGraphStats`) |
| `TypesenseDatasource` | Typesense | `typesense` | **sync** | articles, search, facets |
| `TypesenseAdminDatasource` | Typesense | `typesense` | **sync** | writes (indexação, internal) |
| `FirestoreDatasource` | Firestore | `firebase-admin` | **sync** (wrapped) | clippings, subscriptions, marketplace, users |

!!! note "Async e sync convivem"
    `PostgresDatasource` é async (asyncpg); `Typesense*` e `Firestore` usam libs
    upstream síncronas. Strawberry lida com resolvers sync **e** async — não force
    `async` onde a lib é síncrona. Datasource ausente no startup vira
    `app.state.<ds> = None` (warning), e o resolver dependente falha em runtime.

!!! warning "Grafo de entidades depende da migração 032 do data-platform"
    `entity`, `relatedEntities` e `entityNetwork` fazem LEFT JOIN em
    `entity_stats` e leem `entity_edges.unique_content_count`;
    `relatedEntities` e `entityGraphStats` leem `entity_graph_meta`. As três
    estruturas vêm da migração 032 do data-platform: sem ela essas queries
    falham (tabela/coluna inexistente). Com a migração aplicada mas antes da
    primeira execução da DAG do grafo, os campos novos (`articleCount`,
    `uniqueContentCount`, `uniqueContentWeight`, `lift`) ficam nulos e
    `entityGraphStats` é `null`.

!!! warning "`entityCoverage` depende da migração 035 do data-platform"
    O dedup de republicações do `entityCoverage` (`uniqueContentCount`) usa a
    coluna `news.content_key`, criada pela migração 035 do data-platform: sem
    ela a query falha com `column n.content_key does not exist`. Aplique a 035
    **antes** de colocar esta versão no ar. Com a 035 aplicada mas antes do
    backfill da chave, o resultado é o mesmo de antes (cai no `content_hash`).

## Chave de conteúdo (dedup de republicações)

Um mesmo conteúdo publicado por vários órgãos (republicação) deve contar uma
vez nas contagens de "conteúdo único". A chave fica numa constante só,
`_CONTENT_DEDUP_KEY_SQL` em `datasources/postgres.py`:

```sql
COALESCE(n.content_key, n.content_hash, ne.unique_id)
```

- **`news.content_key`** (migração 035 do data-platform): agrupa
  quase-duplicatas. É o `unique_id` do artigo-líder do grupo (o mais antigo),
  calculado em lote pela tarefa `compute_content_keys` da DAG do grafo, que
  recalcula os últimos 10 dias a cada execução.
- **`content_hash`**: hash exato do conteúdo, para linhas ainda sem chave
  (antes do backfill ou recém-ingeridas). Sozinho quase não pega republicações
  entre órgãos, porque cada órgão reescreve as URLs de imagem e de link.
- **`unique_id`**: garante uma chave não nula.

Os espaços de chave não colidem (`unique_id` do líder contra hash de 16 hex).
A DAG do grafo do data-platform usa a mesma definição
(`ARTICLE_CONTENT_KEY_SQL`) para `entity_stats.unique_content_count` e
`entity_edges.unique_content_count`: mude as duas pontas juntas.

A mesma chave sai na API como **`Article.contentKey`**, calculada em Python
(`_content_dedup_key`, mesma precedência) a partir do `n.*` lido do Postgres,
por isso não exige a coluna. Só os caminhos servidos pelo Postgres a preenchem
(hoje, `entityArticles`); nas listas e na busca servidas pelo Typesense o campo
é `null`, porque o índice ainda não tem a chave (o `dedup: true` do Typesense
continua agrupando por `content_hash`, ver abaixo).

## Injeção por request

`get_context(request)` lê os datasources de `request.app.state` (populados pelo
lifespan) e monta o contexto:

```python
ctx = GraphQLContext(
    typesense_ds=getattr(state, "typesense_ds", None),
    firestore_ds=getattr(state, "firestore_ds", None),
    postgres_ds=getattr(state, "postgres_ds", None),
    typesense_admin_ds=getattr(state, "typesense_admin_ds", None),
)
```

## DataLoaders (batch + cache por request)

`get_context()` cria DataLoaders quando há `firestore_ds` — um por request
(`subscription_loader`, `releases_loader`). Eles resolvem o problema N+1: ao
listar N clippings, em vez de N idas ao Firestore para `mySubscription`/`releases`
de cada um, o loader agrupa as chaves e faz **uma** busca em lote.

Há também loaders de `Theme`/`Agency` (`dataloaders.py`) que resolvem labels via
uma **única facet query no Typesense** (`facet_by`), em vez de uma busca por
chave:

```python
async def _batch_load_agencies(keys, typesense_ds):
    response = typesense_ds.collections["articles"].documents.search(
        {"q": "*", "per_page": 0, "facet_by": "agency", "max_facet_values": 250}
    )
    # mapeia facet → label e devolve na ordem das keys
```

## Capacidades do `TypesenseDatasource`

Além da busca paginada de artigos e das facet queries de labels, o datasource de
leitura expõe (a partir do desacoplamento BD→GraphQL do portal) as seguintes
capacidades, todas em cima da coleção `articles`:

- **Dedup / `group_by content_hash`.** Quando o resolver pede `dedup` (em
  `articles`/`search` via `ArticleFilter.dedup` ou o arg `dedup` da `search`), a
  query usa `group_by=content_hash` + `group_limit=1`, colapsando variações do
  mesmo conteúdo numa única linha. É o hash exato: republicações entre órgãos
  com URLs reescritas não se juntam. Trocar o `group_by` pela chave de conteúdo
  (`content_key`) exige indexá-la no Typesense antes (pendente).
- **Busca híbrida com `alpha` passthrough.** O arg `alpha` da `search` é repassado
  ao `vector_query` (`embedding:([], alpha: <alpha>)`), controlando o peso do
  vetor no ranking híbrido (default `0.3`; `0` = keyword puro, `1` = vetor puro).
  Só tem efeito com `semantic: true`.
- **`theme_counts(level, days)`.** Uma facet query (`facet_by` no code do nível
  pedido, `per_page: 0`, filtro `published_at >= now - days`) que devolve a
  contagem de artigos por tema. Alimenta `themeArticleCounts`.
- **Busca por keyword (`q` / `query_by`).** Para estimativa/recortes, a busca por
  termo usa `q=<keyword>` com `query_by=title,summary`; quando o recorte não tem
  keywords, cai para `q=*` (filtro-only). Alimenta `estimateRecorteCount`,
  `releaseArticles` e `relatedArticles`.

!!! note "Similaridade: dois caminhos distintos"
    `relatedArticles` resolve **por theme-code** neste datasource Typesense
    (`mostSpecificThemeCode`, senão `theme1Level1Code`; exclui o próprio artigo;
    ordena por `publishedAt` desc; dedup por `content_hash`). É **distinto** do
    `similarArticles` **interno** (`IsInternal`), que usa **embeddings no
    Postgres** — backends diferentes, públicos diferentes.

## Mapeamento camelCase ↔ snake_case

Documentos do Firestore usam `camelCase` (mesma convenção do portal que os
escrevia); o Python usa `snake_case`. Os modelos Pydantic dos datasources fazem a
ponte com `Field(alias=...)`:

```python
class ClippingData(BaseModel):
    published_to_marketplace: bool = Field(default=False, alias="publishedToMarketplace")
    marketplace_listing_id: Optional[str] = Field(default=None, alias="marketplaceListingId")
```

!!! warning "Local de escrita importa"
    Mutations que escrevem no Firestore devem gravar **onde a query lê**. Um bug
    da R1: `updatePushPreferences` escrevia num campo aninhado em `users/{uid}`,
    mas `pushPreferences` lia da subcoleção `users/{uid}/pushPreferences/filters`
    — a mutation retornava `true` e a leitura voltava vazia (falha silenciosa).
    Sempre confirme o par escrita/leitura ao mexer em Firestore.

## O modelo subscriptions-first dos clippings

Clippings **não** vivem em `users/{uid}/clippings/` (modelo legado do portal).
Vivem numa coleção **top-level `clippings/`**, e a relação usuário↔clipping é
mediada por documentos de `subscriptions` com `role` (`AUTHOR` | `SUBSCRIBER`).
A query `clippings` retorna os clippings em que o usuário autenticado é autor
**ou** inscrito; `Clipping.isAuthor` e `Clipping.mySubscription` são resolvidos
no contexto do usuário. Isso permite que o mesmo clipping seja seguido por vários
usuários (marketplace) sem duplicação.
