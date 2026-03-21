import enum
from typing import Optional

import strawberry

from graphql_api.schema.types.article import Article


@strawberry.enum
class WidgetLayout(enum.Enum):
    LIST = "LIST"
    GRID_2 = "GRID_2"
    GRID_3 = "GRID_3"
    CAROUSEL = "CAROUSEL"


@strawberry.input
class WidgetConfigInput:
    agencies: list[str] = strawberry.field(default_factory=list)
    themes: list[str] = strawberry.field(default_factory=list)
    layout: WidgetLayout = WidgetLayout.LIST
    articles_per_page: int = 10


@strawberry.type
class WidgetConfig:
    agencies: list[str]
    themes: list[str]


@strawberry.type
class WidgetPagination:
    page: int
    limit: int
    total: int
    has_more: bool


@strawberry.type
class WidgetArticlesResult:
    articles: list[Article]
    pagination: WidgetPagination
