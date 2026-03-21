import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class NewsRecord:
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
    tags: list[str] = field(default_factory=list)
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
    features: Optional[dict[str, Any]] = None


@dataclass
class TypesenseDocRecord(NewsRecord):
    content_embedding: Optional[list[float]] = None
    sentiment_label: Optional[str] = None
    sentiment_score: Optional[float] = None
    trending_score: Optional[float] = None
    word_count: Optional[int] = None
    has_image: Optional[bool] = None
    has_video: Optional[bool] = None
    image_broken: Optional[bool] = None
    readability_flesch: Optional[float] = None


_UPSERT_FEATURES_SQL = """
INSERT INTO news_features (unique_id, features, updated_at)
VALUES ($1, $2::jsonb, NOW())
ON CONFLICT (unique_id) DO UPDATE SET
  features = news_features.features || $2::jsonb,
  updated_at = NOW()
"""

_NEWS_BASE_SQL = """
SELECT n.*,
  t1.code as theme_l1_code, t1.label as theme_l1_label,
  t2.code as theme_l2_code, t2.label as theme_l2_label,
  t3.code as theme_l3_code, t3.label as theme_l3_label,
  tm.code as most_specific_theme_code, tm.label as most_specific_theme_label,
  nf.features
FROM news n
LEFT JOIN themes t1 ON n.theme_l1_id = t1.id
LEFT JOIN themes t2 ON n.theme_l2_id = t2.id
LEFT JOIN themes t3 ON n.theme_l3_id = t3.id
LEFT JOIN themes tm ON n.most_specific_theme_id = tm.id
LEFT JOIN news_features nf ON n.unique_id = nf.unique_id
"""

_NEWS_TYPESENSE_SQL = """
SELECT n.*, n.content_embedding,
  t1.code as theme_l1_code, t1.label as theme_l1_label,
  t2.code as theme_l2_code, t2.label as theme_l2_label,
  t3.code as theme_l3_code, t3.label as theme_l3_label,
  tm.code as most_specific_theme_code, tm.label as most_specific_theme_label,
  nf.features
FROM news n
LEFT JOIN themes t1 ON n.theme_l1_id = t1.id
LEFT JOIN themes t2 ON n.theme_l2_id = t2.id
LEFT JOIN themes t3 ON n.theme_l3_id = t3.id
LEFT JOIN themes tm ON n.most_specific_theme_id = tm.id
LEFT JOIN news_features nf ON n.unique_id = nf.unique_id
"""


def _row_to_news_record(row: dict) -> NewsRecord:
    tags = row.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return NewsRecord(
        unique_id=row["unique_id"],
        title=row["title"],
        url=row["url"],
        image_url=row.get("image_url"),
        video_url=row.get("video_url"),
        content=row.get("content"),
        summary=row.get("summary"),
        subtitle=row.get("subtitle"),
        editorial_lead=row.get("editorial_lead"),
        category=row.get("category"),
        tags=tags,
        agency_key=row.get("agency_key"),
        agency_name=row.get("agency_name"),
        published_at=row.get("published_at"),
        extracted_at=row.get("extracted_at"),
        theme_l1_code=row.get("theme_l1_code"),
        theme_l1_label=row.get("theme_l1_label"),
        theme_l2_code=row.get("theme_l2_code"),
        theme_l2_label=row.get("theme_l2_label"),
        theme_l3_code=row.get("theme_l3_code"),
        theme_l3_label=row.get("theme_l3_label"),
        most_specific_theme_code=row.get("most_specific_theme_code"),
        most_specific_theme_label=row.get("most_specific_theme_label"),
        features=row.get("features"),
    )


def _row_to_typesense_doc(row: dict) -> TypesenseDocRecord:
    tags = row.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    features = row.get("features") or {}
    return TypesenseDocRecord(
        unique_id=row["unique_id"],
        title=row["title"],
        url=row["url"],
        image_url=row.get("image_url"),
        video_url=row.get("video_url"),
        content=row.get("content"),
        summary=row.get("summary"),
        subtitle=row.get("subtitle"),
        editorial_lead=row.get("editorial_lead"),
        category=row.get("category"),
        tags=tags,
        agency_key=row.get("agency_key"),
        agency_name=row.get("agency_name"),
        published_at=row.get("published_at"),
        extracted_at=row.get("extracted_at"),
        theme_l1_code=row.get("theme_l1_code"),
        theme_l1_label=row.get("theme_l1_label"),
        theme_l2_code=row.get("theme_l2_code"),
        theme_l2_label=row.get("theme_l2_label"),
        theme_l3_code=row.get("theme_l3_code"),
        theme_l3_label=row.get("theme_l3_label"),
        most_specific_theme_code=row.get("most_specific_theme_code"),
        most_specific_theme_label=row.get("most_specific_theme_label"),
        features=features,
        content_embedding=row.get("content_embedding"),
        sentiment_label=features.get("sentiment_label"),
        sentiment_score=features.get("sentiment_score"),
        trending_score=features.get("trending_score"),
        word_count=features.get("word_count"),
        has_image=features.get("has_image"),
        has_video=features.get("has_video"),
        image_broken=features.get("image_broken"),
        readability_flesch=features.get("readability_flesch"),
    )


class PostgresDatasource:
    def __init__(self, pool):
        self._pool = pool

    async def get_news_by_id(self, unique_id: str) -> Optional[NewsRecord]:
        query = _NEWS_BASE_SQL + " WHERE n.unique_id = $1"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, unique_id)
        if row is None:
            return None
        return _row_to_news_record(dict(row))

    async def get_news_batch(self, unique_ids: list[str]) -> list[NewsRecord]:
        if not unique_ids:
            return []
        query = _NEWS_BASE_SQL + " WHERE n.unique_id = ANY($1)"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, unique_ids)
        return [_row_to_news_record(dict(r)) for r in rows]

    async def get_news_for_typesense(self, unique_id: str) -> Optional[TypesenseDocRecord]:
        query = _NEWS_TYPESENSE_SQL + " WHERE n.unique_id = $1"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, unique_id)
        if row is None:
            return None
        return _row_to_typesense_doc(dict(row))

    async def upsert_features(self, unique_id: str, features: dict) -> bool:
        features_json = json.dumps(features)
        result = await self._pool.execute(
            _UPSERT_FEATURES_SQL, unique_id, features_json
        )
        # asyncpg execute returns a command tag like "INSERT 0 1"
        return result is not None

    async def batch_upsert_features(
        self, items: list[tuple[str, dict]]
    ) -> tuple[int, int]:
        if not items:
            return (0, 0)
        processed = 0
        failed = 0
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for unique_id, features in items:
                    try:
                        features_json = json.dumps(features)
                        await conn.execute(
                            _UPSERT_FEATURES_SQL, unique_id, features_json
                        )
                        processed += 1
                    except Exception:
                        failed += 1
        return (processed, failed)
