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


@strawberry.type
class TrendingEntityResult:
    entity_id: str
    canonical_name: str
    type: str
    trending_score: float
    volume_ratio: float
    window_count: int
    window_agencies: int
    computed_at: Optional[str]
    baseline_count: Optional[int] = strawberry.field(
        default=None,
        description="Artigos da entidade na janela de baseline. Null em linha gravada antes da migração 029.",
    )
    baseline_agencies: Optional[int] = strawberry.field(
        default=None,
        description="Agências distintas na janela de baseline. Null em linha gravada antes da migração 029.",
    )
    is_new: Optional[bool] = strawberry.field(
        default=None,
        description="True quando a entidade não aparece no baseline (baselineCount = 0). Null se o baseline é nulo.",
    )


@strawberry.type
class PolicyListItem:
    """Entrada de política pública na listagem /politicas do portal."""

    entity_id: str
    canonical_name: str
    domain: Optional[str]           # SOCIAL|ECONOMIC|HEALTH|EDUCATION|SECURITY|ENVIRONMENT|GOVERNANCE
    lifecycle_phase: Optional[str]  # ANNOUNCED|REGULATION|IMPLEMENTATION|EVALUATION|ROUTINE
    wikidata_id: Optional[str]
    aliases: list[str]
    article_count: int              # total de menções distintas no corpus


@strawberry.type
class PolicyDetails:
    domain: Optional[str]           # SOCIAL|ECONOMIC|HEALTH|EDUCATION|SECURITY|ENVIRONMENT|GOVERNANCE
    lifecycle_phase: Optional[str]  # ANNOUNCED|REGULATION|IMPLEMENTATION|EVALUATION|ROUTINE
    enabling_laws: list[str]        # entity_ids de LAWs relacionadas
    responsible_agencies: list[str] # agency keys
    target_population: list[str]    # ex: ["estudantes", "idosos"]
    first_mentioned_date: Optional[str]  # ISO date string
    wikidata_id: Optional[str]
    instance_of: Optional[str]      # QID da classe Wikidata
