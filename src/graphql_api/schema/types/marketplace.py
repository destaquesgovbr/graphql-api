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
