from typing import Optional

import strawberry
from strawberry.types import Info

from graphql_api.auth.guards import IsInternal
from graphql_api.datasources.postgres import (
    BigQueryRecord,
    IntegrityCandidateRecord,
    NewsRecord,
    SimilarArticleRecord,
    TypesenseDocRecord,
)
from graphql_api.schema.types.internal import (
    BigQueryRecordType,
    IntegrityCandidateType,
    NewsRecordType,
    SimilarArticleRecordType,
    TypesenseDocRecordType,
)


def _to_news_record_type(rec: NewsRecord) -> NewsRecordType:
    return NewsRecordType(
        unique_id=rec.unique_id,
        title=rec.title,
        url=rec.url,
        image_url=rec.image_url,
        video_url=rec.video_url,
        content=rec.content,
        summary=rec.summary,
        subtitle=rec.subtitle,
        editorial_lead=rec.editorial_lead,
        category=rec.category,
        tags=rec.tags,
        agency_key=rec.agency_key,
        agency_name=rec.agency_name,
        published_at=rec.published_at,
        extracted_at=rec.extracted_at,
        theme_l1_code=rec.theme_l1_code,
        theme_l1_label=rec.theme_l1_label,
        theme_l2_code=rec.theme_l2_code,
        theme_l2_label=rec.theme_l2_label,
        theme_l3_code=rec.theme_l3_code,
        theme_l3_label=rec.theme_l3_label,
        most_specific_theme_code=rec.most_specific_theme_code,
        most_specific_theme_label=rec.most_specific_theme_label,
        features=rec.features,
    )


def _to_bigquery_record_type(rec: BigQueryRecord) -> BigQueryRecordType:
    return BigQueryRecordType(
        unique_id=rec.unique_id,
        title=rec.title,
        url=rec.url,
        image_url=rec.image_url,
        video_url=rec.video_url,
        content=rec.content,
        summary=rec.summary,
        subtitle=rec.subtitle,
        editorial_lead=rec.editorial_lead,
        category=rec.category,
        tags=rec.tags,
        agency_key=rec.agency_key,
        agency_name=rec.agency_name,
        published_at=rec.published_at,
        extracted_at=rec.extracted_at,
        theme_l1_code=rec.theme_l1_code,
        theme_l1_label=rec.theme_l1_label,
        theme_l2_code=rec.theme_l2_code,
        theme_l2_label=rec.theme_l2_label,
        theme_l3_code=rec.theme_l3_code,
        theme_l3_label=rec.theme_l3_label,
        most_specific_theme_code=rec.most_specific_theme_code,
        most_specific_theme_label=rec.most_specific_theme_label,
        features=rec.features,
        sentiment_label=rec.sentiment_label,
        sentiment_score=rec.sentiment_score,
        trending_score=rec.trending_score,
        word_count=rec.word_count,
        has_image=rec.has_image,
        has_video=rec.has_video,
        image_broken=rec.image_broken,
        readability_flesch=rec.readability_flesch,
    )


def _to_similar_article_type(rec: SimilarArticleRecord) -> SimilarArticleRecordType:
    return SimilarArticleRecordType(
        unique_id=rec.unique_id,
        similarity=rec.similarity,
    )


def _to_integrity_candidate_type(rec: IntegrityCandidateRecord) -> IntegrityCandidateType:
    return IntegrityCandidateType(
        unique_id=rec.unique_id,
        url=rec.url,
        image_url=rec.image_url,
        published_at=rec.published_at,
        integrity=rec.integrity,
    )


def _to_typesense_doc_type(rec: TypesenseDocRecord) -> TypesenseDocRecordType:
    return TypesenseDocRecordType(
        unique_id=rec.unique_id,
        title=rec.title,
        url=rec.url,
        image_url=rec.image_url,
        video_url=rec.video_url,
        content=rec.content,
        summary=rec.summary,
        subtitle=rec.subtitle,
        editorial_lead=rec.editorial_lead,
        category=rec.category,
        tags=rec.tags,
        agency_key=rec.agency_key,
        agency_name=rec.agency_name,
        published_at=rec.published_at,
        extracted_at=rec.extracted_at,
        theme_l1_code=rec.theme_l1_code,
        theme_l1_label=rec.theme_l1_label,
        theme_l2_code=rec.theme_l2_code,
        theme_l2_label=rec.theme_l2_label,
        theme_l3_code=rec.theme_l3_code,
        theme_l3_label=rec.theme_l3_label,
        most_specific_theme_code=rec.most_specific_theme_code,
        most_specific_theme_label=rec.most_specific_theme_label,
        features=rec.features,
        content_embedding=rec.content_embedding,
        sentiment_label=rec.sentiment_label,
        sentiment_score=rec.sentiment_score,
        trending_score=rec.trending_score,
        word_count=rec.word_count,
        has_image=rec.has_image,
        has_video=rec.has_video,
        image_broken=rec.image_broken,
        readability_flesch=rec.readability_flesch,
    )


@strawberry.type
class InternalQuery:
    @strawberry.field(
        description="Fetch a single news record by unique_id",
        permission_classes=[IsInternal],
    )
    async def news_by_id(
        self, info: Info, unique_id: str
    ) -> Optional[NewsRecordType]:
        ctx = info.context
        ds = ctx.postgres_ds
        rec = await ds.get_news_by_id(unique_id)
        if rec is None:
            return None
        return _to_news_record_type(rec)

    @strawberry.field(
        description="Fetch a batch of news records by unique_ids",
        permission_classes=[IsInternal],
    )
    async def news_batch(
        self, info: Info, unique_ids: list[str]
    ) -> list[NewsRecordType]:
        ctx = info.context
        ds = ctx.postgres_ds
        records = await ds.get_news_batch(unique_ids)
        return [_to_news_record_type(r) for r in records]

    @strawberry.field(
        description="Fetch a news record formatted for Typesense indexing",
        permission_classes=[IsInternal],
    )
    async def news_for_typesense(
        self, info: Info, unique_id: str
    ) -> Optional[TypesenseDocRecordType]:
        ctx = info.context
        ds = ctx.postgres_ds
        rec = await ds.get_news_for_typesense(unique_id)
        if rec is None:
            return None
        return _to_typesense_doc_type(rec)

    @strawberry.field(
        description="Fetch a batch of news records for BigQuery export",
        permission_classes=[IsInternal],
    )
    async def news_batch_for_bigquery(
        self,
        info: Info,
        start_date: str,
        end_date: str,
        batch_size: int = 100,
        cursor: Optional[str] = None,
    ) -> list[BigQueryRecordType]:
        ctx = info.context
        ds = ctx.postgres_ds
        records = await ds.get_news_batch_for_bigquery(
            start_date, end_date, batch_size, cursor
        )
        return [_to_bigquery_record_type(r) for r in records]

    @strawberry.field(
        description="Find articles similar to a given article using embeddings",
        permission_classes=[IsInternal],
    )
    async def similar_articles(
        self,
        info: Info,
        unique_id: str,
        threshold: float = 0.8,
        limit: int = 5,
    ) -> list[SimilarArticleRecordType]:
        ctx = info.context
        ds = ctx.postgres_ds
        records = await ds.get_similar_articles(unique_id, threshold, limit)
        return [_to_similar_article_type(r) for r in records]

    @strawberry.field(
        description="Fetch a batch of articles needing integrity checks",
        permission_classes=[IsInternal],
    )
    async def integrity_batch(
        self, info: Info, batch_size: int = 50
    ) -> list[IntegrityCandidateType]:
        ctx = info.context
        ds = ctx.postgres_ds
        records = await ds.get_integrity_batch(batch_size)
        return [_to_integrity_candidate_type(r) for r in records]
