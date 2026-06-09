from unittest.mock import MagicMock, patch

import pytest

from graphql_api.datasources.typesense import ArticleDocument, SearchResult


def _sample_article_doc(**overrides) -> ArticleDocument:
    from datetime import datetime, timezone

    defaults = dict(
        unique_id="abc-123",
        title="Test Article",
        url="https://example.com/article",
        image="https://example.com/img.jpg",
        video_url=None,
        content="Full content",
        summary="Summary",
        subtitle="Subtitle",
        editorial_lead="Lead",
        category="politica",
        tags=["economia"],
        agency="agencia-brasil",
        agency_name="Agência Brasil",
        published_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        extracted_at=datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return ArticleDocument(**defaults)


def _make_mock_ds():
    ds = MagicMock()
    ds.search_articles.return_value = SearchResult(
        articles=[_sample_article_doc()],
        page=1,
        found=1,
    )
    ds.get_article_by_id.return_value = _sample_article_doc()
    return ds


@pytest.fixture
def mock_typesense_ds():
    return _make_mock_ds()


@pytest.fixture
def app(mock_typesense_ds):
    from graphql_api.app import create_app
    from graphql_api.context import GraphQLContext

    application = create_app()

    # Override context to inject mock datasource
    async def mock_context():
        ctx = GraphQLContext(typesense_ds=mock_typesense_ds)
        return ctx

    # Patch the context dependency in the graphql router
    with patch("graphql_api.app.get_context", mock_context):
        # We need to recreate the app with the patched context
        application = create_app()
        yield application


@pytest.fixture
async def client(app):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_articles_query_shape(client):
    query = """
    {
        articles {
            articles {
                uniqueId
                title
                url
                image
                videoUrl
                content
                summary
                subtitle
                editorialLead
                category
                tags
                agency
                agencyName
                publishedAt
                extractedAt
            }
            page
            found
        }
    }
    """
    response = await client.post("/graphql", json={"query": query})
    assert response.status_code == 200
    data = response.json()
    assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"
    result = data["data"]["articles"]
    assert result["page"] == 1
    assert result["found"] == 1
    assert len(result["articles"]) == 1
    article = result["articles"][0]
    assert article["uniqueId"] == "abc-123"
    assert article["title"] == "Test Article"
    assert article["agency"] == "agencia-brasil"
    assert article["agencyName"] == "Agência Brasil"
    assert article["tags"] == ["economia"]


@pytest.mark.asyncio
async def test_articles_with_filter(client, mock_typesense_ds):
    query = """
    query($filter: ArticleFilter) {
        articles(filter: $filter) {
            articles { uniqueId title }
            found
        }
    }
    """
    variables = {
        "filter": {
            "agencies": ["agencia-brasil"],
            "startDate": "2024-01-01",
            "endDate": "2024-12-31",
        }
    }
    response = await client.post("/graphql", json={"query": query, "variables": variables})
    assert response.status_code == 200
    data = response.json()
    assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"

    # Verify the datasource was called with filter params
    mock_typesense_ds.search_articles.assert_called_once()
    call_kwargs = mock_typesense_ds.search_articles.call_args
    assert call_kwargs.kwargs.get("agencies") == ["agencia-brasil"] or (
        call_kwargs.args == () and "agencies" in str(call_kwargs)
    )


@pytest.mark.asyncio
async def test_article_by_id(client, mock_typesense_ds):
    query = """
    query($uniqueId: String!) {
        article(uniqueId: $uniqueId) {
            uniqueId
            title
            url
        }
    }
    """
    response = await client.post(
        "/graphql", json={"query": query, "variables": {"uniqueId": "abc-123"}}
    )
    assert response.status_code == 200
    data = response.json()
    assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"
    article = data["data"]["article"]
    assert article["uniqueId"] == "abc-123"
    assert article["title"] == "Test Article"


@pytest.mark.asyncio
async def test_article_not_found_returns_null(client, mock_typesense_ds):
    mock_typesense_ds.get_article_by_id.return_value = None

    query = """
    query($uniqueId: String!) {
        article(uniqueId: $uniqueId) {
            uniqueId
        }
    }
    """
    response = await client.post(
        "/graphql", json={"query": query, "variables": {"uniqueId": "nonexistent"}}
    )
    assert response.status_code == 200
    data = response.json()
    assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"
    assert data["data"]["article"] is None


@pytest.mark.asyncio
async def test_articles_pagination(client, mock_typesense_ds):
    mock_typesense_ds.search_articles.return_value = SearchResult(
        articles=[_sample_article_doc(unique_id=f"art-{i}") for i in range(5)],
        page=2,
        found=15,
    )

    query = """
    query {
        articles(page: 2, limit: 5) {
            articles { uniqueId }
            page
            found
        }
    }
    """
    response = await client.post("/graphql", json={"query": query})
    assert response.status_code == 200
    data = response.json()
    assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"
    result = data["data"]["articles"]
    assert result["page"] == 2
    assert result["found"] == 15
    assert len(result["articles"]) == 5


def _mock_info(ds):
    info = MagicMock()
    info.context.typesense_ds = ds
    return info


class TestArticlesSortAndFilters:
    """Fiação do resolver ArticleQuery.articles para `sort` (Fase 2) e os novos
    filtros entities/sentiment — encaminhamento correto ao search_articles."""

    def test_sort_views_maps_to_view_count_desc(self):
        from graphql_api.schema.resolvers.articles import ArticleQuery
        from graphql_api.schema.types.article import ArticleSort

        ds = _make_mock_ds()
        ArticleQuery().articles(_mock_info(ds), sort=ArticleSort.VIEWS)
        assert ds.search_articles.call_args.kwargs["sort_by"] == "view_count:desc"

    def test_sort_trending_maps_to_trending_desc(self):
        from graphql_api.schema.resolvers.articles import ArticleQuery
        from graphql_api.schema.types.article import ArticleSort

        ds = _make_mock_ds()
        ArticleQuery().articles(_mock_info(ds), sort=ArticleSort.TRENDING)
        assert ds.search_articles.call_args.kwargs["sort_by"] == "trending_score:desc"

    def test_sort_none_defaults_to_published_desc(self):
        # Listagem (sem query): None cai para data desc (distinto do None da busca).
        from graphql_api.schema.resolvers.articles import ArticleQuery

        ds = _make_mock_ds()
        ArticleQuery().articles(_mock_info(ds))
        assert ds.search_articles.call_args.kwargs["sort_by"] == "published_at:desc"

    def test_entities_and_sentiment_forwarded_from_filter(self):
        from graphql_api.schema.resolvers.articles import ArticleQuery
        from graphql_api.schema.types.article import ArticleFilter

        ds = _make_mock_ds()
        ArticleQuery().articles(
            _mock_info(ds),
            filter=ArticleFilter(
                entities=["Ministério da Saúde"], sentiment=["positive"]
            ),
        )
        kwargs = ds.search_articles.call_args.kwargs
        assert kwargs["entities"] == ["Ministério da Saúde"]
        assert kwargs["sentiment"] == ["positive"]
