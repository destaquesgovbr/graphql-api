from typing import Optional

import strawberry
from strawberry.types import Info

from graphql_api.schema.types.analytics import Granularity
from graphql_api.schema.types.article import Article, ArticlesResult
from graphql_api.schema.types.entities import (
    EntityCoveragePoint,
    EntityKind,
    EntitySearchResult,
    TrendingEntityResult,
)


def _news_record_to_article(record) -> Article:
    """Mapeia `NewsRecord` (Postgres) para `Article` (GraphQL)."""
    from graphql_api.datasources.postgres import NewsRecord

    if not isinstance(record, NewsRecord):
        record = record
    return Article(
        unique_id=record.unique_id,
        title=record.title,
        url=record.url,
        image=record.image_url,
        video_url=record.video_url,
        content=record.content,
        summary=record.summary,
        subtitle=record.subtitle,
        editorial_lead=record.editorial_lead,
        category=record.category,
        tags=record.tags or [],
        agency=record.agency_key,
        agency_name=record.agency_name,
        published_at=record.published_at,
        extracted_at=record.extracted_at,
        theme_1_level_1_code=record.theme_l1_code,
        theme_1_level_1_label=record.theme_l1_label,
        theme_1_level_2_code=record.theme_l2_code,
        theme_1_level_2_label=record.theme_l2_label,
        theme_1_level_3_code=record.theme_l3_code,
        theme_1_level_3_label=record.theme_l3_label,
        most_specific_theme_code=record.most_specific_theme_code,
        most_specific_theme_label=record.most_specific_theme_label,
    )


@strawberry.type
class EntityQuery:
    @strawberry.field(description="Série temporal de cobertura de uma entidade por agência")
    async def entity_coverage(
        self,
        info: Info,
        entity_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        granularity: Granularity = Granularity.MONTH,
    ) -> list[EntityCoveragePoint]:
        ds = info.context.postgres_ds
        rows = await ds.entity_coverage(entity_id, granularity.value, date_from, date_to)
        return [
            EntityCoveragePoint(
                period=str(row["period"]),
                agency_key=row.get("agency_key") or "",
                agency_name=row.get("agency_name"),
                article_count=int(row.get("article_count") or 0),
                total_mentions=int(row.get("total_mentions") or 0),
                avg_sentiment_score=row.get("avg_sentiment_score"),
            )
            for row in rows
        ]

    @strawberry.field(description="Busca fuzzy de entidades por nome ou alias")
    async def entity_search(
        self,
        info: Info,
        query: str,
        entity_type: Optional[EntityKind] = None,
        limit: int = 5,
    ) -> list[EntitySearchResult]:
        if not query.strip():
            return []
        ds = info.context.postgres_ds
        type_val = entity_type.value if entity_type else None
        rows = await ds.entity_search(query.strip(), type_val, min(limit, 20))
        return [
            EntitySearchResult(
                entity_id=row["entity_id"],
                canonical_name=row.get("canonical_name") or "",
                type=row.get("type") or "",
                description=row.get("description"),
                wikidata_url=row.get("wikidata_url"),
                agency_key=row.get("agency_key"),
                aliases=row.get("aliases") or [],
                article_count=int(row.get("article_count") or 0),
                confidence=float(row.get("confidence") or 0.0),
                match_type=row.get("match_type") or "",
            )
            for row in rows
        ]

    @strawberry.field(
        description=(
            "Artigos de uma entidade canônica via news_entities (Postgres direto). "
            "Não depende do campo entityCanonical no Typesense."
        )
    )
    async def entity_articles(
        self,
        info: Info,
        entity_id: str,
        page: int = 1,
        limit: int = 10,
    ) -> ArticlesResult:
        ds = info.context.postgres_ds
        safe_limit = min(max(limit, 1), 50)
        safe_page = max(page, 1)
        offset = (safe_page - 1) * safe_limit
        records, total = await ds.get_entity_articles(entity_id, safe_limit, offset)
        return ArticlesResult(
            articles=[_news_record_to_article(r) for r in records],
            page=safe_page,
            found=total,
        )

    @strawberry.field(description="Entidades NER com maior crescimento de cobertura (pré-computado)")
    async def trending_entities(
        self,
        info: Info,
        limit: int = 10,
    ) -> list[TrendingEntityResult]:
        ds = info.context.postgres_ds
        rows = await ds.get_trending_entities(min(limit, 50))
        return [
            TrendingEntityResult(
                entity_id=row["entity_id"],
                canonical_name=row.get("canonical_name") or "",
                type=row.get("type") or "",
                trending_score=float(row.get("trending_score") or 0.0),
                volume_ratio=float(row.get("volume_ratio") or 0.0),
                window_count=int(row.get("window_count") or 0),
                window_agencies=int(row.get("window_agencies") or 0),
                computed_at=row.get("computed_at"),
            )
            for row in rows
        ]
