"""Testes dos resolvers de conteudo publico (Fase 2A, Stream B3).

Cobre `relatedArticles`, `themeArticleCounts`, `releaseArticles` e
`estimateRecorteCount` em `resolvers/public_content.py`. Mock de datasource
(Typesense + Firestore). Schema de teste minimo (sem subir o app).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import strawberry

from graphql_api.context import GraphQLContext, User
from graphql_api.datasources.firestore import ClippingData, ReleaseData, SubscriptionData
from graphql_api.datasources.typesense import ArticleDocument, SearchResult
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.public_content import PublicContentQuery

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


@strawberry.type
class _Query(HealthQuery, PublicContentQuery):
    pass


test_schema = strawberry.Schema(query=_Query)


def _doc(unique_id: str, published_at: datetime = NOW, **overrides) -> ArticleDocument:
    defaults = dict(
        unique_id=unique_id,
        title=f"Titulo {unique_id}",
        url=f"https://example.gov.br/{unique_id}",
        published_at=published_at,
    )
    defaults.update(overrides)
    return ArticleDocument(**defaults)


def _ctx(typesense_ds=None, firestore_ds=None, user_id=None) -> GraphQLContext:
    ctx = GraphQLContext(typesense_ds=typesense_ds, firestore_ds=firestore_ds)
    if user_id is not None:
        ctx.user = User(id=user_id, email="test@example.com")
    return ctx


# ---------------------------------------------------------------------------
# relatedArticles
# ---------------------------------------------------------------------------
RELATED_QUERY = """
    query($id: String!, $limit: Int!) {
        relatedArticles(uniqueId: $id, limit: $limit) {
            uniqueId
            title
            theme1Level1Code
            mostSpecificThemeCode
        }
    }
"""


class TestRelatedArticles:
    def test_uses_most_specific_theme_code_and_excludes_self(self):
        ds = MagicMock()
        ds.get_article_by_id.return_value = _doc(
            "base",
            most_specific_theme_code="saude.vacina",
            theme_1_level_1_code="saude",
        )
        ds.search_articles.return_value = SearchResult(
            articles=[
                _doc("base"),  # o proprio artigo -> deve ser excluido
                _doc("a", most_specific_theme_code="saude.vacina"),
                _doc("b"),
            ],
            page=1,
            found=3,
        )
        result = test_schema.execute_sync(
            RELATED_QUERY,
            variable_values={"id": "base", "limit": 4},
            context_value=_ctx(typesense_ds=ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        ids = [a["uniqueId"] for a in result.data["relatedArticles"]]
        assert "base" not in ids
        assert ids == ["a", "b"]
        # most_specific_theme_code foi usado (nao o L1)
        _, kwargs = ds.search_articles.call_args
        assert kwargs["themes"] == ["saude.vacina"]
        assert kwargs["dedup"] is True

    def test_falls_back_to_level_1_when_no_most_specific(self):
        ds = MagicMock()
        ds.get_article_by_id.return_value = _doc(
            "base",
            most_specific_theme_code=None,
            theme_1_level_1_code="economia",
        )
        ds.search_articles.return_value = SearchResult(
            articles=[_doc("x")], page=1, found=1
        )
        result = test_schema.execute_sync(
            RELATED_QUERY,
            variable_values={"id": "base", "limit": 4},
            context_value=_ctx(typesense_ds=ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        _, kwargs = ds.search_articles.call_args
        assert kwargs["themes"] == ["economia"]

    def test_respects_limit_after_excluding_self(self):
        ds = MagicMock()
        ds.get_article_by_id.return_value = _doc("base", theme_1_level_1_code="t")
        ds.search_articles.return_value = SearchResult(
            articles=[_doc(c) for c in ("base", "a", "b", "c", "d")],
            page=1,
            found=5,
        )
        result = test_schema.execute_sync(
            RELATED_QUERY,
            variable_values={"id": "base", "limit": 2},
            context_value=_ctx(typesense_ds=ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        ids = [a["uniqueId"] for a in result.data["relatedArticles"]]
        assert ids == ["a", "b"]

    def test_missing_base_article_returns_empty(self):
        ds = MagicMock()
        ds.get_article_by_id.return_value = None
        result = test_schema.execute_sync(
            RELATED_QUERY,
            variable_values={"id": "missing", "limit": 4},
            context_value=_ctx(typesense_ds=ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["relatedArticles"] == []
        ds.search_articles.assert_not_called()

    def test_no_theme_code_returns_empty(self):
        ds = MagicMock()
        ds.get_article_by_id.return_value = _doc("base")  # sem nenhum theme code
        result = test_schema.execute_sync(
            RELATED_QUERY,
            variable_values={"id": "base", "limit": 4},
            context_value=_ctx(typesense_ds=ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["relatedArticles"] == []


# ---------------------------------------------------------------------------
# themeArticleCounts
# ---------------------------------------------------------------------------
THEME_COUNTS_QUERY = """
    query($days: Int!, $level: Int!) {
        themeArticleCounts(days: $days, level: $level) {
            code
            label
            count
        }
    }
"""


class TestThemeArticleCounts:
    def test_maps_tuples_to_theme_count(self):
        ds = MagicMock()
        ds.theme_counts.return_value = [("saude", 42), ("economia", 17)]
        result = test_schema.execute_sync(
            THEME_COUNTS_QUERY,
            variable_values={"days": 30, "level": 1},
            context_value=_ctx(typesense_ds=ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        data = result.data["themeArticleCounts"]
        assert data == [
            {"code": "saude", "label": None, "count": 42},
            {"code": "economia", "label": None, "count": 17},
        ]
        ds.theme_counts.assert_called_once_with(level=1, days=30)

    def test_respects_level_and_days(self):
        ds = MagicMock()
        ds.theme_counts.return_value = []
        test_schema.execute_sync(
            THEME_COUNTS_QUERY,
            variable_values={"days": 7, "level": 2},
            context_value=_ctx(typesense_ds=ds),
        )
        ds.theme_counts.assert_called_once_with(level=2, days=7)

    def test_defaults(self):
        ds = MagicMock()
        ds.theme_counts.return_value = []
        query = "{ themeArticleCounts { code count } }"
        test_schema.execute_sync(query, context_value=_ctx(typesense_ds=ds))
        ds.theme_counts.assert_called_once_with(level=1, days=30)


# ---------------------------------------------------------------------------
# releaseArticles
# ---------------------------------------------------------------------------
RELEASE_ARTICLES_QUERY = """
    query($id: String!) {
        releaseArticles(id: $id) {
            uniqueId
            title
        }
    }
"""


def _release(release_id="rel-1", clipping_id="clip-1", since_hours=24) -> ReleaseData:
    return ReleaseData(
        id=release_id,
        clipping_id=clipping_id,
        user_id="user-author",
        clipping_name="Meu Clipping",
        digest="{}",
        digest_html="<p>x</p>",
        articles_count=0,
        created_at=NOW,
        release_url=f"/clipping/release/{release_id}",
        ref_time=NOW,
        since_hours=since_hours,
    )


def _clipping(clipping_id="clip-1", author_user_id="user-author", recortes=None) -> ClippingData:
    return ClippingData(
        id=clipping_id,
        name="Meu Clipping",
        recortes=recortes if recortes is not None else [],
        schedule="0 8 * * *",
        author_user_id=author_user_id,
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )


def _subscription(clipping_id="clip-1", user_id="user-sub") -> SubscriptionData:
    return SubscriptionData(
        id="sub-1",
        clipping_id=clipping_id,
        user_id=user_id,
        role="subscriber",
        delivery_channels={"email": True},
        active=True,
        subscribed_at=NOW,
    )


class TestReleaseArticles:
    def test_public_active_listing_unions_recortes_and_dedups(self):
        recortes = [
            {"id": "r1", "title": "Saude", "themes": ["saude"], "agencies": [], "keywords": []},
            {"id": "r2", "title": "Economia", "themes": [], "agencies": ["me"], "keywords": []},
        ]
        firestore = MagicMock()
        firestore.get_release.return_value = _release()
        firestore.get_marketplace_listing_for_clipping.return_value = {
            "id": "listing-1",
            "active": True,
        }
        firestore.get_clipping.return_value = _clipping(recortes=recortes)

        typesense = MagicMock()
        # r1 retorna a,b ; r2 retorna b,c -> uniao dedup = a,b,c
        typesense.search_articles.side_effect = [
            SearchResult(
                articles=[
                    _doc("a", published_at=NOW - timedelta(hours=1)),
                    _doc("b", published_at=NOW - timedelta(hours=3)),
                ],
                page=1,
                found=2,
            ),
            SearchResult(
                articles=[
                    _doc("b", published_at=NOW - timedelta(hours=3)),
                    _doc("c", published_at=NOW - timedelta(hours=2)),
                ],
                page=1,
                found=2,
            ),
        ]

        result = test_schema.execute_sync(
            RELEASE_ARTICLES_QUERY,
            variable_values={"id": "rel-1"},
            context_value=_ctx(typesense_ds=typesense, firestore_ds=firestore),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        ids = [a["uniqueId"] for a in result.data["releaseArticles"]]
        # dedup por unique_id + ordenado por published_at desc
        assert ids == ["a", "c", "b"]
        assert typesense.search_articles.call_count == 2

    def test_time_window_passed_to_search(self):
        recortes = [
            {"id": "r1", "title": "Saude", "themes": ["saude"], "agencies": [], "keywords": []},
        ]
        firestore = MagicMock()
        firestore.get_release.return_value = _release(since_hours=48)
        firestore.get_marketplace_listing_for_clipping.return_value = {"id": "l1", "active": True}
        firestore.get_clipping.return_value = _clipping(recortes=recortes)
        typesense = MagicMock()
        typesense.search_articles.return_value = SearchResult(articles=[], page=1, found=0)

        test_schema.execute_sync(
            RELEASE_ARTICLES_QUERY,
            variable_values={"id": "rel-1"},
            context_value=_ctx(typesense_ds=typesense, firestore_ds=firestore),
        )
        _, kwargs = typesense.search_articles.call_args
        start = datetime.fromisoformat(kwargs["start_date"])
        end = datetime.fromisoformat(kwargs["end_date"])
        assert end == NOW
        assert start == NOW - timedelta(hours=48)
        assert kwargs["themes"] == ["saude"]
        # Recorte sem keywords -> q=* filter-only.
        assert kwargs["q"] == "*"
        assert kwargs["query_by"] is None

    def test_keywords_issue_one_query_per_keyword(self):
        recortes = [
            {
                "id": "r1",
                "title": "Saude",
                "themes": ["saude"],
                "agencies": [],
                "keywords": ["vacina", "dengue"],
            },
        ]
        firestore = MagicMock()
        firestore.get_release.return_value = _release()
        firestore.get_marketplace_listing_for_clipping.return_value = {
            "id": "l1",
            "active": True,
        }
        firestore.get_clipping.return_value = _clipping(recortes=recortes)
        typesense = MagicMock()
        # "vacina" -> a,b ; "dengue" -> b,c -> uniao dedup = a,b,c
        typesense.search_articles.side_effect = [
            SearchResult(
                articles=[
                    _doc("a", published_at=NOW - timedelta(hours=1)),
                    _doc("b", published_at=NOW - timedelta(hours=3)),
                ],
                page=1,
                found=2,
            ),
            SearchResult(
                articles=[
                    _doc("b", published_at=NOW - timedelta(hours=3)),
                    _doc("c", published_at=NOW - timedelta(hours=2)),
                ],
                page=1,
                found=2,
            ),
        ]
        result = test_schema.execute_sync(
            RELEASE_ARTICLES_QUERY,
            variable_values={"id": "rel-1"},
            context_value=_ctx(typesense_ds=typesense, firestore_ds=firestore),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        ids = [a["uniqueId"] for a in result.data["releaseArticles"]]
        assert ids == ["a", "c", "b"]
        # Uma busca por keyword, com q real sobre title,summary.
        assert typesense.search_articles.call_count == 2
        qs = [c.kwargs["q"] for c in typesense.search_articles.call_args_list]
        assert qs == ["vacina", "dengue"]
        for c in typesense.search_articles.call_args_list:
            assert c.kwargs["query_by"] == "title,summary"
            assert c.kwargs["themes"] == ["saude"]

    def test_unauthorized_returns_empty(self):
        firestore = MagicMock()
        firestore.get_release.return_value = _release()
        firestore.get_marketplace_listing_for_clipping.return_value = None  # inativo
        firestore.get_clipping.return_value = _clipping(author_user_id="user-author")
        firestore.get_subscription.return_value = None
        typesense = MagicMock()

        result = test_schema.execute_sync(
            RELEASE_ARTICLES_QUERY,
            variable_values={"id": "rel-1"},
            context_value=_ctx(
                typesense_ds=typesense, firestore_ds=firestore, user_id="outsider"
            ),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["releaseArticles"] == []
        typesense.search_articles.assert_not_called()

    def test_nonexistent_release_returns_empty(self):
        firestore = MagicMock()
        firestore.get_release.return_value = None
        result = test_schema.execute_sync(
            RELEASE_ARTICLES_QUERY,
            variable_values={"id": "missing"},
            context_value=_ctx(typesense_ds=MagicMock(), firestore_ds=firestore),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["releaseArticles"] == []

    def test_author_reads_private_release(self):
        recortes = [
            {"id": "r1", "title": "Saude", "themes": ["saude"], "agencies": [], "keywords": []},
        ]
        firestore = MagicMock()
        firestore.get_release.return_value = _release()
        firestore.get_marketplace_listing_for_clipping.return_value = None
        firestore.get_clipping.return_value = _clipping(
            author_user_id="user-author", recortes=recortes
        )
        firestore.get_subscription.return_value = None
        typesense = MagicMock()
        typesense.search_articles.return_value = SearchResult(
            articles=[_doc("a")], page=1, found=1
        )
        result = test_schema.execute_sync(
            RELEASE_ARTICLES_QUERY,
            variable_values={"id": "rel-1"},
            context_value=_ctx(
                typesense_ds=typesense, firestore_ds=firestore, user_id="user-author"
            ),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert [a["uniqueId"] for a in result.data["releaseArticles"]] == ["a"]

    def test_subscriber_reads_private_release(self):
        recortes = [
            {"id": "r1", "title": "Saude", "themes": ["saude"], "agencies": [], "keywords": []},
        ]
        firestore = MagicMock()
        firestore.get_release.return_value = _release()
        firestore.get_marketplace_listing_for_clipping.return_value = None
        firestore.get_clipping.return_value = _clipping(
            author_user_id="user-author", recortes=recortes
        )
        firestore.get_subscription.return_value = _subscription(user_id="user-sub")
        typesense = MagicMock()
        typesense.search_articles.return_value = SearchResult(
            articles=[_doc("a")], page=1, found=1
        )
        result = test_schema.execute_sync(
            RELEASE_ARTICLES_QUERY,
            variable_values={"id": "rel-1"},
            context_value=_ctx(
                typesense_ds=typesense, firestore_ds=firestore, user_id="user-sub"
            ),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert [a["uniqueId"] for a in result.data["releaseArticles"]] == ["a"]


# ---------------------------------------------------------------------------
# estimateRecorteCount
# ---------------------------------------------------------------------------
ESTIMATE_QUERY = """
    query($t: [String!]!, $a: [String!]!, $k: [String!]!, $h: Int!) {
        estimateRecorteCount(themes: $t, agencies: $a, keywords: $k, sinceHours: $h)
    }
"""


class TestEstimateRecorteCount:
    def test_max_across_keywords(self):
        ds = MagicMock()
        ds.search_articles.side_effect = [
            SearchResult(articles=[], page=1, found=10),
            SearchResult(articles=[], page=1, found=37),
            SearchResult(articles=[], page=1, found=5),
        ]
        result = test_schema.execute_sync(
            ESTIMATE_QUERY,
            variable_values={"t": ["saude"], "a": [], "k": ["a", "b", "c"], "h": 24},
            context_value=_ctx(typesense_ds=ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["estimateRecorteCount"] == 37
        assert ds.search_articles.call_count == 3
        # Cada keyword vira um `q` real sobre title,summary.
        qs = [c.kwargs["q"] for c in ds.search_articles.call_args_list]
        assert qs == ["a", "b", "c"]
        for c in ds.search_articles.call_args_list:
            assert c.kwargs["query_by"] == "title,summary"
            assert c.kwargs["themes"] == ["saude"]

    def test_single_count_when_no_keywords(self):
        ds = MagicMock()
        ds.search_articles.return_value = SearchResult(articles=[], page=1, found=21)
        result = test_schema.execute_sync(
            ESTIMATE_QUERY,
            variable_values={"t": ["saude"], "a": ["me"], "k": [], "h": 24},
            context_value=_ctx(typesense_ds=ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["estimateRecorteCount"] == 21
        assert ds.search_articles.call_count == 1
        # Sem keywords: q=* filter-only (sem query_by).
        _, kwargs = ds.search_articles.call_args
        assert kwargs["q"] == "*"
        assert kwargs["query_by"] is None

    def test_filter_built_with_since_hours(self):
        ds = MagicMock()
        ds.search_articles.return_value = SearchResult(articles=[], page=1, found=0)
        before = datetime.now(tz=timezone.utc)
        test_schema.execute_sync(
            ESTIMATE_QUERY,
            variable_values={"t": ["saude"], "a": [], "k": [], "h": 12},
            context_value=_ctx(typesense_ds=ds),
        )
        after = datetime.now(tz=timezone.utc)
        _, kwargs = ds.search_articles.call_args
        start = datetime.fromisoformat(kwargs["start_date"])
        # start ~= now - 12h (dentro da janela de execucao do teste)
        assert before - timedelta(hours=12) - timedelta(seconds=5) <= start
        assert start <= after - timedelta(hours=12) + timedelta(seconds=5)
        assert kwargs["themes"] == ["saude"]

    def test_no_filters_returns_zero(self):
        ds = MagicMock()
        result = test_schema.execute_sync(
            ESTIMATE_QUERY,
            variable_values={"t": [], "a": [], "k": [], "h": 24},
            context_value=_ctx(typesense_ds=ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["estimateRecorteCount"] == 0
        ds.search_articles.assert_not_called()


# ---------------------------------------------------------------------------
# SDL surface
# ---------------------------------------------------------------------------
class TestSDL:
    def test_theme_count_type_and_fields(self):
        sdl = test_schema.as_str()
        assert "type ThemeCount {" in sdl
        block = sdl.split("type ThemeCount {", 1)[1].split("}", 1)[0]
        assert "code: String!" in block
        assert "label: String" in block
        assert "count: Int!" in block

    def test_query_signatures(self):
        sdl = test_schema.as_str()
        assert "relatedArticles(uniqueId: String!, limit: Int! = 4): [Article!]!" in sdl
        assert "themeArticleCounts(days: Int! = 30, level: Int! = 1): [ThemeCount!]!" in sdl
        assert "releaseArticles(id: String!): [Article!]!" in sdl
        assert (
            "estimateRecorteCount(themes: [String!]!, agencies: [String!]!, "
            "keywords: [String!]!, sinceHours: Int! = 24): Int!" in sdl
        )
