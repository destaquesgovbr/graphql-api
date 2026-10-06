import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphql_api.datasources.postgres import (
    BigQueryRecord,
    PostgresDatasource,
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
        "features": {
            "sentiment_label": "positive",
            "sentiment_score": 0.85,
            "trending_score": 12.5,
            "word_count": 350,
            "has_image": True,
            "has_video": False,
            "image_broken": False,
            "readability_flesch": 45.2,
        },
    }
    base.update(overrides)
    return base


def _make_mock_pool(fetch_result=None):
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=fetch_result or [])

    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm

    return pool, conn


class TestGetNewsBatchForBigquery:
    @pytest.mark.asyncio
    async def test_news_batch_for_bigquery_returns_records(self):
        rows = [_sample_row(unique_id=f"news-{i:03d}") for i in range(3)]
        pool, conn = _make_mock_pool(fetch_result=rows)
        ds = PostgresDatasource(pool)

        result = await ds.get_news_batch_for_bigquery("2024-06-01", "2024-06-30")

        assert len(result) == 3
        assert all(isinstance(r, BigQueryRecord) for r in result)
        assert result[0].unique_id == "news-000"
        assert result[0].sentiment_label == "positive"
        assert result[0].word_count == 350
        assert result[0].has_image is True
        conn.fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bigquery_empty_range_returns_empty(self):
        pool, conn = _make_mock_pool(fetch_result=[])
        ds = PostgresDatasource(pool)

        result = await ds.get_news_batch_for_bigquery("2024-01-01", "2024-01-01")

        assert result == []
        conn.fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bigquery_pagination_with_cursor(self):
        rows = [_sample_row(unique_id=f"news-{i:03d}") for i in range(5, 8)]
        pool, conn = _make_mock_pool(fetch_result=rows)
        ds = PostgresDatasource(pool)

        result = await ds.get_news_batch_for_bigquery(
            "2024-06-01", "2024-06-30", batch_size=3, cursor="news-004"
        )

        assert len(result) == 3
        assert result[0].unique_id == "news-005"
        # Verify cursor was passed as parameter
        call_args = conn.fetch.call_args
        assert "news-004" in call_args[0]
        conn.fetch.assert_awaited_once()


class TestSentimentoNoRegistroBigquery:
    """F0c: o enrichment-worker grava `features.sentiment.{label,score}`; as chaves
    planas `sentiment_label`/`sentiment_score` são legado (fallback)."""

    @pytest.mark.asyncio
    async def test_le_sentimento_aninhado(self):
        row = _sample_row(features={"sentiment": {"label": "negative", "score": -0.4}, "word_count": 120})
        pool, _ = _make_mock_pool(fetch_result=[row])
        ds = PostgresDatasource(pool)

        [rec] = await ds.get_news_batch_for_bigquery("2026-09-01", "2026-09-30")

        assert rec.sentiment_label == "negative"
        assert rec.sentiment_score == -0.4
        assert rec.word_count == 120

    @pytest.mark.asyncio
    async def test_cai_para_chaves_planas_legadas(self):
        row = _sample_row(features={"sentiment_label": "neutral", "sentiment_score": 0.05})
        pool, _ = _make_mock_pool(fetch_result=[row])
        ds = PostgresDatasource(pool)

        [rec] = await ds.get_news_batch_for_bigquery("2026-09-01", "2026-09-30")

        assert rec.sentiment_label == "neutral"
        assert rec.sentiment_score == 0.05

    @pytest.mark.asyncio
    async def test_aninhado_prevalece_sobre_plano(self):
        row = _sample_row(
            features={
                "sentiment": {"label": "positive", "score": 0.9},
                "sentiment_label": "negative",
                "sentiment_score": -0.9,
            }
        )
        pool, _ = _make_mock_pool(fetch_result=[row])
        ds = PostgresDatasource(pool)

        [rec] = await ds.get_news_batch_for_bigquery("2026-09-01", "2026-09-30")

        assert rec.sentiment_label == "positive"
        assert rec.sentiment_score == 0.9

    @pytest.mark.asyncio
    async def test_features_em_str_json_como_o_asyncpg_entrega(self):
        # O pool não registra codec de JSONB: em produção `features` chega como str.
        row = _sample_row(features=json.dumps({"sentiment": {"label": "positive", "score": 0.7}, "word_count": 42}))
        pool, _ = _make_mock_pool(fetch_result=[row])
        ds = PostgresDatasource(pool)

        [rec] = await ds.get_news_batch_for_bigquery("2026-09-01", "2026-09-30")

        assert rec.sentiment_label == "positive"
        assert rec.sentiment_score == 0.7
        assert rec.word_count == 42
        assert rec.features == {"sentiment": {"label": "positive", "score": 0.7}, "word_count": 42}

    @pytest.mark.asyncio
    async def test_features_str_json_nao_objeto_vira_vazio(self):
        # JSONB `null`/array não derruba o lote: tratado como "sem features".
        row = _sample_row(features="null")
        pool, _ = _make_mock_pool(fetch_result=[row])
        ds = PostgresDatasource(pool)

        [rec] = await ds.get_news_batch_for_bigquery("2026-09-01", "2026-09-30")

        assert rec.features == {}
        assert rec.sentiment_label is None
        assert rec.sentiment_score is None

    @pytest.mark.asyncio
    async def test_sem_sentimento_fica_nulo(self):
        row = _sample_row(features={"word_count": 10})
        pool, _ = _make_mock_pool(fetch_result=[row])
        ds = PostgresDatasource(pool)

        [rec] = await ds.get_news_batch_for_bigquery("2026-09-01", "2026-09-30")

        assert rec.sentiment_label is None
        assert rec.sentiment_score is None
