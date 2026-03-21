from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphql_api.datasources.postgres import (
    NewsRecord,
    PostgresDatasource,
    TypesenseDocRecord,
)


def _sample_row(**overrides) -> dict:
    base = {
        "unique_id": "news-001",
        "title": "Governo anuncia novo programa",
        "url": "https://gov.br/news/001",
        "image_url": "https://gov.br/img/001.jpg",
        "video_url": None,
        "content": "Conteudo completo da noticia.",
        "summary": "Resumo da noticia.",
        "subtitle": "Subtitulo",
        "editorial_lead": "Lead editorial",
        "category": "economia",
        "tags": ["economia", "governo"],
        "agency_key": "agencia-brasil",
        "agency_name": "Agencia Brasil",
        "published_at": datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        "extracted_at": datetime(2024, 6, 15, 11, 0, 0, tzinfo=timezone.utc),
        "theme_l1_code": "ECO",
        "theme_l1_label": "Economia",
        "theme_l2_code": "ECO-FIN",
        "theme_l2_label": "Financas",
        "theme_l3_code": None,
        "theme_l3_label": None,
        "most_specific_theme_code": "ECO-FIN",
        "most_specific_theme_label": "Financas",
        "features": None,
    }
    base.update(overrides)
    return base


def _make_mock_pool(fetchrow_result=None, fetch_result=None):
    """Create a mock asyncpg pool with acquire context manager."""
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    conn.fetch = AsyncMock(return_value=fetch_result or [])

    # Make pool.acquire() an async context manager
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm

    return pool, conn


class TestGetNewsById:
    @pytest.mark.asyncio
    async def test_get_news_by_id_returns_record(self):
        row = _sample_row()
        pool, conn = _make_mock_pool(fetchrow_result=row)
        ds = PostgresDatasource(pool)

        result = await ds.get_news_by_id("news-001")

        assert result is not None
        assert isinstance(result, NewsRecord)
        assert result.unique_id == "news-001"
        assert result.title == "Governo anuncia novo programa"
        assert result.theme_l1_code == "ECO"
        assert result.theme_l1_label == "Economia"
        assert result.agency_key == "agencia-brasil"
        assert result.tags == ["economia", "governo"]
        conn.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_news_by_id_not_found_returns_none(self):
        pool, conn = _make_mock_pool(fetchrow_result=None)
        ds = PostgresDatasource(pool)

        result = await ds.get_news_by_id("nonexistent")

        assert result is None
        conn.fetchrow.assert_awaited_once()


class TestGetNewsBatch:
    @pytest.mark.asyncio
    async def test_get_news_batch_returns_list(self):
        rows = [_sample_row(unique_id=f"news-{i}") for i in range(3)]
        pool, conn = _make_mock_pool(fetch_result=rows)
        ds = PostgresDatasource(pool)

        result = await ds.get_news_batch(["news-0", "news-1", "news-2"])

        assert len(result) == 3
        assert all(isinstance(r, NewsRecord) for r in result)
        assert result[0].unique_id == "news-0"
        assert result[2].unique_id == "news-2"
        conn.fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_news_batch_empty_returns_empty(self):
        pool, conn = _make_mock_pool()
        ds = PostgresDatasource(pool)

        result = await ds.get_news_batch([])

        assert result == []
        # Should not even call the database for empty list
        conn.fetch.assert_not_awaited()


class TestGetNewsForTypesense:
    @pytest.mark.asyncio
    async def test_get_news_for_typesense_includes_embeddings_and_features(self):
        features = {
            "sentiment_label": "positive",
            "sentiment_score": 0.85,
            "trending_score": 12.5,
            "word_count": 350,
            "has_image": True,
            "has_video": False,
            "image_broken": False,
            "readability_flesch": 45.2,
        }
        row = _sample_row(
            content_embedding=[0.1, 0.2, 0.3],
            features=features,
        )
        pool, conn = _make_mock_pool(fetchrow_result=row)
        ds = PostgresDatasource(pool)

        result = await ds.get_news_for_typesense("news-001")

        assert result is not None
        assert isinstance(result, TypesenseDocRecord)
        assert result.unique_id == "news-001"
        assert result.content_embedding == [0.1, 0.2, 0.3]
        assert result.sentiment_label == "positive"
        assert result.sentiment_score == 0.85
        assert result.trending_score == 12.5
        assert result.word_count == 350
        assert result.has_image is True
        assert result.has_video is False
        assert result.image_broken is False
        assert result.readability_flesch == 45.2
        assert result.features == features
        conn.fetchrow.assert_awaited_once()
