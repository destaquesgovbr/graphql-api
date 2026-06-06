"""Tipos Strawberry para marketplace.

Fase A1: `MarketplaceListing` continua sendo definido manualmente como
`@strawberry.type` porque carrega campos contextuais (`has_liked`,
`has_followed`) que sao calculados pelo resolver e nao existem no modelo
Pydantic da fronteira Firestore (`MarketplaceListingData`).

`MarketplaceRecorte` poderia ser gerado a partir de `RecorteData`, mas
permanece manual aqui para nao misturar a hierarquia de tipos do clipping com
a do marketplace — duas listas independentes que podem evoluir de forma
diferente (ex.: campos analytics no recorte do marketplace).
"""

from datetime import datetime
from typing import Optional

import strawberry


@strawberry.type
class MarketplaceRecorte:
    id: str
    title: str
    themes: list[str]
    agencies: list[str]
    keywords: list[str]


@strawberry.type
class MarketplaceListing:
    id: str
    author_user_id: str
    author_display_name: str
    source_clipping_id: str
    name: str
    description: Optional[str] = None
    recortes: list[MarketplaceRecorte] = strawberry.field(default_factory=list)
    prompt: Optional[str] = None
    schedule: Optional[str] = None
    like_count: int = 0
    follower_count: int = 0
    clone_count: int = 0
    published_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    active: bool = True
    has_liked: Optional[bool] = None
    has_followed: Optional[bool] = None


@strawberry.type
class MarketplaceListingsResult:
    listings: list[MarketplaceListing]
    total: int


@strawberry.input
class PublishInput:
    name: str
    description: Optional[str] = None
