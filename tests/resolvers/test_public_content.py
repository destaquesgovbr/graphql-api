"""Testes dos resolvers de conteudo publico (Fase 2A, Stream B3).

Cobre `relatedArticles`, `themeArticleCounts`, `releaseArticles` e
`estimateRecorteCount` em `resolvers/public_content.py`. Mock de datasource
(Typesense + Firestore). Schema de teste minimo (sem subir o app).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import strawberry

from graphql_api.context import GraphQLContext, User
from graphql_api.datasources.firestore import ClippingData, ReleaseData, SubscriptionData
from graphql_api.datasources.postgres import EntityRegistryRecord, SimilarArticleRecord
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


def _ctx(
    typesense_ds=None, firestore_ds=None, postgres_ds=None, user_id=None
) -> GraphQLContext:
    ctx = GraphQLContext(
        typesense_ds=typesense_ds,
        firestore_ds=firestore_ds,
        postgres_ds=postgres_ds,
    )
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
    """relatedArticles agora é por similaridade semântica (embedding pgvector):
    PostgresDatasource.get_similar_articles → hidratação via
    TypesenseDatasource.get_articles_by_ids, preservando a ordem de similaridade.
    """

    @pytest.mark.asyncio
    async def test_semantic_order_preserved_and_hydrated(self):
        pg = MagicMock()
        pg.get_similar_articles = AsyncMock(
            return_value=[
                SimilarArticleRecord("a", 0.92),
                SimilarArticleRecord("b", 0.81),
                SimilarArticleRecord("c", 0.73),
            ]
        )
        ts = MagicMock()
        ts.get_articles_by_ids.return_value = {
            "a": _doc("a"),
            "b": _doc("b"),
            "c": _doc("c"),
        }
        result = await test_schema.execute(
            RELATED_QUERY,
            variable_values={"id": "base", "limit": 4},
            context_value=_ctx(typesense_ds=ts, postgres_ds=pg),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        ids = [a["uniqueId"] for a in result.data["relatedArticles"]]
        assert ids == ["a", "b", "c"]
        pg.get_similar_articles.assert_awaited_once()
        # over-fetch: pede mais que `limit` p/ compensar ids ausentes no índice
        assert pg.get_similar_articles.await_args.kwargs["limit"] >= 4

    @pytest.mark.asyncio
    async def test_skips_ids_missing_in_typesense(self):
        pg = MagicMock()
        pg.get_similar_articles = AsyncMock(
            return_value=[
                SimilarArticleRecord("a", 0.9),
                SimilarArticleRecord("b", 0.8),
                SimilarArticleRecord("c", 0.7),
            ]
        )
        ts = MagicMock()
        # "b" não está indexado no Typesense → omitido do resultado
        ts.get_articles_by_ids.return_value = {"a": _doc("a"), "c": _doc("c")}
        result = await test_schema.execute(
            RELATED_QUERY,
            variable_values={"id": "base", "limit": 4},
            context_value=_ctx(typesense_ds=ts, postgres_ds=pg),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        ids = [a["uniqueId"] for a in result.data["relatedArticles"]]
        assert ids == ["a", "c"]

    @pytest.mark.asyncio
    async def test_respects_limit(self):
        pg = MagicMock()
        pg.get_similar_articles = AsyncMock(
            return_value=[
                SimilarArticleRecord(c, 0.9 - i * 0.05)
                for i, c in enumerate(["a", "b", "c", "d", "e"])
            ]
        )
        ts = MagicMock()
        ts.get_articles_by_ids.return_value = {
            c: _doc(c) for c in ["a", "b", "c", "d", "e"]
        }
        result = await test_schema.execute(
            RELATED_QUERY,
            variable_values={"id": "base", "limit": 2},
            context_value=_ctx(typesense_ds=ts, postgres_ds=pg),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        ids = [a["uniqueId"] for a in result.data["relatedArticles"]]
        assert ids == ["a", "b"]

    @pytest.mark.asyncio
    async def test_no_similar_returns_empty(self):
        pg = MagicMock()
        pg.get_similar_articles = AsyncMock(return_value=[])
        ts = MagicMock()
        result = await test_schema.execute(
            RELATED_QUERY,
            variable_values={"id": "base", "limit": 4},
            context_value=_ctx(typesense_ds=ts, postgres_ds=pg),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["relatedArticles"] == []
        ts.get_articles_by_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_datasources_returns_empty(self):
        result = await test_schema.execute(
            RELATED_QUERY,
            variable_values={"id": "base", "limit": 4},
            context_value=_ctx(),  # sem typesense_ds / postgres_ds
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

    def test_entity_facet_type_and_suggestions_signature(self):
        sdl = test_schema.as_str()
        assert "type EntityFacet {" in sdl
        block = sdl.split("type EntityFacet {", 1)[1].split("}", 1)[0]
        assert "value: String!" in block
        assert "count: Int!" in block
        # Fase 4: campos canônicos opcionais (aditivos, não-quebrantes).
        assert "entityId: String" in block
        assert "label: String" in block
        assert "entitySuggestions(" in sdl

    def test_entity_node_type_and_query_signature(self):
        sdl = test_schema.as_str()
        assert "type EntityNode {" in sdl
        block = sdl.split("type EntityNode {", 1)[1].split("}", 1)[0]
        assert "entityId: String!" in block
        assert "canonicalName: String" in block
        assert "type: String" in block
        assert "aliases: [String!]!" in block
        assert "wikidataId: String" in block
        assert "wikidataUrl: String" in block
        assert "description: String" in block
        assert "agencyKey: String" in block
        assert "entity(id: String!): EntityNode" in sdl


# ---------------------------------------------------------------------------
# entitySuggestions
# ---------------------------------------------------------------------------
ENTITY_SUGGESTIONS_QUERY = """
    query($q: String!, $t: String, $limit: Int!) {
        entitySuggestions(query: $q, type: $t, limit: $limit) {
            value
            count
        }
    }
"""


class TestEntitySuggestions:
    @pytest.mark.asyncio
    async def test_maps_facets_to_entity_facet(self):
        ds = MagicMock()
        ds.entity_facets.return_value = [
            ("Ministério da Saúde", 12),
            ("Brasília", 5),
        ]
        result = await test_schema.execute(
            ENTITY_SUGGESTIONS_QUERY,
            variable_values={"q": "min", "t": "ORG", "limit": 10},
            context_value=_ctx(typesense_ds=ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["entitySuggestions"] == [
            {"value": "Ministério da Saúde", "count": 12},
            {"value": "Brasília", "count": 5},
        ]
        ds.entity_facets.assert_called_once_with(query="min", entity_type="ORG", limit=10)

    @pytest.mark.asyncio
    async def test_empty_when_no_datasource(self):
        result = await test_schema.execute(
            ENTITY_SUGGESTIONS_QUERY,
            variable_values={"q": "min", "t": None, "limit": 10},
            context_value=_ctx(),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["entitySuggestions"] == []

    @pytest.mark.asyncio
    async def test_text_mode_does_not_touch_postgres(self):
        # Modo texto (type != CANONICAL) não resolve labels: postgres intacto.
        ts = MagicMock()
        ts.entity_facets.return_value = [("Brasília", 3)]
        pg = MagicMock()
        pg.get_entities_batch = AsyncMock(return_value={})
        result = await test_schema.execute(
            ENTITY_SUGGESTIONS_QUERY,
            variable_values={"q": "", "t": "LOC", "limit": 10},
            context_value=_ctx(typesense_ds=ts, postgres_ds=pg),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["entitySuggestions"] == [{"value": "Brasília", "count": 3}]
        pg.get_entities_batch.assert_not_called()


CANONICAL_SUGGESTIONS_QUERY = """
    query($q: String!, $t: String, $limit: Int!) {
        entitySuggestions(query: $q, type: $t, limit: $limit) {
            value
            count
            entityId
            label
        }
    }
"""


class TestEntitySuggestionsCanonical:
    @pytest.mark.asyncio
    async def test_canonical_mode_resolves_labels(self):
        ts = MagicMock()
        # Valores facetados de entity_canonical = canonical_ids (entity_id).
        ts.entity_facets.return_value = [("Q216330", 458), ("dgb_abc", 12)]
        pg = MagicMock()
        pg.get_entities_batch = AsyncMock(
            return_value={
                "Q216330": EntityRegistryRecord(
                    entity_id="Q216330", canonical_name="Ministério da Educação"
                ),
                # dgb_abc existe mas sem canonical_name -> label None.
                "dgb_abc": EntityRegistryRecord(entity_id="dgb_abc"),
            }
        )
        result = await test_schema.execute(
            CANONICAL_SUGGESTIONS_QUERY,
            variable_values={"q": "", "t": "CANONICAL", "limit": 10},
            context_value=_ctx(typesense_ds=ts, postgres_ds=pg),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["entitySuggestions"] == [
            {
                "value": "Q216330",
                "count": 458,
                "entityId": "Q216330",
                "label": "Ministério da Educação",
            },
            {"value": "dgb_abc", "count": 12, "entityId": "dgb_abc", "label": None},
        ]
        # Faceta o campo entity_canonical (type passa pro datasource).
        ts.entity_facets.assert_called_once_with(query="", entity_type="CANONICAL", limit=10)
        pg.get_entities_batch.assert_awaited_once_with(["Q216330", "dgb_abc"])

    @pytest.mark.asyncio
    async def test_canonical_mode_without_postgres_keeps_value_fallback(self):
        # Sem postgres_ds: value = canonical_id, label/entityId presentes mas
        # label None (não-quebrante; o portal exibe o id).
        ts = MagicMock()
        ts.entity_facets.return_value = [("Q216330", 5)]
        result = await test_schema.execute(
            CANONICAL_SUGGESTIONS_QUERY,
            variable_values={"q": "", "t": "CANONICAL", "limit": 10},
            context_value=_ctx(typesense_ds=ts),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["entitySuggestions"] == [
            {"value": "Q216330", "count": 5, "entityId": "Q216330", "label": None}
        ]


# ---------------------------------------------------------------------------
# entity(id)
# ---------------------------------------------------------------------------
ENTITY_QUERY = """
    query($id: String!) {
        entity(id: $id) {
            entityId
            canonicalName
            type
            aliases
            wikidataId
            wikidataUrl
            description
            agencyKey
        }
    }
"""


class TestEntity:
    @pytest.mark.asyncio
    async def test_returns_entity_node(self):
        pg = MagicMock()
        pg.get_entity = AsyncMock(
            return_value=EntityRegistryRecord(
                entity_id="Q216330",
                canonical_name="Ministério da Educação",
                type="ORG",
                aliases=["MEC", "Ministério da Educação (MEC)"],
                wikidata_id="Q216330",
                wikidata_url="https://www.wikidata.org/wiki/Q216330",
                description="Ministério do Brasil",
                agency_key="mec",
            )
        )
        result = await test_schema.execute(
            ENTITY_QUERY,
            variable_values={"id": "Q216330"},
            context_value=_ctx(postgres_ds=pg),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["entity"] == {
            "entityId": "Q216330",
            "canonicalName": "Ministério da Educação",
            "type": "ORG",
            "aliases": ["MEC", "Ministério da Educação (MEC)"],
            "wikidataId": "Q216330",
            "wikidataUrl": "https://www.wikidata.org/wiki/Q216330",
            "description": "Ministério do Brasil",
            "agencyKey": "mec",
        }
        pg.get_entity.assert_awaited_once_with("Q216330")

    @pytest.mark.asyncio
    async def test_null_when_not_found(self):
        pg = MagicMock()
        pg.get_entity = AsyncMock(return_value=None)
        result = await test_schema.execute(
            ENTITY_QUERY,
            variable_values={"id": "missing"},
            context_value=_ctx(postgres_ds=pg),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["entity"] is None

    @pytest.mark.asyncio
    async def test_null_without_postgres(self):
        result = await test_schema.execute(
            ENTITY_QUERY,
            variable_values={"id": "Q1"},
            context_value=_ctx(),  # sem postgres_ds
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["entity"] is None
