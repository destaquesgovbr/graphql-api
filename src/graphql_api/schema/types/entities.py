import enum
from typing import Optional

import strawberry

from graphql_api.schema.types.analytics import Granularity  # noqa: F401 — reexportado para resolvers


@strawberry.enum
class EntityKind(enum.Enum):
    """Tipo/classe de entidade nomeada no registry (filtro de busca)."""

    ORG = "ORG"
    PER = "PER"
    LOC = "LOC"
    EVENT = "EVENT"
    POLICY = "POLICY"
    LAW = "LAW"


@strawberry.type
class EntityCoveragePoint:
    period: str
    agency_key: str
    agency_name: Optional[str]
    article_count: int
    total_mentions: int
    avg_sentiment_score: Optional[float]


@strawberry.type
class EntitySearchResult:
    entity_id: str
    canonical_name: str
    type: str
    description: Optional[str]
    wikidata_url: Optional[str]
    agency_key: Optional[str]
    aliases: list[str]
    article_count: int
    confidence: float
    match_type: str
