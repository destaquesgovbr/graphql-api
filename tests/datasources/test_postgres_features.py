import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphql_api.datasources.postgres import (
    _UPSERT_FEATURES_SQL,
    EntityRegistryRecord,
    PostgresDatasource,
)


def _make_mock_pool():
    """Create a mock asyncpg pool with acquire/transaction context managers."""
    pool = AsyncMock()

    # Direct execute on pool (for upsert_features)
    pool.execute = AsyncMock(return_value="INSERT 0 1")

    # Connection returned by acquire
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")

    # Transaction context manager
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)

    # Acquire context manager
    acq = AsyncMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acq)

    return pool, conn


class TestUpsertFeatures:
    @pytest.mark.asyncio
    async def test_upsert_features_merges_jsonb(self):
        pool, _ = _make_mock_pool()
        ds = PostgresDatasource(pool)

        features = {"sentiment_score": 0.85, "word_count": 350}
        result = await ds.upsert_features("news-123", features)

        assert result is True
        pool.execute.assert_awaited_once_with(
            _UPSERT_FEATURES_SQL, "news-123", json.dumps(features)
        )

    @pytest.mark.asyncio
    async def test_upsert_nonexistent_news_returns_false(self):
        pool, _ = _make_mock_pool()
        pool.execute = AsyncMock(return_value=None)
        ds = PostgresDatasource(pool)

        result = await ds.upsert_features("nonexistent-id", {"key": "value"})

        assert result is False


class TestBatchUpsertFeatures:
    @pytest.mark.asyncio
    async def test_batch_upsert_features_processes_all(self):
        pool, conn = _make_mock_pool()
        ds = PostgresDatasource(pool)

        items = [
            ("news-1", {"sentiment_score": 0.9}),
            ("news-2", {"word_count": 200}),
            ("news-3", {"has_image": True}),
        ]

        processed, failed = await ds.batch_upsert_features(items)

        assert processed == 3
        assert failed == 0
        assert conn.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_batch_upsert_empty_returns_zero(self):
        pool, conn = _make_mock_pool()
        ds = PostgresDatasource(pool)

        processed, failed = await ds.batch_upsert_features([])

        assert processed == 0
        assert failed == 0
        conn.execute.assert_not_awaited()


def _make_fetch_pool(rows, *, fetchrow=None):
    """Pool mock com `conn.fetch` retornando `rows` (lista de dicts).

    `fetchrow` opcional define o retorno de `conn.fetchrow` (linha única)."""
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    acq = AsyncMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acq)
    return pool, conn


class TestGetFeaturesBatch:
    @pytest.mark.asyncio
    async def test_returns_features_keyed_by_unique_id(self):
        rows = [
            {
                "unique_id": "a",
                "features": {
                    "word_count": 100,
                    "entities": [{"text": "X", "type": "ORG", "count": 2}],
                },
            },
            {"unique_id": "b", "features": {"trending_score": 1.5}},
        ]
        pool, conn = _make_fetch_pool(rows)
        ds = PostgresDatasource(pool)

        result = await ds.get_features_batch(["a", "b", "c"])

        assert result["a"]["word_count"] == 100
        assert result["a"]["entities"][0]["text"] == "X"
        assert result["b"]["trending_score"] == 1.5
        assert "c" not in result  # ausente em news_features
        conn.fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_jsonb_string_is_parsed(self):
        # asyncpg devolve jsonb como str (sem codec) — deve ser desserializado.
        rows = [{"unique_id": "a", "features": '{"word_count": 5}'}]
        pool, _ = _make_fetch_pool(rows)
        ds = PostgresDatasource(pool)

        result = await ds.get_features_batch(["a"])

        assert result["a"]["word_count"] == 5

    @pytest.mark.asyncio
    async def test_empty_ids_returns_empty_no_query(self):
        pool, conn = _make_fetch_pool([])
        ds = PostgresDatasource(pool)

        result = await ds.get_features_batch([])

        assert result == {}
        conn.fetch.assert_not_awaited()


class TestGetEntity:
    @pytest.mark.asyncio
    async def test_maps_row_to_entity_registry_record(self):
        row = {
            "entity_id": "Q216330",
            "canonical_name": "Ministério da Educação",
            "type": "ORG",
            "aliases": ["MEC", "Ministério da Educação (MEC)"],
            "wikidata_id": "Q216330",
            "wikidata_url": "https://www.wikidata.org/wiki/Q216330",
            "description": "Ministério do Brasil",
            "agency_key": "mec",
        }
        pool, conn = _make_fetch_pool([], fetchrow=row)
        ds = PostgresDatasource(pool)

        rec = await ds.get_entity("Q216330")

        assert isinstance(rec, EntityRegistryRecord)
        assert rec.entity_id == "Q216330"
        assert rec.canonical_name == "Ministério da Educação"
        assert rec.type == "ORG"
        assert rec.aliases == ["MEC", "Ministério da Educação (MEC)"]
        assert rec.wikidata_id == "Q216330"
        assert rec.agency_key == "mec"
        conn.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aliases_jsonb_string_is_parsed(self):
        # asyncpg devolve JSONB como str (sem codec) — aliases deve virar list.
        row = {
            "entity_id": "dgb_abc",
            "canonical_name": "Pé-de-Meia",
            "type": "POLICY",
            "aliases": '["Pé de Meia", "Pe-de-Meia"]',
            "wikidata_id": None,
            "wikidata_url": None,
            "description": None,
            "agency_key": None,
        }
        pool, _ = _make_fetch_pool([], fetchrow=row)
        ds = PostgresDatasource(pool)

        rec = await ds.get_entity("dgb_abc")

        assert rec.aliases == ["Pé de Meia", "Pe-de-Meia"]
        assert rec.wikidata_id is None

    @pytest.mark.asyncio
    async def test_aliases_null_or_malformed_becomes_empty_list(self):
        row = {
            "entity_id": "dgb_x",
            "canonical_name": "X",
            "type": "MISC",
            "aliases": None,
            "wikidata_id": None,
            "wikidata_url": None,
            "description": None,
            "agency_key": None,
        }
        pool, _ = _make_fetch_pool([], fetchrow=row)
        ds = PostgresDatasource(pool)

        rec = await ds.get_entity("dgb_x")

        assert rec.aliases == []

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        pool, _ = _make_fetch_pool([], fetchrow=None)
        ds = PostgresDatasource(pool)

        rec = await ds.get_entity("missing")

        assert rec is None


class TestGetEntitiesBatch:
    @pytest.mark.asyncio
    async def test_returns_dict_keyed_by_entity_id(self):
        rows = [
            {
                "entity_id": "Q216330",
                "canonical_name": "Ministério da Educação",
                "type": "ORG",
                "aliases": ["MEC"],
                "wikidata_id": "Q216330",
                "wikidata_url": None,
                "description": None,
                "agency_key": "mec",
            },
            {
                "entity_id": "dgb_abc",
                "canonical_name": "Pé-de-Meia",
                "type": "POLICY",
                "aliases": [],
                "wikidata_id": None,
                "wikidata_url": None,
                "description": None,
                "agency_key": None,
            },
        ]
        pool, conn = _make_fetch_pool(rows)
        ds = PostgresDatasource(pool)

        result = await ds.get_entities_batch(["Q216330", "dgb_abc", "ghost"])

        assert set(result.keys()) == {"Q216330", "dgb_abc"}
        assert result["Q216330"].canonical_name == "Ministério da Educação"
        assert result["dgb_abc"].type == "POLICY"
        assert "ghost" not in result
        conn.fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_ids_returns_empty_no_query(self):
        pool, conn = _make_fetch_pool([])
        ds = PostgresDatasource(pool)

        result = await ds.get_entities_batch([])

        assert result == {}
        conn.fetch.assert_not_awaited()


# --- F0c: chave de sentimento (aninhada com fallback plano) ----------------------


class TestSentimentHelper:
    def test_aninhado(self):
        from graphql_api.datasources.postgres import _sentiment

        assert _sentiment({"sentiment": {"label": "positive", "score": 0.7}}) == ("positive", 0.7)

    def test_fallback_plano(self):
        from graphql_api.datasources.postgres import _sentiment

        assert _sentiment({"sentiment_label": "negative", "sentiment_score": -0.2}) == ("negative", -0.2)

    def test_aninhado_prevalece_e_completa_campo_a_campo(self):
        # Mesma semântica do COALESCE do SQL: cada campo cai para o plano se o aninhado é nulo.
        from graphql_api.datasources.postgres import _sentiment

        feats = {"sentiment": {"label": "neutral", "score": None}, "sentiment_label": "x", "sentiment_score": 0.1}
        assert _sentiment(feats) == ("neutral", 0.1)

    def test_sentimento_nao_objeto_usa_plano(self):
        from graphql_api.datasources.postgres import _sentiment

        assert _sentiment({"sentiment": "positive", "sentiment_label": "positive"}) == ("positive", None)

    def test_sem_sentimento(self):
        from graphql_api.datasources.postgres import _sentiment

        assert _sentiment({}) == (None, None)

    def test_score_texto_numerico_vira_float(self):
        # Como o `::float` do SQL: o score sai float mesmo gravado como texto JSON.
        from graphql_api.datasources.postgres import _sentiment

        assert _sentiment({"sentiment": {"label": "positive", "score": "0.5"}}) == ("positive", 0.5)
        assert _sentiment({"sentiment_score": 1}) == (None, 1.0)
        assert isinstance(_sentiment({"sentiment_score": 1})[1], float)

    def test_valor_invalido_conta_como_nulo_e_cai_para_plano(self):
        # Onde o SQL levantaria erro no cast, o mapeador trata como nulo (não derruba o lote).
        from graphql_api.datasources.postgres import _sentiment

        feats = {"sentiment": {"label": 1, "score": "n/a"}, "sentiment_label": "neutral", "sentiment_score": 0.2}
        assert _sentiment(feats) == ("neutral", 0.2)
        assert _sentiment({"sentiment_score": True}) == (None, None)
        assert _sentiment({"sentiment_score": float("nan")}) == (None, None)


class TestTypesenseDocSentimento:
    @pytest.mark.asyncio
    async def test_doc_typesense_le_sentimento_aninhado(self):
        row = {
            "unique_id": "n-1",
            "title": "T",
            "url": "https://gov.br/n-1",
            "features": {"sentiment": {"label": "positive", "score": 0.6}},
        }
        pool, _ = _make_fetch_pool([], fetchrow=row)
        ds = PostgresDatasource(pool)

        doc = await ds.get_news_for_typesense("n-1")

        assert doc.sentiment_label == "positive"
        assert doc.sentiment_score == 0.6

    @pytest.mark.asyncio
    async def test_doc_typesense_cai_para_plano(self):
        row = {
            "unique_id": "n-2",
            "title": "T",
            "url": "https://gov.br/n-2",
            "features": {"sentiment_label": "negative", "sentiment_score": -0.3},
        }
        pool, _ = _make_fetch_pool([], fetchrow=row)
        ds = PostgresDatasource(pool)

        doc = await ds.get_news_for_typesense("n-2")

        assert doc.sentiment_label == "negative"
        assert doc.sentiment_score == -0.3

    @pytest.mark.asyncio
    async def test_doc_typesense_features_em_str_json_como_o_asyncpg_entrega(self):
        # O pool não registra codec de JSONB: em produção `features` chega como str.
        feats = {"sentiment": {"label": "positive", "score": 0.6}, "word_count": 99}
        row = {
            "unique_id": "n-3",
            "title": "T",
            "url": "https://gov.br/n-3",
            "features": json.dumps(feats),
        }
        pool, _ = _make_fetch_pool([], fetchrow=row)
        ds = PostgresDatasource(pool)

        doc = await ds.get_news_for_typesense("n-3")

        assert doc.sentiment_label == "positive"
        assert doc.sentiment_score == 0.6
        assert doc.word_count == 99
        assert doc.features == feats

    @pytest.mark.asyncio
    async def test_doc_typesense_features_str_json_nao_objeto_vira_vazio(self):
        row = {"unique_id": "n-4", "title": "T", "url": "https://gov.br/n-4", "features": "[]"}
        pool, _ = _make_fetch_pool([], fetchrow=row)
        ds = PostgresDatasource(pool)

        doc = await ds.get_news_for_typesense("n-4")

        assert doc.features == {}
        assert doc.sentiment_label is None
        assert doc.sentiment_score is None


def _flat(sql: str) -> str:
    return " ".join(sql.split())


_NESTED_LABEL = "COALESCE(nf.features->'sentiment'->>'label', nf.features->>'sentiment_label')"
_NESTED_SCORE = "COALESCE(nf.features->'sentiment'->>'score', nf.features->>'sentiment_score')"


class TestSentimentoNasQueries:
    """agencyAnalytics (MONTH/WEEK e DAY) e entityCoverage leem a chave aninhada com
    fallback plano; pct_* é a fração dos artigos COM rótulo (NULL sem rótulo)."""

    async def _sql_agency_analytics(self, granularity: str) -> str:
        pool, conn = _make_fetch_pool([])
        ds = PostgresDatasource(pool)
        await ds.agency_analytics(granularity, ["mec"], "2026-09-01", "2026-09-30")
        return _flat(conn.fetch.await_args.args[0])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("granularity", ["month", "week", "day"])
    async def test_agency_analytics_le_chave_aninhada_com_fallback(self, granularity):
        sql = await self._sql_agency_analytics(granularity)
        assert _NESTED_LABEL in sql
        assert _NESTED_SCORE in sql
        # Sem leitura isolada da chave plana (o bug: features->>'sentiment_score' sozinho).
        assert "AVG((nf.features->>'sentiment_score')::float)" not in sql

    @pytest.mark.asyncio
    @pytest.mark.parametrize("granularity", ["month", "day"])
    async def test_pct_nulo_quando_sem_rotulo(self, granularity):
        sql = await self._sql_agency_analytics(granularity)
        # Antes: ELSE 0.0 contava artigo sem rótulo como "não positivo" (pct 0 em vez de NULL).
        assert "ELSE 0.0" not in sql
        assert "WHEN s.label = 'positive' THEN 1.0 WHEN s.label IS NOT NULL THEN 0.0 END" in sql
        assert "WHEN s.label = 'negative' THEN 1.0 WHEN s.label IS NOT NULL THEN 0.0 END" in sql

    @pytest.mark.asyncio
    async def test_entity_coverage_le_score_aninhado_com_fallback(self):
        pool, conn = _make_fetch_pool([])
        ds = PostgresDatasource(pool)
        await ds.entity_coverage("Q1", "month")
        sql = _flat(conn.fetch.await_args.args[0])
        assert _NESTED_SCORE in sql
        assert "AVG((nf.features->>'sentiment_score')::float)" not in sql
