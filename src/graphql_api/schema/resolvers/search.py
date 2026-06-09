from typing import Optional

import strawberry
from strawberry.types import Info

from graphql_api.datasources.embeddings import EmbeddingsDatasource
from graphql_api.datasources.typesense import COLLECTION_NAME, TypesenseDatasource
from graphql_api.schema.resolvers.articles import _to_graphql_article
from graphql_api.schema.types.article import (
    ArticleFilter,
    ArticleSort,
    ArticlesResult,
    sort_by_clause,
)
from graphql_api.schema.types.search import SearchSuggestion


async def resolve_search(
    query: str,
    filter: Optional[ArticleFilter] = None,
    page: int = 1,
    semantic: bool = False,
    alpha: Optional[float] = None,
    dedup: bool = False,
    ds: TypesenseDatasource = None,
    embeddings_ds: Optional[EmbeddingsDatasource] = None,
    sort_by: Optional[str] = None,
) -> ArticlesResult:
    """Busca artigos (keyword ou semântica), roteando pelo datasource.

    Usa `TypesenseDatasource.search_articles`, que já trata corretamente
    COLLECTION_NAME, conversão de timestamps (published_at → datetime),
    themes OR através de L1/L2/L3, theme_label, tags e conversão de datas.

    `sort_by=None` deixa o Typesense ranquear por relevância de text-match
    (correto para busca por keyword). `alpha` controla o peso híbrido
    (keyword vs. semântico); None mantém o legado 0.3. `dedup=True` aplica
    group_by content_hash.
    """
    if not query or not query.strip():
        raise ValueError("Query must not be empty")

    vector: Optional[list[float]] = None
    effective_alpha: Optional[float] = None
    if semantic:
        if embeddings_ds is None:
            embeddings_ds = EmbeddingsDatasource()
        vector = await embeddings_ds.generate_embedding(query)
        if vector is not None:
            effective_alpha = 0.3 if alpha is None else alpha

    result = ds.search_articles(
        q=query,
        query_by="title,content,summary",
        page=page,
        limit=20,
        agencies=filter.agencies if filter else None,
        themes=filter.themes if filter else None,
        theme_label=filter.theme_label if filter else None,
        tags=filter.tags if filter else None,
        start_date=filter.start_date if filter else None,
        end_date=filter.end_date if filter else None,
        entities=filter.entities if filter else None,
        sentiment=filter.sentiment if filter else None,
        dedup=dedup,
        sort_by=sort_by,
        vector=vector,
        alpha=effective_alpha,
    )

    return ArticlesResult(
        articles=[_to_graphql_article(a) for a in result.articles],
        page=page,
        found=result.found,
    )


async def resolve_search_suggestions(
    query: str,
    typesense_client: object = None,
) -> list[SearchSuggestion]:
    """Return top title suggestions for a query."""
    if not query or not query.strip():
        return []

    params = {
        "q": query,
        "query_by": "title",
        "per_page": 7,
        "page": 1,
        "include_fields": "unique_id,title",
    }

    result = typesense_client.collections[COLLECTION_NAME].documents.search(params)

    return [
        SearchSuggestion(
            unique_id=hit.get("document", hit).get("unique_id", ""),
            title=hit.get("document", hit).get("title", ""),
        )
        for hit in result.get("hits", [])
    ]


@strawberry.type
class SearchQuery:
    @strawberry.field(description="Search articles with keyword or semantic search")
    async def search(
        self,
        info: Info,
        query: str,
        filter: Optional[ArticleFilter] = None,
        page: int = 1,
        semantic: bool = False,
        alpha: Optional[float] = None,
        dedup: bool = False,
        sort: Optional[ArticleSort] = None,
    ) -> ArticlesResult:
        ds = info.context.typesense_ds
        if ds is None:
            return ArticlesResult(articles=[], page=page, found=0)

        embeddings_ds = EmbeddingsDatasource() if semantic else None
        return await resolve_search(
            query=query,
            filter=filter,
            page=page,
            semantic=semantic,
            alpha=alpha,
            dedup=dedup,
            ds=ds,
            embeddings_ds=embeddings_ds,
            # Busca por keyword: None/RELEVANCE preservam a relevância de
            # text-match (sort_by=None); DATE/TRENDING/VIEWS sobrescrevem.
            sort_by=sort_by_clause(sort, relevance_default=True),
        )

    @strawberry.field(description="Get search suggestions for autocomplete")
    async def search_suggestions(self, info: Info, query: str) -> list[SearchSuggestion]:
        ds = info.context.typesense_ds
        if ds is None:
            return []

        return await resolve_search_suggestions(
            query=query,
            typesense_client=ds.client,
        )
