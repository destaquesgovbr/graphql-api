from unittest.mock import AsyncMock, MagicMock

import pytest

from graphql_api.schema.resolvers.search import (
    SearchQuery,
    resolve_search,
    resolve_search_suggestions,
)
from graphql_api.schema.types.article import ArticleFilter


def _make_typesense_hit(unique_id: str, title: str) -> dict:
    return {
        "document": {
            "unique_id": unique_id,
            "title": title,
            "url": f"https://example.com/{unique_id}",
            "content": "Some content",
            "tags": ["test"],
            "agency": "gov",
        }
    }


def _mock_typesense_client(hits: list[dict], found: int | None = None):
    """Create a mock Typesense client that returns given hits."""
    mock_client = MagicMock()
    mock_client.collections.__getitem__.return_value.documents.search.return_value = {
        "hits": hits,
        "found": found if found is not None else len(hits),
    }
    return mock_client


async def test_keyword_search_returns_results():
    hits = [
        _make_typesense_hit("art-1", "First Article"),
        _make_typesense_hit("art-2", "Second Article"),
    ]
    ts_client = _mock_typesense_client(hits, found=2)

    result = await resolve_search(
        query="article",
        typesense_client=ts_client,
    )

    assert result.found == 2
    assert len(result.articles) == 2
    assert result.articles[0].unique_id == "art-1"
    assert result.articles[1].title == "Second Article"
    assert result.page == 1


async def test_semantic_search_calls_embeddings():
    hits = [_make_typesense_hit("art-1", "Semantic Result")]
    ts_client = _mock_typesense_client(hits)

    mock_embeddings = AsyncMock()
    mock_embeddings.generate_embedding.return_value = [0.1, 0.2, 0.3]

    result = await resolve_search(
        query="semantic query",
        semantic=True,
        typesense_client=ts_client,
        embeddings_ds=mock_embeddings,
    )

    mock_embeddings.generate_embedding.assert_called_once_with("semantic query")
    assert result.found == 1

    # Verify vector_query was included in the search params
    search_call = ts_client.collections.__getitem__.return_value.documents.search
    search_params = search_call.call_args[0][0]
    assert "vector_query" in search_params
    assert "content_embedding:" in search_params["vector_query"]
    assert "k:256" in search_params["vector_query"]
    assert "alpha:0.3" in search_params["vector_query"]


async def test_search_with_filters():
    hits = [_make_typesense_hit("art-1", "Filtered Article")]
    ts_client = _mock_typesense_client(hits)

    filter_input = ArticleFilter(
        agencies=["gov-br"],
        themes=["economia"],
        start_date="2024-01-01",
    )

    result = await resolve_search(
        query="economia",
        filter=filter_input,
        typesense_client=ts_client,
    )

    assert result.found == 1

    search_call = ts_client.collections.__getitem__.return_value.documents.search
    search_params = search_call.call_args[0][0]
    assert "filter_by" in search_params
    assert "agency:=" in search_params["filter_by"]
    assert "category:=" in search_params["filter_by"]
    assert "published_at:>=" in search_params["filter_by"]


async def test_search_suggestions_returns_list():
    hits = [
        _make_typesense_hit("s-1", "Suggestion One"),
        _make_typesense_hit("s-2", "Suggestion Two"),
        _make_typesense_hit("s-3", "Suggestion Three"),
    ]
    ts_client = _mock_typesense_client(hits)

    suggestions = await resolve_search_suggestions(
        query="suggest",
        typesense_client=ts_client,
    )

    assert len(suggestions) == 3
    assert suggestions[0].title == "Suggestion One"
    assert suggestions[2].unique_id == "s-3"

    search_call = ts_client.collections.__getitem__.return_value.documents.search
    search_params = search_call.call_args[0][0]
    assert search_params["per_page"] == 7
    assert search_params["query_by"] == "title"


async def test_semantic_search_custom_alpha():
    """alpha passado ao resolver deve substituir o 0.3 hardcoded no vector_query."""
    hits = [_make_typesense_hit("art-1", "Semantic Result")]
    ts_client = _mock_typesense_client(hits)

    mock_embeddings = AsyncMock()
    mock_embeddings.generate_embedding.return_value = [0.1, 0.2, 0.3]

    await resolve_search(
        query="semantic query",
        semantic=True,
        alpha=0.8,
        typesense_client=ts_client,
        embeddings_ds=mock_embeddings,
    )

    search_call = ts_client.collections.__getitem__.return_value.documents.search
    search_params = search_call.call_args[0][0]
    assert "alpha:0.8" in search_params["vector_query"]
    assert "alpha:0.3" not in search_params["vector_query"]


async def test_semantic_search_default_alpha_is_0_3():
    """alpha=None (default) mantém o comportamento legado (0.3)."""
    hits = [_make_typesense_hit("art-1", "Semantic Result")]
    ts_client = _mock_typesense_client(hits)

    mock_embeddings = AsyncMock()
    mock_embeddings.generate_embedding.return_value = [0.1, 0.2, 0.3]

    await resolve_search(
        query="semantic query",
        semantic=True,
        typesense_client=ts_client,
        embeddings_ds=mock_embeddings,
    )

    search_call = ts_client.collections.__getitem__.return_value.documents.search
    search_params = search_call.call_args[0][0]
    assert "alpha:0.3" in search_params["vector_query"]


async def test_dedup_applies_group_by_content_hash_keyword():
    """dedup=True aplica group_by content_hash na busca keyword."""
    hits = [_make_typesense_hit("art-1", "Article")]
    ts_client = _mock_typesense_client(hits)

    await resolve_search(
        query="article",
        dedup=True,
        typesense_client=ts_client,
    )

    search_call = ts_client.collections.__getitem__.return_value.documents.search
    search_params = search_call.call_args[0][0]
    assert search_params["group_by"] == "content_hash"
    assert search_params["group_limit"] == 1


async def test_dedup_applies_group_by_content_hash_semantic():
    """dedup=True aplica group_by content_hash também na busca semântica."""
    hits = [_make_typesense_hit("art-1", "Article")]
    ts_client = _mock_typesense_client(hits)

    mock_embeddings = AsyncMock()
    mock_embeddings.generate_embedding.return_value = [0.1, 0.2, 0.3]

    await resolve_search(
        query="article",
        semantic=True,
        dedup=True,
        typesense_client=ts_client,
        embeddings_ds=mock_embeddings,
    )

    search_call = ts_client.collections.__getitem__.return_value.documents.search
    search_params = search_call.call_args[0][0]
    assert search_params["group_by"] == "content_hash"
    assert search_params["group_limit"] == 1
    assert "vector_query" in search_params


async def test_dedup_off_by_default():
    """Sem dedup, nenhum group_by é aplicado (comportamento legado)."""
    hits = [_make_typesense_hit("art-1", "Article")]
    ts_client = _mock_typesense_client(hits)

    await resolve_search(query="article", typesense_client=ts_client)

    search_call = ts_client.collections.__getitem__.return_value.documents.search
    search_params = search_call.call_args[0][0]
    assert "group_by" not in search_params


async def test_search_populates_theme_fields():
    """Os 8 campos de tema devem ser populados nos Articles retornados."""
    hit = {
        "document": {
            "unique_id": "art-1",
            "title": "Themed Article",
            "url": "https://example.com/art-1",
            "theme_1_level_1_code": "100",
            "theme_1_level_1_label": "Economia",
            "theme_1_level_2_code": "110",
            "theme_1_level_2_label": "Tributação",
            "theme_1_level_3_code": "111",
            "theme_1_level_3_label": "Imposto de Renda",
            "most_specific_theme_code": "111",
            "most_specific_theme_label": "Imposto de Renda",
        }
    }
    ts_client = _mock_typesense_client([hit], found=1)

    result = await resolve_search(query="economia", typesense_client=ts_client)

    art = result.articles[0]
    assert art.theme_1_level_1_code == "100"
    assert art.theme_1_level_1_label == "Economia"
    assert art.theme_1_level_2_code == "110"
    assert art.theme_1_level_2_label == "Tributação"
    assert art.theme_1_level_3_code == "111"
    assert art.theme_1_level_3_label == "Imposto de Renda"
    assert art.most_specific_theme_code == "111"
    assert art.most_specific_theme_label == "Imposto de Renda"


async def test_empty_query_returns_error():
    ts_client = _mock_typesense_client([])

    with pytest.raises(ValueError, match="Query must not be empty"):
        await resolve_search(query="", typesense_client=ts_client)

    with pytest.raises(ValueError, match="Query must not be empty"):
        await resolve_search(query="   ", typesense_client=ts_client)


def _mock_info(typesense_ds):
    """Mock de strawberry Info com context.typesense_ds."""
    info = MagicMock()
    info.context.typesense_ds = typesense_ds
    return info


class TestSearchQueryResolver:
    async def test_search_uses_context_typesense_client(self):
        hits = [_make_typesense_hit("art-1", "From Context")]
        ts_client = _mock_typesense_client(hits, found=1)
        ds = MagicMock()
        ds.client = ts_client
        info = _mock_info(ds)

        result = await SearchQuery().search(info, query="article")

        assert result.found == 1
        assert result.articles[0].unique_id == "art-1"
        # Usou o client vindo do contexto
        ts_client.collections.__getitem__.return_value.documents.search.assert_called()

    async def test_search_returns_empty_when_ds_none(self):
        info = _mock_info(None)

        result = await SearchQuery().search(info, query="article")

        assert result.found == 0
        assert result.articles == []

    async def test_search_suggestions_uses_context_typesense_client(self):
        hits = [_make_typesense_hit("s-1", "Suggestion")]
        ts_client = _mock_typesense_client(hits)
        ds = MagicMock()
        ds.client = ts_client
        info = _mock_info(ds)

        suggestions = await SearchQuery().search_suggestions(info, query="sug")

        assert len(suggestions) == 1
        assert suggestions[0].unique_id == "s-1"

    async def test_search_suggestions_returns_empty_when_ds_none(self):
        info = _mock_info(None)

        suggestions = await SearchQuery().search_suggestions(info, query="sug")

        assert suggestions == []
