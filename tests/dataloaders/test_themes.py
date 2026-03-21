from unittest.mock import MagicMock

import pytest

from graphql_api.dataloaders import create_agency_loader, create_theme_loader


def _make_typesense_mock(field_name: str, values: list[dict]) -> MagicMock:
    """Create a mock Typesense client that returns facet data."""
    mock = MagicMock()
    mock.collections.__getitem__.return_value.documents.search.return_value = {
        "facet_counts": [
            {
                "field_name": field_name,
                "counts": values,
            }
        ],
        "found": 0,
        "hits": [],
    }
    return mock


@pytest.mark.asyncio
async def test_theme_dataloader_batches():
    """A single Typesense call should serve multiple .load() calls."""
    ts_mock = _make_typesense_mock(
        "theme_1_level_1_label",
        [
            {"value": "Saude", "count": 50},
            {"value": "Educacao", "count": 30},
        ],
    )
    loader = create_theme_loader(ts_mock)

    # Load two keys — both should resolve from the same batch
    results = await loader.load_many(["Saude", "Educacao"])

    assert len(results) == 2
    assert results[0].code == "Saude"
    assert results[0].label == "Saude"
    assert results[1].code == "Educacao"
    assert results[1].label == "Educacao"

    # Only one Typesense search call should have been made (batched)
    ts_mock.collections.__getitem__.return_value.documents.search.assert_called_once()


@pytest.mark.asyncio
async def test_theme_dataloader_caches_within_request():
    """Subsequent loads of the same key should not trigger another Typesense call."""
    ts_mock = _make_typesense_mock(
        "theme_1_level_1_label",
        [{"value": "Saude", "count": 50}],
    )
    loader = create_theme_loader(ts_mock)

    result1 = await loader.load("Saude")
    result2 = await loader.load("Saude")

    assert result1.code == "Saude"
    assert result2.code == "Saude"
    # Still only one call — the second load was cached
    ts_mock.collections.__getitem__.return_value.documents.search.assert_called_once()


@pytest.mark.asyncio
async def test_agency_dataloader_batches():
    """Agency DataLoader should batch multiple loads into one Typesense call."""
    ts_mock = _make_typesense_mock(
        "agency",
        [
            {"value": "ministerio-saude", "count": 40},
            {"value": "ministerio-educacao", "count": 25},
        ],
    )
    loader = create_agency_loader(ts_mock)

    results = await loader.load_many(["ministerio-saude", "ministerio-educacao"])

    assert len(results) == 2
    assert results[0].code == "ministerio-saude"
    assert results[1].code == "ministerio-educacao"
    ts_mock.collections.__getitem__.return_value.documents.search.assert_called_once()
