from datetime import datetime
from typing import Optional

import strawberry


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
