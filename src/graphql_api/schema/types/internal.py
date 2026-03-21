from datetime import datetime
from typing import Optional

import strawberry
from strawberry.scalars import JSON


@strawberry.input
class FeatureUpsertInput:
    unique_id: str
    features: JSON


@strawberry.type
class BatchResult:
    processed: int
    failed: int


@strawberry.type
class NewsRecordType:
    unique_id: str
    title: str
    url: str
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    subtitle: Optional[str] = None
    editorial_lead: Optional[str] = None
    category: Optional[str] = None
    tags: list[str] = strawberry.field(default_factory=list)
    agency_key: Optional[str] = None
    agency_name: Optional[str] = None
    published_at: Optional[datetime] = None
    extracted_at: Optional[datetime] = None
    theme_l1_code: Optional[str] = None
    theme_l1_label: Optional[str] = None
    theme_l2_code: Optional[str] = None
    theme_l2_label: Optional[str] = None
    theme_l3_code: Optional[str] = None
    theme_l3_label: Optional[str] = None
    most_specific_theme_code: Optional[str] = None
    most_specific_theme_label: Optional[str] = None
    features: Optional[JSON] = None


@strawberry.type
class TypesenseDocRecordType:
    unique_id: str
    title: str
    url: str
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    subtitle: Optional[str] = None
    editorial_lead: Optional[str] = None
    category: Optional[str] = None
    tags: list[str] = strawberry.field(default_factory=list)
    agency_key: Optional[str] = None
    agency_name: Optional[str] = None
    published_at: Optional[datetime] = None
    extracted_at: Optional[datetime] = None
    theme_l1_code: Optional[str] = None
    theme_l1_label: Optional[str] = None
    theme_l2_code: Optional[str] = None
    theme_l2_label: Optional[str] = None
    theme_l3_code: Optional[str] = None
    theme_l3_label: Optional[str] = None
    most_specific_theme_code: Optional[str] = None
    most_specific_theme_label: Optional[str] = None
    features: Optional[JSON] = None
    content_embedding: Optional[list[float]] = None
    sentiment_label: Optional[str] = None
    sentiment_score: Optional[float] = None
    trending_score: Optional[float] = None
    word_count: Optional[int] = None
    has_image: Optional[bool] = None
    has_video: Optional[bool] = None
    image_broken: Optional[bool] = None
    readability_flesch: Optional[float] = None
