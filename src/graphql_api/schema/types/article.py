from datetime import datetime
from enum import Enum
from typing import Optional

import strawberry
from strawberry.types import Info

from graphql_api.schema.types.features import (
    ArticleFeatures,
    article_features_from_json,
)


@strawberry.type
class Article:
    unique_id: str
    title: str
    url: str
    image: Optional[str] = None
    video_url: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    subtitle: Optional[str] = None
    editorial_lead: Optional[str] = None
    category: Optional[str] = None
    tags: list[str] = strawberry.field(default_factory=list)
    agency: Optional[str] = None
    agency_name: Optional[str] = None
    published_at: Optional[datetime] = None
    extracted_at: Optional[datetime] = None
    # Campos de tema (taxonomia gov.br). Mapeados do índice Typesense; podem
    # estar ausentes em documentos antigos, por isso Optional[str] = None.
    theme_1_level_1_code: Optional[str] = None
    theme_1_level_1_label: Optional[str] = None
    theme_1_level_2_code: Optional[str] = None
    theme_1_level_2_label: Optional[str] = None
    theme_1_level_3_code: Optional[str] = None
    theme_1_level_3_label: Optional[str] = None
    most_specific_theme_code: Optional[str] = None
    most_specific_theme_label: Optional[str] = None

    @strawberry.field(description="Hora de publicação (0-23), derivada de publishedAt")
    def publication_hour(self) -> Optional[int]:
        if self.published_at is None:
            return None
        return self.published_at.hour

    @strawberry.field(
        description=(
            "Features computadas da notícia (entidades, popularidade/trending, "
            "leitura/legibilidade). Carregado sob demanda do Postgres "
            "(news_features) por unique_id via DataLoader; None quando não há "
            "features. Não onera listas/busca que não selecionam este campo."
        )
    )
    async def features(self, info: Info) -> Optional[ArticleFeatures]:
        loader = getattr(info.context, "features_loader", None)
        if loader is None:
            return None
        raw = await loader.load(self.unique_id)
        return article_features_from_json(raw)


@strawberry.type
class ArticlesResult:
    articles: list[Article]
    page: int
    found: int


@strawberry.input
class ArticleFilter:
    agencies: Optional[list[str]] = None
    # `themes`: lista de codes; filtra OR através de L1/L2/L3 code no datasource.
    themes: Optional[list[str]] = None
    tags: Optional[list[str]] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    # Filtra pelo label de nível 1 (página /temas).
    theme_label: Optional[str] = None
    # Quando true, deduplica por content_hash (group_by). Pass-through ao datasource.
    dedup: Optional[bool] = None
    # Fase 2: filtro por entidades (match exato no campo Typesense `entities`)
    # e por sentimento (sentiment_label: positive/neutral/negative).
    entities: Optional[list[str]] = None
    sentiment: Optional[list[str]] = None
    # Fase 4: filtro por entidade canônica (canonical_id/entity_id) — match
    # exato no campo Typesense `entity_canonical` (dedup'd por entidade).
    entity_canonical: Optional[list[str]] = None


@strawberry.enum
class ArticleSort(Enum):
    """Ordenação dos resultados de listagem/busca."""

    RELEVANCE = "relevance"
    DATE = "date"
    TRENDING = "trending"
    VIEWS = "views"


def sort_by_clause(
    sort: Optional[ArticleSort], *, relevance_default: bool
) -> Optional[str]:
    """Mapeia `ArticleSort` para o `sort_by` do Typesense.

    `relevance_default=True` (busca por keyword): None/RELEVANCE preservam a
    relevância de text-match (retorna None → o datasource omite sort_by).
    `relevance_default=False` (listagem sem query): caem para data desc.
    Campos opcionais (trending_score, view_count) ausentes em docs antigos são
    ordenados por último pelo Typesense — comportamento desejado.
    """
    if sort is None or sort == ArticleSort.RELEVANCE:
        return None if relevance_default else "published_at:desc"
    if sort == ArticleSort.DATE:
        return "published_at:desc"
    if sort == ArticleSort.TRENDING:
        return "trending_score:desc"
    if sort == ArticleSort.VIEWS:
        return "view_count:desc"
    return None
