from unittest.mock import MagicMock

import strawberry

from graphql_api.context import GraphQLContext
from graphql_api.datasources.typesense import TypesenseDatasource
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.widgets import (
    MAX_PER_PAGE,
    WidgetQuery,
)


@strawberry.type
class _Query(HealthQuery, WidgetQuery):
    pass


test_schema = strawberry.Schema(query=_Query)


def _mock_typesense_client_for_facets(agencies: list[str], themes: list[str]):
    mock_client = MagicMock()
    mock_client.collections.__getitem__.return_value.documents.search.return_value = {
        "hits": [],
        "found": 0,
        "facet_counts": [
            {
                "field_name": "agency",
                "counts": [{"value": a, "count": 10} for a in agencies],
            },
            {
                "field_name": "category",
                "counts": [{"value": t, "count": 5} for t in themes],
            },
        ],
    }
    return mock_client


def _make_typesense_hit(unique_id: str, title: str, agency: str = "gov") -> dict:
    return {
        "document": {
            "unique_id": unique_id,
            "title": title,
            "url": f"https://example.com/{unique_id}",
            "content": "Some content",
            "tags": ["test"],
            "agency": agency,
        }
    }


def _mock_typesense_client_for_search(hits: list[dict], found: int | None = None):
    mock_client = MagicMock()
    mock_client.collections.__getitem__.return_value.documents.search.return_value = {
        "hits": hits,
        "found": found if found is not None else len(hits),
    }
    return mock_client


def _make_context_with_typesense(mock_client):
    ts_ds = MagicMock(spec=TypesenseDatasource)
    ts_ds.client = mock_client
    ctx = GraphQLContext(typesense_ds=ts_ds)
    return ctx


class TestWidgetConfig:
    def test_widget_config_returns_agencies_and_themes(self):
        agencies = ["agencia-brasil", "gov-br", "planalto"]
        themes = ["economia", "saude", "educacao"]
        mock_client = _mock_typesense_client_for_facets(agencies, themes)
        ctx = _make_context_with_typesense(mock_client)

        result = test_schema.execute_sync(
            """
            {
                widgetConfig {
                    agencies
                    themes
                }
            }
            """,
            context_value=ctx,
        )

        assert result.errors is None, f"Errors: {result.errors}"
        data = result.data["widgetConfig"]
        assert data["agencies"] == agencies
        assert data["themes"] == themes

        # Verify facet search was called with correct params
        search_call = mock_client.collections.__getitem__.return_value.documents.search
        search_call.assert_called_once()
        params = search_call.call_args[0][0]
        assert params["facet_by"] == "agency,category"
        assert params["per_page"] == 0


class TestWidgetArticles:
    def test_widget_articles_returns_paginated(self):
        hits = [
            _make_typesense_hit("art-1", "First Article", "agencia-brasil"),
            _make_typesense_hit("art-2", "Second Article", "agencia-brasil"),
        ]
        mock_client = _mock_typesense_client_for_search(hits, found=15)
        ctx = _make_context_with_typesense(mock_client)

        result = test_schema.execute_sync(
            """
            query($config: WidgetConfigInput!, $page: Int!) {
                widgetArticles(config: $config, page: $page) {
                    articles {
                        uniqueId
                        title
                    }
                    pagination {
                        page
                        limit
                        total
                        hasMore
                    }
                }
            }
            """,
            variable_values={
                "config": {
                    "agencies": ["agencia-brasil"],
                    "themes": ["economia"],
                    "layout": "LIST",
                    "articlesPerPage": 10,
                },
                "page": 1,
            },
            context_value=ctx,
        )

        assert result.errors is None, f"Errors: {result.errors}"
        data = result.data["widgetArticles"]
        assert len(data["articles"]) == 2
        assert data["articles"][0]["uniqueId"] == "art-1"
        assert data["pagination"]["page"] == 1
        assert data["pagination"]["limit"] == 10
        assert data["pagination"]["total"] == 15
        assert data["pagination"]["hasMore"] is True

    def test_widget_articles_coerces_epoch_published_at(self):
        """Regressão: `published_at`/`extracted_at` vêm como epoch int do
        Typesense. Sem coerção para datetime, o Strawberry chamava `.isoformat()`
        num int e quebrava (`'int' object has no attribute 'isoformat'`)."""
        hit = {
            "document": {
                "unique_id": "art-epoch",
                "title": "Com timestamp epoch",
                "url": "https://example.com/art-epoch",
                "agency": "gov",
                "published_at": 1_717_400_000,  # epoch segundos
                "extracted_at": 1_717_400_100,
            }
        }
        mock_client = _mock_typesense_client_for_search([hit], found=1)
        ctx = _make_context_with_typesense(mock_client)

        result = test_schema.execute_sync(
            """
            query($config: WidgetConfigInput!, $page: Int!) {
                widgetArticles(config: $config, page: $page) {
                    articles { uniqueId publishedAt extractedAt }
                }
            }
            """,
            variable_values={"config": {"articlesPerPage": 5}, "page": 1},
            context_value=ctx,
        )

        assert result.errors is None, f"Errors: {result.errors}"
        article = result.data["widgetArticles"]["articles"][0]
        # Serializado como ISO 8601 (não mais erro de isoformat).
        assert article["publishedAt"].startswith("2024-")
        assert article["extractedAt"].startswith("2024-")

    def test_widget_max_per_page_clamped_to_50(self):
        """When articlesPerPage exceeds 50, it must be clamped to 50."""
        mock_client = _mock_typesense_client_for_search([], found=0)
        ctx = _make_context_with_typesense(mock_client)

        result = test_schema.execute_sync(
            """
            query($config: WidgetConfigInput!, $page: Int!) {
                widgetArticles(config: $config, page: $page) {
                    pagination {
                        limit
                    }
                }
            }
            """,
            variable_values={
                "config": {
                    "articlesPerPage": 100,
                },
                "page": 1,
            },
            context_value=ctx,
        )

        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["widgetArticles"]["pagination"]["limit"] == MAX_PER_PAGE

        # Also verify the actual Typesense search used clamped value
        search_call = mock_client.collections.__getitem__.return_value.documents.search
        params = search_call.call_args[0][0]
        assert params["per_page"] == MAX_PER_PAGE
