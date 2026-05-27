from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
import strawberry

from graphql_api.schema.resolvers.metadata import MetadataQuery


def _make_typesense_mock(facet_field: str, values: list[dict]) -> MagicMock:
    mock = MagicMock()
    mock.client.collections.__getitem__.return_value.documents.search.return_value = {
        "facet_counts": [
            {"field_name": facet_field, "counts": values}
        ],
        "found": 0,
        "hits": [],
    }
    return mock


@dataclass
class FakeContext:
    typesense_ds: Any = None


@strawberry.type
class Query(MetadataQuery):
    pass


test_schema = strawberry.Schema(query=Query)


@pytest.mark.asyncio
async def test_themes_query_returns_list():
    ts_mock = _make_typesense_mock(
        "theme_1_level_1_label",
        [
            {"value": "Saude", "count": 50},
            {"value": "Educacao", "count": 30},
            {"value": "Seguranca", "count": 20},
        ],
    )
    result = await test_schema.execute(
        "{ themes { code label } }",
        context_value=FakeContext(typesense_ds=ts_mock),
    )

    assert result.errors is None
    themes = result.data["themes"]
    assert len(themes) == 3
    assert themes[0]["code"] == "Saude"
    assert themes[0]["label"] == "Saude"
    assert themes[1]["code"] == "Educacao"


@pytest.mark.asyncio
async def test_agencies_query_returns_list():
    ts_mock = _make_typesense_mock(
        "agency",
        [
            {"value": "ministerio-saude", "count": 40},
            {"value": "ministerio-educacao", "count": 25},
        ],
    )
    result = await test_schema.execute(
        "{ agencies { code label } }",
        context_value=FakeContext(typesense_ds=ts_mock),
    )

    assert result.errors is None
    agencies = result.data["agencies"]
    assert len(agencies) == 2
    assert agencies[0]["code"] == "ministerio-saude"
    assert agencies[1]["code"] == "ministerio-educacao"


@pytest.mark.asyncio
async def test_popular_tags_returns_limited_list():
    all_tags = [{"value": f"tag-{i}", "count": 100 - i} for i in range(30)]
    ts_mock = _make_typesense_mock("tags", all_tags[:5])

    result = await test_schema.execute(
        "{ popularTags(limit: 5) { label count } }",
        context_value=FakeContext(typesense_ds=ts_mock),
    )

    assert result.errors is None
    tags = result.data["popularTags"]
    assert len(tags) == 5
    assert tags[0]["label"] == "tag-0"
    assert tags[0]["count"] == 100

    # Verify the limit was passed to Typesense
    call_args = ts_mock.client.collections.__getitem__.return_value.documents.search.call_args
    assert call_args[0][0]["max_facet_values"] == 5
