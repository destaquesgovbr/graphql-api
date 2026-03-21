from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
import strawberry

from graphql_api.schema.resolvers.analytics import AnalyticsQuery


@dataclass
class FakeContext:
    typesense_ds: Any = None


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
