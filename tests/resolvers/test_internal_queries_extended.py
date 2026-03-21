from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import strawberry

from graphql_api.context import GraphQLContext, ServiceAccount
from graphql_api.datasources.postgres import (
    BigQueryRecord,
    IntegrityCandidateRecord,
    SimilarArticleRecord,
)
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.internal_queries import InternalQuery


@strawberry.type
class _Query(HealthQuery, InternalQuery):
    pass


test_schema = strawberry.Schema(query=_Query)


def _make_internal_context(postgres_ds=None):
    ctx = GraphQLContext(postgres_ds=postgres_ds)
    ctx.service_account = ServiceAccount(email="worker@project.iam.gserviceaccount.com")
    return ctx


def _make_public_context():
    return GraphQLContext()


def _sample_bigquery_record(**overrides) -> BigQueryRecord:
    defaults = dict(
        unique_id="news-001",
        title="Governo anuncia programa",
        url="https://gov.br/news/001",
        image_url="https://gov.br/img/001.jpg",
        video_url=None,
        content="Conteudo completo.",
        summary="Resumo.",
        subtitle="Subtitulo",
        editorial_lead="Lead",
        category="economia",
        tags=["economia", "governo"],
        agency_key="agencia-brasil",
        agency_name="Agencia Brasil",
        published_at=datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        extracted_at=datetime(2024, 6, 15, 11, 0, 0, tzinfo=timezone.utc),
        theme_l1_code="ECO",
        theme_l1_label="Economia",
        theme_l2_code="ECO-FIN",
        theme_l2_label="Financas",
        theme_l3_code=None,
        theme_l3_label=None,
        most_specific_theme_code="ECO-FIN",
        most_specific_theme_label="Financas",
        features={"word_count": 350},
        sentiment_label="positive",
        sentiment_score=0.85,
        trending_score=12.5,
        word_count=350,
        has_image=True,
        has_video=False,
        image_broken=False,
        readability_flesch=45.2,
    )
    defaults.update(overrides)
    return BigQueryRecord(**defaults)


class TestNewsBatchForBigqueryQuery:
    @pytest.mark.asyncio
    async def test_news_batch_for_bigquery_query(self):
        mock_pg = AsyncMock()
        records = [_sample_bigquery_record(unique_id=f"news-{i:03d}") for i in range(2)]
        mock_pg.get_news_batch_for_bigquery = AsyncMock(return_value=records)

        result = await test_schema.execute(
            """
            query($startDate: String!, $endDate: String!, $batchSize: Int) {
                newsBatchForBigquery(startDate: $startDate, endDate: $endDate, batchSize: $batchSize) {
                    uniqueId
                    title
                    sentimentLabel
                    sentimentScore
                    wordCount
                    hasImage
                    readabilityFlesch
                    themeL1Code
                    features
                }
            }
            """,
            variable_values={
                "startDate": "2024-06-01",
                "endDate": "2024-06-30",
                "batchSize": 100,
            },
            context_value=_make_internal_context(postgres_ds=mock_pg),
        )

        assert result.errors is None, f"Errors: {result.errors}"
        data = result.data["newsBatchForBigquery"]
        assert len(data) == 2
        assert data[0]["uniqueId"] == "news-000"
        assert data[0]["sentimentLabel"] == "positive"
        assert data[0]["sentimentScore"] == 0.85
        assert data[0]["wordCount"] == 350
        assert data[0]["hasImage"] is True
        assert data[0]["themeL1Code"] == "ECO"
        mock_pg.get_news_batch_for_bigquery.assert_awaited_once_with(
            "2024-06-01", "2024-06-30", 100, None
        )

    @pytest.mark.asyncio
    async def test_news_batch_for_bigquery_forbidden_without_service_account(self):
        result = await test_schema.execute(
            """
            query {
                newsBatchForBigquery(startDate: "2024-06-01", endDate: "2024-06-30") {
                    uniqueId
                }
            }
            """,
            context_value=_make_public_context(),
        )

        assert result.errors is not None
        assert "FORBIDDEN" in str(result.errors[0].message)


class TestSimilarArticlesQuery:
    @pytest.mark.asyncio
    async def test_similar_articles_query(self):
        mock_pg = AsyncMock()
        records = [
            SimilarArticleRecord(unique_id="news-002", similarity=0.95),
            SimilarArticleRecord(unique_id="news-003", similarity=0.88),
        ]
        mock_pg.get_similar_articles = AsyncMock(return_value=records)

        result = await test_schema.execute(
            """
            query($uniqueId: String!, $threshold: Float, $limit: Int) {
                similarArticles(uniqueId: $uniqueId, threshold: $threshold, limit: $limit) {
                    uniqueId
                    similarity
                }
            }
            """,
            variable_values={
                "uniqueId": "news-001",
                "threshold": 0.8,
                "limit": 5,
            },
            context_value=_make_internal_context(postgres_ds=mock_pg),
        )

        assert result.errors is None, f"Errors: {result.errors}"
        data = result.data["similarArticles"]
        assert len(data) == 2
        assert data[0]["uniqueId"] == "news-002"
        assert data[0]["similarity"] == 0.95
        assert data[1]["uniqueId"] == "news-003"
        assert data[1]["similarity"] == 0.88
        mock_pg.get_similar_articles.assert_awaited_once_with("news-001", 0.8, 5)

    @pytest.mark.asyncio
    async def test_similar_articles_forbidden_without_service_account(self):
        result = await test_schema.execute(
            """
            query {
                similarArticles(uniqueId: "news-001") {
                    uniqueId
                    similarity
                }
            }
            """,
            context_value=_make_public_context(),
        )

        assert result.errors is not None
        assert "FORBIDDEN" in str(result.errors[0].message)


class TestIntegrityBatchQuery:
    @pytest.mark.asyncio
    async def test_integrity_batch_query(self):
        mock_pg = AsyncMock()
        records = [
            IntegrityCandidateRecord(
                unique_id="news-001",
                url="https://gov.br/news/001",
                image_url="https://gov.br/img/001.jpg",
                published_at=datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
                integrity=None,
            ),
            IntegrityCandidateRecord(
                unique_id="news-002",
                url="https://gov.br/news/002",
                image_url=None,
                published_at=datetime(2024, 6, 14, 10, 0, 0, tzinfo=timezone.utc),
                integrity={"checked_at": "2024-06-01T00:00:00Z", "url_ok": True},
            ),
        ]
        mock_pg.get_integrity_batch = AsyncMock(return_value=records)

        result = await test_schema.execute(
            """
            query($batchSize: Int) {
                integrityBatch(batchSize: $batchSize) {
                    uniqueId
                    url
                    imageUrl
                    publishedAt
                    integrity
                }
            }
            """,
            variable_values={"batchSize": 50},
            context_value=_make_internal_context(postgres_ds=mock_pg),
        )

        assert result.errors is None, f"Errors: {result.errors}"
        data = result.data["integrityBatch"]
        assert len(data) == 2
        assert data[0]["uniqueId"] == "news-001"
        assert data[0]["url"] == "https://gov.br/news/001"
        assert data[0]["integrity"] is None
        assert data[1]["uniqueId"] == "news-002"
        assert data[1]["integrity"]["url_ok"] is True
        mock_pg.get_integrity_batch.assert_awaited_once_with(50)

    @pytest.mark.asyncio
    async def test_integrity_batch_forbidden_without_service_account(self):
        result = await test_schema.execute(
            """
            query {
                integrityBatch {
                    uniqueId
                }
            }
            """,
            context_value=_make_public_context(),
        )

        assert result.errors is not None
        assert "FORBIDDEN" in str(result.errors[0].message)
