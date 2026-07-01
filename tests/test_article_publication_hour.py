from datetime import datetime

from graphql_api.schema.types.article import Article


def _article(published_at):
    return Article(
        unique_id="uid",
        title="t",
        url="https://example.gov.br/n",
        published_at=published_at,
    )


def test_publication_hour_derivado_de_published_at():
    art = _article(datetime(2026, 6, 15, 14, 30))
    assert art.publication_hour() == 14


def test_publication_hour_none_quando_published_at_none():
    art = _article(None)
    assert art.publication_hour() is None


def test_publication_hour_meia_noite():
    art = _article(datetime(2026, 6, 15, 0, 5))
    assert art.publication_hour() == 0


def test_publication_hour_23():
    art = _article(datetime(2026, 6, 15, 23, 59))
    assert art.publication_hour() == 23


def test_publication_hour_exposto_no_schema():
    from graphql_api.schema import schema

    assert "publicationHour" in schema.as_str()
