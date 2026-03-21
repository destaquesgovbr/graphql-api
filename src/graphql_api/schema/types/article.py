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


@strawberry.type
class ArticlesResult:
    articles: list[Article]
    page: int
    found: int


@strawberry.input
class ArticleFilter:
    agencies: Optional[list[str]] = None
    themes: Optional[list[str]] = None
    tags: Optional[list[str]] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
