from typing import Optional

import strawberry
from strawberry.types import Info

from graphql_api.datasources.embeddings import EmbeddingsDatasource
from graphql_api.datasources.typesense import COLLECTION_NAME
from graphql_api.schema.types.article import Article, ArticleFilter, ArticlesResult
from graphql_api.schema.types.search import SearchSuggestion


def _build_search_params(
    query: str,
    filter_input: Optional[ArticleFilter],
    page: int,
    dedup: bool = False,
) -> dict:
    """Build Typesense search parameters from query and filters."""
    params = {
        "q": query,
        "query_by": "title,content,summary",
        "per_page": 20,
        "page": page,
    }

    filter_parts: list[str] = []
    if filter_input:
        if filter_input.agencies:
            filter_parts.append(f"agency:=[{','.join(filter_input.agencies)}]")
        if filter_input.themes:
            filter_parts.append(f"category:=[{','.join(filter_input.themes)}]")
        if filter_input.tags:
            filter_parts.append(f"tags:=[{','.join(filter_input.tags)}]")
        if filter_input.start_date:
            filter_parts.append(f"published_at:>={filter_input.start_date}")
        if filter_input.end_date:
            filter_parts.append(f"published_at:<={filter_input.end_date}")

    if filter_parts:
        params["filter_by"] = " && ".join(filter_parts)

    if dedup:
        # Deduplica por content_hash (mesma notícia republicada por várias agências).
        params["group_by"] = "content_hash"
        params["group_limit"] = 1

    return params


def _hit_to_article(hit: dict) -> Article:
    """Convert a Typesense hit to an Article."""
    doc = hit.get("document", hit)
    return Article(
        unique_id=doc.get("unique_id", ""),
        title=doc.get("title", ""),
        url=doc.get("url", ""),
        image=doc.get("image"),
        video_url=doc.get("video_url"),
        content=doc.get("content"),
        summary=doc.get("summary"),
        subtitle=doc.get("subtitle"),
        editorial_lead=doc.get("editorial_lead"),
        category=doc.get("category"),
        tags=doc.get("tags", []),
        agency=doc.get("agency"),
        agency_name=doc.get("agency_name"),
        published_at=doc.get("published_at"),
        extracted_at=doc.get("extracted_at"),
        theme_1_level_1_code=doc.get("theme_1_level_1_code"),
        theme_1_level_1_label=doc.get("theme_1_level_1_label"),
        theme_1_level_2_code=doc.get("theme_1_level_2_code"),
        theme_1_level_2_label=doc.get("theme_1_level_2_label"),
        theme_1_level_3_code=doc.get("theme_1_level_3_code"),
        theme_1_level_3_label=doc.get("theme_1_level_3_label"),
        most_specific_theme_code=doc.get("most_specific_theme_code"),
        most_specific_theme_label=doc.get("most_specific_theme_label"),
    )


def _iter_hits(result: dict):
    """Itera os hits de uma resposta, lidando com `hits` (busca normal) e
    `grouped_hits` (quando `group_by`/dedup está ativo)."""
    if "grouped_hits" in result:
        for group in result["grouped_hits"]:
            yield from group.get("hits", [])
        return
    yield from result.get("hits", [])


async def resolve_search(
    query: str,
    filter: Optional[ArticleFilter] = None,
    page: int = 1,
    semantic: bool = False,
    alpha: Optional[float] = None,
    dedup: bool = False,
    typesense_client: object = None,
    embeddings_ds: Optional[EmbeddingsDatasource] = None,
) -> ArticlesResult:
    """Search articles with optional semantic (hybrid) search.

    `alpha` controla o peso híbrido (keyword vs. semântico) no `vector_query`;
    se None, mantém o legado 0.3. `dedup=True` aplica group_by content_hash
    (keyword e semântico).
    """
    if not query or not query.strip():
        raise ValueError("Query must not be empty")

    params = _build_search_params(query, filter, page, dedup=dedup)

    if semantic:
        if embeddings_ds is None:
            embeddings_ds = EmbeddingsDatasource()
        vector = await embeddings_ds.generate_embedding(query)
        if vector is not None:
            effective_alpha = 0.3 if alpha is None else alpha
            vector_str = ",".join(str(v) for v in vector)
            params["vector_query"] = (
                f"content_embedding:([{vector_str}], k:256, alpha:{effective_alpha})"
            )

    result = typesense_client.collections[COLLECTION_NAME].documents.search(params)

    articles = [_hit_to_article(hit) for hit in _iter_hits(result)]
    found = result.get("found", 0)

    return ArticlesResult(articles=articles, page=page, found=found)


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
            typesense_client=ds.client,
            embeddings_ds=embeddings_ds,
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
