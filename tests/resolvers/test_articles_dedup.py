from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from graphql_api.datasources.typesense import ArticleDocument, SearchResult


def _sample_article_doc(**overrides) -> ArticleDocument:
    defaults = dict(
        unique_id="abc-123",
        title="Test Article",
        url="https://example.com/article",
        published_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
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
    return ds


@pytest.fixture
def mock_typesense_ds():
    return _make_mock_ds()


@pytest.fixture
def app(mock_typesense_ds):
    from graphql_api.app import create_app
    from graphql_api.context import GraphQLContext

    async def mock_context():
        return GraphQLContext(typesense_ds=mock_typesense_ds)

    with patch("graphql_api.app.get_context", mock_context):
        yield create_app()


@pytest.fixture
async def client(app):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_dedup_on_passes_dedup_true(client, mock_typesense_ds):
    query = """
    query($filter: ArticleFilter) {
        articles(filter: $filter) { found }
    }
    """
    variables = {"filter": {"dedup": True}}
    response = await client.post("/graphql", json={"query": query, "variables": variables})
    assert response.status_code == 200
    data = response.json()
    assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"

    mock_typesense_ds.search_articles.assert_called_once()
    assert mock_typesense_ds.search_articles.call_args.kwargs.get("dedup") is True


@pytest.mark.asyncio
async def test_dedup_off_passes_dedup_false(client, mock_typesense_ds):
    query = """
    query($filter: ArticleFilter) {
        articles(filter: $filter) { found }
    }
    """
    # dedup not set on filter
    variables = {"filter": {"agencies": ["agencia-brasil"]}}
    response = await client.post("/graphql", json={"query": query, "variables": variables})
    assert response.status_code == 200
    data = response.json()
    assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"

    mock_typesense_ds.search_articles.assert_called_once()
    assert mock_typesense_ds.search_articles.call_args.kwargs.get("dedup") is False


@pytest.mark.asyncio
async def test_no_filter_passes_dedup_false(client, mock_typesense_ds):
    query = "{ articles { found } }"
    response = await client.post("/graphql", json={"query": query})
    assert response.status_code == 200
    data = response.json()
    assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"

    mock_typesense_ds.search_articles.assert_called_once()
    assert mock_typesense_ds.search_articles.call_args.kwargs.get("dedup") is False


@pytest.mark.asyncio
async def test_theme_filter_reaches_datasource(client, mock_typesense_ds):
    query = """
    query($filter: ArticleFilter) {
        articles(filter: $filter) { found }
    }
    """
    variables = {"filter": {"themes": ["saude", "educacao"]}}
    response = await client.post("/graphql", json={"query": query, "variables": variables})
    assert response.status_code == 200
    data = response.json()
    assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"

    mock_typesense_ds.search_articles.assert_called_once()
    assert mock_typesense_ds.search_articles.call_args.kwargs.get("themes") == ["saude", "educacao"]


@pytest.mark.asyncio
async def test_theme_label_reaches_datasource(client, mock_typesense_ds):
    query = """
    query($filter: ArticleFilter) {
        articles(filter: $filter) { found }
    }
    """
    variables = {"filter": {"themeLabel": "Saúde"}}
    response = await client.post("/graphql", json={"query": query, "variables": variables})
    assert response.status_code == 200
    data = response.json()
    assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"

    mock_typesense_ds.search_articles.assert_called_once()
    assert mock_typesense_ds.search_articles.call_args.kwargs.get("theme_label") == "Saúde"


@pytest.mark.asyncio
async def test_tags_and_dates_reach_datasource(client, mock_typesense_ds):
    query = """
    query($filter: ArticleFilter) {
        articles(filter: $filter) { found }
    }
    """
    variables = {
        "filter": {
            "tags": ["economia"],
            "startDate": "2024-01-01",
            "endDate": "2024-12-31",
        }
    }
    response = await client.post("/graphql", json={"query": query, "variables": variables})
    assert response.status_code == 200
    data = response.json()
    assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"

    kwargs = mock_typesense_ds.search_articles.call_args.kwargs
    assert kwargs.get("tags") == ["economia"]
    assert kwargs.get("start_date") == "2024-01-01"
    assert kwargs.get("end_date") == "2024-12-31"


@pytest.mark.asyncio
async def test_theme_fields_populated_on_article(client, mock_typesense_ds):
    mock_typesense_ds.search_articles.return_value = SearchResult(
        articles=[
            _sample_article_doc(
                theme_1_level_1_code="L1C",
                theme_1_level_1_label="L1 Label",
                theme_1_level_2_code="L2C",
                theme_1_level_2_label="L2 Label",
                theme_1_level_3_code="L3C",
                theme_1_level_3_label="L3 Label",
                most_specific_theme_code="MSC",
                most_specific_theme_label="MS Label",
            )
        ],
        page=1,
        found=1,
    )
    query = """
    {
        articles {
            articles {
                theme1Level1Code
                theme1Level1Label
                theme1Level2Code
                theme1Level2Label
                theme1Level3Code
                theme1Level3Label
                mostSpecificThemeCode
                mostSpecificThemeLabel
            }
            found
        }
    }
    """
    response = await client.post("/graphql", json={"query": query})
    assert response.status_code == 200
    data = response.json()
    assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"
    article = data["data"]["articles"]["articles"][0]
    assert article["theme1Level1Code"] == "L1C"
    assert article["theme1Level1Label"] == "L1 Label"
    assert article["theme1Level2Code"] == "L2C"
    assert article["theme1Level2Label"] == "L2 Label"
    assert article["theme1Level3Code"] == "L3C"
    assert article["theme1Level3Label"] == "L3 Label"
    assert article["mostSpecificThemeCode"] == "MSC"
    assert article["mostSpecificThemeLabel"] == "MS Label"


@pytest.mark.asyncio
async def test_found_still_exposed(client, mock_typesense_ds):
    mock_typesense_ds.search_articles.return_value = SearchResult(
        articles=[], page=1, found=117000
    )
    query = "{ articles(limit: 1) { found } }"
    response = await client.post("/graphql", json={"query": query})
    assert response.status_code == 200
    data = response.json()
    assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"
    assert data["data"]["articles"]["found"] == 117000
