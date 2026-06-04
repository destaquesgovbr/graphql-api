# Datasources

Cada backend tem um datasource dedicado em `src/graphql_api/datasources/`. Eles
são instanciados no `lifespan` (startup) a partir de env vars e injetados no
`GraphQLContext` por request. Resolvers acessam via `info.context.<ds>`.

| Datasource | Backend | Lib | Modelo | Usado por |
|-----------|---------|-----|--------|-----------|
| `PostgresDatasource` | PostgreSQL `govbrnews` | `asyncpg` (pool) | **async** | themes, agencies, news (internal) |
| `TypesenseDatasource` | Typesense | `typesense` | **sync** | articles, search, facets |
| `TypesenseAdminDatasource` | Typesense | `typesense` | **sync** | writes (indexação, internal) |
| `FirestoreDatasource` | Firestore | `firebase-admin` | **sync** (wrapped) | clippings, subscriptions, marketplace, users |

!!! note "Async e sync convivem"
    `PostgresDatasource` é async (asyncpg); `Typesense*` e `Firestore` usam libs
    upstream síncronas. Strawberry lida com resolvers sync **e** async — não force
    `async` onde a lib é síncrona. Datasource ausente no startup vira
    `app.state.<ds> = None` (warning), e o resolver dependente falha em runtime.

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
