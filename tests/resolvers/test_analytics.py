from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import strawberry

from graphql_api.schema.resolvers.analytics import AnalyticsQuery


@dataclass
class FakeContext:
    typesense_ds: Any = None
    postgres_ds: Any = None


@strawberry.type
class Query(AnalyticsQuery):
    pass


test_schema = strawberry.Schema(query=Query)


def _make_ts_mock(response: dict) -> MagicMock:
    """Create a mock typesense_ds that returns the given response from search."""
    mock = MagicMock()
    mock.client.collections.__getitem__.return_value.documents.search.return_value = (
        response
    )
    return mock


@pytest.mark.asyncio
async def test_analytics_kpis():
    ts_mock = _make_ts_mock(
        {
            "found": 150,
            "hits": [],
            "facet_counts": [
                {
                    "field_name": "theme_1_level_1_label",
                    "counts": [
                        {"value": "Saude", "count": 60},
                        {"value": "Educacao", "count": 50},
                        {"value": "Economia", "count": 40},
                    ],
                },
                {
                    "field_name": "agency",
                    "counts": [
                        {"value": "ministerio-saude", "count": 80},
                        {"value": "ministerio-educacao", "count": 70},
                    ],
                },
            ],
        }
    )

    result = await test_schema.execute(
        """
        query {
            analyticsKpis(range: { days: 30 }) {
                total
                activeThemes
                activeAgencies
                dailyAverage
            }
        }
        """,
        context_value=FakeContext(typesense_ds=ts_mock),
    )

    assert result.errors is None
    kpis = result.data["analyticsKpis"]
    assert kpis["total"] == 150
    assert kpis["activeThemes"] == 3
    assert kpis["activeAgencies"] == 2
    assert kpis["dailyAverage"] == 5.0

    # Verify Typesense was called with correct params
    call_args = (
        ts_mock.client.collections.__getitem__
        .return_value.documents.search.call_args
    )
    params = call_args[0][0]
    assert params["per_page"] == 0
    assert "published_at:>=" in params["filter_by"]
    assert "theme_1_level_1_label" in params["facet_by"]
    assert "agency" in params["facet_by"]


@pytest.mark.asyncio
async def test_top_themes():
    ts_mock = _make_ts_mock(
        {
            "found": 100,
            "hits": [],
            "facet_counts": [
                {
                    "field_name": "theme_1_level_1_label",
                    "counts": [
                        {"value": "Saude", "count": 40},
                        {"value": "Educacao", "count": 30},
                        {"value": "Economia", "count": 20},
                        {"value": "Seguranca", "count": 10},
                    ],
                }
            ],
        }
    )

    result = await test_schema.execute(
        """
        query {
            topThemes(range: { days: 7 }, limit: 4) {
                label
                count
            }
        }
        """,
        context_value=FakeContext(typesense_ds=ts_mock),
    )

    assert result.errors is None
    themes = result.data["topThemes"]
    assert len(themes) == 4
    assert themes[0]["label"] == "Saude"
    assert themes[0]["count"] == 40
    assert themes[3]["label"] == "Seguranca"
    assert themes[3]["count"] == 10

    # Verify limit was passed as max_facet_values
    call_args = (
        ts_mock.client.collections.__getitem__
        .return_value.documents.search.call_args
    )
    params = call_args[0][0]
    assert params["max_facet_values"] == 4
    assert params["facet_by"] == "theme_1_level_1_label"


@pytest.mark.asyncio
async def test_top_agencies():
    ts_mock = _make_ts_mock(
        {
            "found": 80,
            "hits": [],
            "facet_counts": [
                {
                    "field_name": "agency",
                    "counts": [
                        {"value": "Ministerio da Saude", "count": 35},
                        {"value": "Ministerio da Educacao", "count": 25},
                        {"value": "Ministerio da Economia", "count": 20},
                    ],
                }
            ],
        }
    )

    result = await test_schema.execute(
        """
        query {
            topAgencies(range: { days: 14 }, limit: 3) {
                name
                count
            }
        }
        """,
        context_value=FakeContext(typesense_ds=ts_mock),
    )

    assert result.errors is None
    agencies = result.data["topAgencies"]
    assert len(agencies) == 3
    assert agencies[0]["name"] == "Ministerio da Saude"
    assert agencies[0]["count"] == 35
    assert agencies[2]["name"] == "Ministerio da Economia"

    # Verify limit was passed as max_facet_values
    call_args = (
        ts_mock.client.collections.__getitem__
        .return_value.documents.search.call_args
    )
    params = call_args[0][0]
    assert params["max_facet_values"] == 3
    assert params["facet_by"] == "agency"


@pytest.mark.asyncio
async def test_articles_timeline():
    ts_mock = _make_ts_mock(
        {
            "found": 50,
            "hits": [],
            "facet_counts": [
                {
                    "field_name": "published_date",
                    "counts": [
                        {"value": "2026-03-18", "count": 15},
                        {"value": "2026-03-17", "count": 20},
                        {"value": "2026-03-19", "count": 15},
                    ],
                }
            ],
        }
    )

    result = await test_schema.execute(
        """
        query {
            articlesTimeline(range: { days: 7 }) {
                date
                count
            }
        }
        """,
        context_value=FakeContext(typesense_ds=ts_mock),
    )

    assert result.errors is None
    timeline = result.data["articlesTimeline"]
    assert len(timeline) == 3
    # Should be sorted by date ascending
    assert timeline[0]["date"] == "2026-03-17"
    assert timeline[0]["count"] == 20
    assert timeline[1]["date"] == "2026-03-18"
    assert timeline[2]["date"] == "2026-03-19"


@pytest.mark.asyncio
async def test_invalid_range_returns_error():
    ts_mock = _make_ts_mock({"found": 0, "hits": [], "facet_counts": []})

    # days = 0
    result = await test_schema.execute(
        """
        query {
            analyticsKpis(range: { days: 0 }) {
                total
            }
        }
        """,
        context_value=FakeContext(typesense_ds=ts_mock),
    )
    assert result.errors is not None
    assert "greater than 0" in str(result.errors[0])

    # days = -5
    result = await test_schema.execute(
        """
        query {
            analyticsKpis(range: { days: -5 }) {
                total
            }
        }
        """,
        context_value=FakeContext(typesense_ds=ts_mock),
    )
    assert result.errors is not None
    assert "greater than 0" in str(result.errors[0])

    # Also test other resolvers with invalid range
    result = await test_schema.execute(
        """
        query {
            topThemes(range: { days: 0 }) { label count }
        }
        """,
        context_value=FakeContext(typesense_ds=ts_mock),
    )
    assert result.errors is not None

    result = await test_schema.execute(
        """
        query {
            topAgencies(range: { days: -1 }) { name count }
        }
        """,
        context_value=FakeContext(typesense_ds=ts_mock),
    )
    assert result.errors is not None

    result = await test_schema.execute(
        """
        query {
            articlesTimeline(range: { days: 0 }) { date count }
        }
        """,
        context_value=FakeContext(typesense_ds=ts_mock),
    )
    assert result.errors is not None


class TestAgencyAnalytics:
    @pytest.mark.asyncio
    async def test_retorna_metricas_por_periodo(self):
        mock_pg = AsyncMock()
        mock_pg.agency_analytics = AsyncMock(return_value=[
            {
                "period": "2024-01",
                "agency_key": "mec",
                "agency_name": "Ministério da Educação",
                "article_count": 45,
                "avg_sentiment_score": 0.62,
                "pct_positive": 0.71,
                "pct_negative": 0.08,
                "avg_readability_flesch": 52.3,
                "avg_word_count": 380.0,
            }
        ])
        query = """
        query {
            agencyAnalytics(
                agencies: ["mec"]
                dateFrom: "2024-01-01"
                dateTo: "2024-01-31"
                granularity: MONTH
            ) {
                period agencyKey agencyName articleCount
                avgSentimentScore pctPositive pctNegative
                avgReadabilityFlesch avgWordCount
            }
        }
        """
        result = await test_schema.execute(
            query,
            context_value=FakeContext(postgres_ds=mock_pg),
        )
        assert result.errors is None
        data = result.data["agencyAnalytics"][0]
        assert data["period"] == "2024-01"
        assert data["agencyKey"] == "mec"
        assert data["articleCount"] == 45
        mock_pg.agency_analytics.assert_awaited_once_with("month", ["mec"], "2024-01-01", "2024-01-31")

    @pytest.mark.asyncio
    async def test_agencias_vazias_levanta_erro(self):
        mock_pg = AsyncMock()
        query = """
        query {
            agencyAnalytics(agencies: [] dateFrom: "2024-01-01" dateTo: "2024-01-31" granularity: MONTH) {
                period
            }
        }
        """
        result = await test_schema.execute(
            query,
            context_value=FakeContext(postgres_ds=mock_pg),
        )
        assert result.errors is not None


class TestTrendingThemes:
    @pytest.mark.asyncio
    async def test_calcula_growth_score_e_filtra_por_threshold(self):
        ts_mock = MagicMock()
        def _search(params):
            return {
                "found": 100,
                "facet_counts": [
                    {"field_name": "theme_1_level_1_label", "counts": [
                        {"value": "Saúde", "count": 21},
                        {"value": "Educação", "count": 2},  # abaixo de min_articles=3
                    ]}
                ]
            }
        ts_mock.client.collections["news"].documents.search.side_effect = _search

        query = """
        query {
            trendingThemes(windowDays: 7 baselineDays: 28 minArticles: 3 growthThreshold: 1.5) {
                themeLabel windowCount baselineDailyAvg growthScore
            }
        }
        """
        result = await test_schema.execute(
            query,
            context_value=FakeContext(typesense_ds=ts_mock),
        )
        assert result.errors is None
        themes = result.data["trendingThemes"]
        # Saúde: window_daily=21/7=3, baseline_daily=21/28=0.75, growth=4.0 >= 1.5 ✓
        assert len(themes) >= 1
        saude = next(t for t in themes if t["themeLabel"] == "Saúde")
        assert saude["growthScore"] > 1.5
        # Educação < min_articles → excluída
        assert all(t["themeLabel"] != "Educação" for t in themes)

    @pytest.mark.asyncio
    async def test_window_maior_que_baseline_levanta_erro(self):
        ts_mock = MagicMock()
        query = """
        query {
            trendingThemes(windowDays: 30 baselineDays: 7) {
                themeLabel
            }
        }
        """
        result = await test_schema.execute(
            query,
            context_value=FakeContext(typesense_ds=ts_mock),
        )
        assert result.errors is not None


class TestAgencyAnalyticsDayGapFill:
    """Testa que granularity=DAY usa generate_series (gap-fill) e MONTH usa DATE_TRUNC."""

    def _make_mock_pool(self, return_rows=None):
        from unittest.mock import AsyncMock, MagicMock

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=return_rows or [])
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
        return mock_pool, mock_conn

    @pytest.mark.asyncio
    async def test_day_granularity_usa_generate_series(self):
        from graphql_api.datasources.postgres import PostgresDatasource

        mock_pool, mock_conn = self._make_mock_pool()
        ds = PostgresDatasource(pool=mock_pool)
        await ds.agency_analytics("day", ["mec"], "2026-06-01", "2026-06-07")

        sql_used = mock_conn.fetch.call_args[0][0]
        assert "generate_series" in sql_used
        # parâmetro $1 não é mais a granularidade — é a lista de agências
        assert "DATE_TRUNC($1" not in sql_used

    @pytest.mark.asyncio
    async def test_month_granularity_usa_date_trunc(self):
        from graphql_api.datasources.postgres import PostgresDatasource

        mock_pool, mock_conn = self._make_mock_pool()
        ds = PostgresDatasource(pool=mock_pool)
        await ds.agency_analytics("month", ["mec"], "2026-06-01", "2026-06-30")

        sql_used = mock_conn.fetch.call_args[0][0]
        assert "generate_series" not in sql_used
        assert "DATE_TRUNC($1" in sql_used

    @pytest.mark.asyncio
    async def test_day_retorna_zeros_para_dias_sem_artigos(self):
        """Verifica que dias sem artigos retornam article_count=0 via COALESCE."""
        from graphql_api.datasources.postgres import PostgresDatasource

        # Simula DB retornando 2 dias: um com artigos e um com zeros
        mock_rows = [
            {
                "period": "2026-06-01",
                "agency_key": "mec",
                "agency_name": "Ministério da Educação",
                "article_count": 5,
                "avg_sentiment_score": 0.6,
                "pct_positive": 0.7,
                "pct_negative": 0.1,
                "avg_readability_flesch": 55.0,
                "avg_word_count": 300.0,
            },
            {
                "period": "2026-06-02",
                "agency_key": "mec",
                "agency_name": "Ministério da Educação",
                "article_count": 0,
                "avg_sentiment_score": None,
                "pct_positive": None,
                "pct_negative": None,
                "avg_readability_flesch": None,
                "avg_word_count": None,
            },
        ]
        mock_pool, _ = self._make_mock_pool(return_rows=mock_rows)
        ds = PostgresDatasource(pool=mock_pool)
        result = await ds.agency_analytics("day", ["mec"], "2026-06-01", "2026-06-02")

        assert len(result) == 2
        assert result[0]["article_count"] == 5
        assert result[1]["article_count"] == 0
        assert result[1]["avg_sentiment_score"] is None
