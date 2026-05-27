from datetime import datetime, timezone
from unittest.mock import MagicMock

import typesense.exceptions

from graphql_api.datasources.typesense import TypesenseDatasource


def _make_hit(doc: dict) -> dict:
    return {"document": doc}


def _sample_doc(**overrides) -> dict:
    base = {
        "unique_id": "abc-123",
        "title": "Test Article",
        "url": "https://example.com/article",
        "image": "https://example.com/img.jpg",
        "video_url": None,
        "content": "Full article content",
        "summary": "Short summary",
        "subtitle": "A subtitle",
        "editorial_lead": "Lead text",
        "category": "politica",
        "tags": ["economia", "saude"],
        "agency": "agencia-brasil",
        "agency_name": "Agência Brasil",
        "published_at": 1700000000,
        "extracted_at": 1700001000,
    }
    base.update(overrides)
    return base


def _mock_client(search_return=None, retrieve_return=None, retrieve_side_effect=None):
    client = MagicMock()
    collection = MagicMock()
    client.collections.__getitem__ = MagicMock(return_value=collection)

    if search_return is not None:
        collection.documents.search.return_value = search_return

    if retrieve_return is not None:
        doc_mock = MagicMock()
        doc_mock.retrieve.return_value = retrieve_return
        collection.documents.__getitem__ = MagicMock(return_value=doc_mock)
    elif retrieve_side_effect is not None:
        doc_mock = MagicMock()
        doc_mock.retrieve.side_effect = retrieve_side_effect
        collection.documents.__getitem__ = MagicMock(return_value=doc_mock)

    return client


class TestSearchArticles:
    def test_search_articles_returns_typed_result(self):
        doc = _sample_doc()
        client = _mock_client(search_return={"hits": [_make_hit(doc)], "found": 1})
        ds = TypesenseDatasource(client)

        result = ds.search_articles(page=1, limit=10)

        assert result.found == 1
        assert result.page == 1
        assert len(result.articles) == 1
        article = result.articles[0]
        assert article.unique_id == "abc-123"
        assert article.title == "Test Article"
        assert article.url == "https://example.com/article"
        assert article.agency == "agencia-brasil"
        assert article.agency_name == "Agência Brasil"
        assert article.tags == ["economia", "saude"]
        assert article.published_at == datetime.fromtimestamp(1700000000, tz=timezone.utc)
        assert article.extracted_at == datetime.fromtimestamp(1700001000, tz=timezone.utc)

    def test_search_articles_filters_by_agency(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(agencies=["agencia-brasil"])

        call_args = client.collections["news"].documents.search.call_args[0][0]
        assert "agency:" in call_args["filter_by"]
        assert "agencia-brasil" in call_args["filter_by"]

    def test_search_articles_filters_by_date_range(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(start_date="2024-01-01T00:00:00+00:00", end_date="2024-12-31T23:59:59+00:00")

        call_args = client.collections["news"].documents.search.call_args[0][0]
        assert "published_at:>=" in call_args["filter_by"]
        assert "published_at:<=" in call_args["filter_by"]

    def test_search_articles_filters_by_themes(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(themes=["01", "02"])

        call_args = client.collections["news"].documents.search.call_args[0][0]
        assert "theme_1_level_1_code:=01" in call_args["filter_by"]
        assert "theme_1_level_1_code:=02" in call_args["filter_by"]


class TestGetArticleById:
    def test_get_article_by_id(self):
        doc = _sample_doc()
        client = _mock_client(retrieve_return=doc)
        ds = TypesenseDatasource(client)

        article = ds.get_article_by_id("abc-123")

        assert article is not None
        assert article.unique_id == "abc-123"
        assert article.title == "Test Article"

    def test_get_article_not_found_returns_none(self):
        client = _mock_client(
            retrieve_side_effect=typesense.exceptions.ObjectNotFound("Not found")
        )
        ds = TypesenseDatasource(client)

        article = ds.get_article_by_id("nonexistent")

        assert article is None
