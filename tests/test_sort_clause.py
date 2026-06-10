"""Testes do helper `sort_by_clause` (Fase 2) — mapeia ArticleSort → sort_by Typesense."""

from graphql_api.schema.types.article import ArticleSort, sort_by_clause


def test_relevance_default_keeps_relevance_none():
    # Em busca por keyword (relevance_default=True), None/RELEVANCE = relevância
    # de text-match (omite sort_by).
    assert sort_by_clause(None, relevance_default=True) is None
    assert sort_by_clause(ArticleSort.RELEVANCE, relevance_default=True) is None


def test_no_relevance_default_falls_back_to_date():
    # Em listagem (sem query), None/RELEVANCE caem para data desc.
    assert sort_by_clause(None, relevance_default=False) == "published_at:desc"
    assert (
        sort_by_clause(ArticleSort.RELEVANCE, relevance_default=False)
        == "published_at:desc"
    )


def test_explicit_sorts():
    assert sort_by_clause(ArticleSort.DATE, relevance_default=True) == "published_at:desc"
    assert (
        sort_by_clause(ArticleSort.TRENDING, relevance_default=True)
        == "trending_score:desc"
    )
    assert sort_by_clause(ArticleSort.VIEWS, relevance_default=False) == "view_count:desc"
