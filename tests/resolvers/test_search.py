from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphql_api.datasources.typesense import ArticleDocument, SearchResult
from graphql_api.schema.resolvers.search import (
    SearchQuery,
    resolve_search,
    resolve_search_suggestions,
)
from graphql_api.schema.types.article import ArticleFilter


def _make_article_doc(unique_id: str, title: str, **overrides) -> ArticleDocument:
    base = dict(
        unique_id=unique_id,
        title=title,
        url=f"https://example.com/{unique_id}",
        content="Some content",
        tags=["test"],
        agency="gov",
    )
    base.update(overrides)
    return ArticleDocument(**base)


def _mock_ds(articles: list[ArticleDocument], found: int | None = None):
    """Mock de TypesenseDatasource cujo search_articles retorna um SearchResult."""
    ds = MagicMock()
    ds.search_articles.return_value = SearchResult(
        articles=articles,
        page=1,
        found=found if found is not None else len(articles),
    )
    return ds


async def test_keyword_search_returns_results():
    articles = [
        _make_article_doc("art-1", "First Article"),
        _make_article_doc("art-2", "Second Article"),
    ]
    ds = _mock_ds(articles, found=2)

    result = await resolve_search(query="article", ds=ds)

    assert result.found == 2
    assert len(result.articles) == 2
    assert result.articles[0].unique_id == "art-1"
    assert result.articles[1].title == "Second Article"
    assert result.page == 1


async def test_keyword_search_passes_relevance_ranking():
    """Busca por keyword chama search_articles com q, query_by e sort_by=None."""
    ds = _mock_ds([_make_article_doc("art-1", "A")])

    await resolve_search(query="reforma tributaria", ds=ds)

    kwargs = ds.search_articles.call_args.kwargs
    assert kwargs["q"] == "reforma tributaria"
    assert kwargs["query_by"] == "title,content,summary"
    assert kwargs["sort_by"] is None
    assert kwargs["dedup"] is False
    assert kwargs["page"] == 1
    assert kwargs["limit"] == 20
    assert kwargs.get("vector") is None


async def test_semantic_search_calls_embeddings():
    ds = _mock_ds([_make_article_doc("art-1", "Semantic Result")])

    mock_embeddings = AsyncMock()
    mock_embeddings.generate_embedding.return_value = [0.1, 0.2, 0.3]

    result = await resolve_search(
        query="semantic query",
        semantic=True,
        ds=ds,
        embeddings_ds=mock_embeddings,
    )

    mock_embeddings.generate_embedding.assert_called_once_with("semantic query")
    assert result.found == 1

    kwargs = ds.search_articles.call_args.kwargs
    assert kwargs["vector"] == [0.1, 0.2, 0.3]
    assert kwargs["alpha"] == 0.3


async def test_search_with_filters():
    ds = _mock_ds([_make_article_doc("art-1", "Filtered Article")])

    filter_input = ArticleFilter(
        agencies=["gov-br"],
        themes=["economia"],
        tags=["tag-x"],
        theme_label="Economia",
        start_date="2024-01-01",
        end_date="2024-12-31",
    )

    result = await resolve_search(query="economia", filter=filter_input, ds=ds)

    assert result.found == 1

    kwargs = ds.search_articles.call_args.kwargs
    assert kwargs["agencies"] == ["gov-br"]
    assert kwargs["themes"] == ["economia"]
    assert kwargs["tags"] == ["tag-x"]
    assert kwargs["theme_label"] == "Economia"
    assert kwargs["start_date"] == "2024-01-01"
    assert kwargs["end_date"] == "2024-12-31"


async def test_search_maps_published_at_datetime():
    """Bug fix: timestamps já convertidos pelo datasource passam por
    _to_graphql_article sem erro de isoformat — publishedAt é datetime."""
    published = datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
    doc = _make_article_doc("art-1", "Dated Article", published_at=published)
    ds = _mock_ds([doc], found=1)

    result = await resolve_search(query="dated", ds=ds)

    art = result.articles[0]
    assert art.published_at == published
    assert isinstance(art.published_at, datetime)


async def test_semantic_search_custom_alpha():
    ds = _mock_ds([_make_article_doc("art-1", "Semantic Result")])

    mock_embeddings = AsyncMock()
    mock_embeddings.generate_embedding.return_value = [0.1, 0.2, 0.3]

    await resolve_search(
        query="semantic query",
        semantic=True,
        alpha=0.8,
        ds=ds,
        embeddings_ds=mock_embeddings,
    )

    kwargs = ds.search_articles.call_args.kwargs
    assert kwargs["alpha"] == 0.8


async def test_semantic_search_default_alpha_is_0_3():
    ds = _mock_ds([_make_article_doc("art-1", "Semantic Result")])

    mock_embeddings = AsyncMock()
    mock_embeddings.generate_embedding.return_value = [0.1, 0.2, 0.3]

    await resolve_search(
        query="semantic query",
        semantic=True,
        ds=ds,
        embeddings_ds=mock_embeddings,
    )

    kwargs = ds.search_articles.call_args.kwargs
    assert kwargs["alpha"] == 0.3


async def test_semantic_no_vector_omits_vector():
    """Se generate_embedding retorna None, não passa vector/alpha."""
    ds = _mock_ds([_make_article_doc("art-1", "Result")])

    mock_embeddings = AsyncMock()
    mock_embeddings.generate_embedding.return_value = None

    await resolve_search(
        query="semantic query",
        semantic=True,
        ds=ds,
        embeddings_ds=mock_embeddings,
    )

    kwargs = ds.search_articles.call_args.kwargs
    assert kwargs.get("vector") is None


async def test_dedup_passed_through_keyword():
    ds = _mock_ds([_make_article_doc("art-1", "Article")])

    await resolve_search(query="article", dedup=True, ds=ds)

    kwargs = ds.search_articles.call_args.kwargs
    assert kwargs["dedup"] is True


async def test_dedup_passed_through_semantic():
    ds = _mock_ds([_make_article_doc("art-1", "Article")])

    mock_embeddings = AsyncMock()
    mock_embeddings.generate_embedding.return_value = [0.1, 0.2, 0.3]

    await resolve_search(
        query="article",
        semantic=True,
        dedup=True,
        ds=ds,
        embeddings_ds=mock_embeddings,
    )

    kwargs = ds.search_articles.call_args.kwargs
    assert kwargs["dedup"] is True
    assert kwargs["vector"] == [0.1, 0.2, 0.3]


async def test_dedup_off_by_default():
    ds = _mock_ds([_make_article_doc("art-1", "Article")])

    await resolve_search(query="article", ds=ds)

    kwargs = ds.search_articles.call_args.kwargs
    assert kwargs["dedup"] is False


async def test_search_populates_theme_fields():
    """Os 8 campos de tema devem ser populados nos Articles retornados."""
    doc = _make_article_doc(
        "art-1",
        "Themed Article",
        theme_1_level_1_code="100",
        theme_1_level_1_label="Economia",
        theme_1_level_2_code="110",
        theme_1_level_2_label="Tributação",
        theme_1_level_3_code="111",
        theme_1_level_3_label="Imposto de Renda",
        most_specific_theme_code="111",
        most_specific_theme_label="Imposto de Renda",
    )
    ds = _mock_ds([doc], found=1)

    result = await resolve_search(query="economia", ds=ds)

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
    ds = _mock_ds([])

    with pytest.raises(ValueError, match="Query must not be empty"):
        await resolve_search(query="", ds=ds)

    with pytest.raises(ValueError, match="Query must not be empty"):
        await resolve_search(query="   ", ds=ds)


def _make_typesense_hit(unique_id: str, title: str) -> dict:
    return {
        "document": {
            "unique_id": unique_id,
            "title": title,
        }
    }


def _mock_suggestions_client(hits: list[dict]):
    mock_client = MagicMock()
    mock_client.collections.__getitem__.return_value.documents.search.return_value = {
        "hits": hits,
        "found": len(hits),
    }
    return mock_client


async def test_search_suggestions_returns_list():
    hits = [
        _make_typesense_hit("s-1", "Suggestion One"),
        _make_typesense_hit("s-2", "Suggestion Two"),
        _make_typesense_hit("s-3", "Suggestion Three"),
    ]
    ts_client = _mock_suggestions_client(hits)

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


def _mock_info(typesense_ds):
    """Mock de strawberry Info com context.typesense_ds."""
    info = MagicMock()
    info.context.typesense_ds = typesense_ds
    return info


class TestSearchQueryResolver:
    async def test_search_uses_context_datasource(self):
        articles = [_make_article_doc("art-1", "From Context")]
        ds = _mock_ds(articles, found=1)
        info = _mock_info(ds)

        result = await SearchQuery().search(info, query="article")

        assert result.found == 1
        assert result.articles[0].unique_id == "art-1"
        ds.search_articles.assert_called_once()

    async def test_search_returns_empty_when_ds_none(self):
        info = _mock_info(None)

        result = await SearchQuery().search(info, query="article")

        assert result.found == 0
        assert result.articles == []

    async def test_search_suggestions_uses_context_typesense_client(self):
        hits = [_make_typesense_hit("s-1", "Suggestion")]
        ts_client = _mock_suggestions_client(hits)
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


async def test_search_passes_entities_and_sentiment_filter():
    ds = _mock_ds([_make_article_doc("a", "A")])

    filter_input = ArticleFilter(entities=["Ministério da Saúde"], sentiment=["positive"])
    await resolve_search(query="saude", filter=filter_input, ds=ds)

    kwargs = ds.search_articles.call_args.kwargs
    assert kwargs["entities"] == ["Ministério da Saúde"]
    assert kwargs["sentiment"] == ["positive"]


async def test_search_sort_by_overrides_relevance():
    ds = _mock_ds([_make_article_doc("a", "A")])

    await resolve_search(query="x", ds=ds, sort_by="trending_score:desc")

    kwargs = ds.search_articles.call_args.kwargs
    assert kwargs["sort_by"] == "trending_score:desc"


async def test_search_sort_by_defaults_to_none_relevance():
    ds = _mock_ds([_make_article_doc("a", "A")])

    await resolve_search(query="x", ds=ds)

    kwargs = ds.search_articles.call_args.kwargs
    assert kwargs["sort_by"] is None
