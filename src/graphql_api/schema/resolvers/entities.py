from typing import Optional

import strawberry
from strawberry.types import Info

from graphql_api.schema.types.analytics import Granularity
from graphql_api.schema.types.entities import (
    EntityCoveragePoint,
    EntityKind,
    EntitySearchResult,
    TrendingEntityResult,
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
