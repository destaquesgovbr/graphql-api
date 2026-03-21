import math

import strawberry
from strawberry.types import Info

from graphql_api.schema.types.article import Article
from graphql_api.schema.types.widget import (
    WidgetArticlesResult,
    WidgetConfig,
    WidgetConfigInput,
    WidgetPagination,
)

MAX_PER_PAGE = 50
COLLECTION_NAME = "news"


def _hit_to_article(hit: dict) -> Article:
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
    )


def resolve_widget_config(typesense_client: object) -> WidgetConfig:
    """Return available agencies and themes by faceting the Typesense collection."""
    params = {
        "q": "*",
        "per_page": 0,
        "facet_by": "agency,category",
        "max_facet_values": 100,
    }
    result = typesense_client.collections[COLLECTION_NAME].documents.search(params)

    agencies: list[str] = []
    themes: list[str] = []
    for facet in result.get("facet_counts", []):
        field_name = facet.get("field_name", "")
        values = [c["value"] for c in facet.get("counts", []) if c.get("value")]
        if field_name == "agency":
            agencies = values
        elif field_name == "category":
            themes = values

    return WidgetConfig(agencies=agencies, themes=themes)


def resolve_widget_articles(
    config: WidgetConfigInput,
    page: int,
    typesense_client: object,
) -> WidgetArticlesResult:
    """Return articles filtered by widget config with pagination."""
    limit = min(config.articles_per_page, MAX_PER_PAGE)
    if limit < 1:
        limit = 1

    filter_parts: list[str] = []
    if config.agencies:
        joined = ", ".join(f"`{a}`" for a in config.agencies)
        filter_parts.append(f"agency:[{joined}]")
    if config.themes:
        joined = ", ".join(f"`{t}`" for t in config.themes)
        filter_parts.append(f"category:[{joined}]")

    search_params: dict = {
        "q": "*",
        "per_page": limit,
        "page": max(page, 1),
        "sort_by": "published_at:desc",
    }
    if filter_parts:
        search_params["filter_by"] = " && ".join(filter_parts)

    result = typesense_client.collections[COLLECTION_NAME].documents.search(
        search_params
    )

    articles = [_hit_to_article(hit) for hit in result.get("hits", [])]
    found = result.get("found", 0)
    total_pages = math.ceil(found / limit) if limit > 0 else 0

    pagination = WidgetPagination(
        page=max(page, 1),
        limit=limit,
        total=found,
        has_more=max(page, 1) < total_pages,
    )

    return WidgetArticlesResult(articles=articles, pagination=pagination)


@strawberry.type
class WidgetQuery:
    @strawberry.field(description="Get available widget configuration options")
    def widget_config(self, info: Info) -> WidgetConfig:
        ts = info.context.typesense_ds
        return resolve_widget_config(ts.client)

    @strawberry.field(description="Get articles for a widget")
    def widget_articles(
        self,
        info: Info,
        config: WidgetConfigInput,
        page: int = 1,
    ) -> WidgetArticlesResult:
        ts = info.context.typesense_ds
        return resolve_widget_articles(config, page, ts.client)
