"""Testes do datasource Postgres para o grafo de entidades (Fase 6c).

Cobre `get_related_entities` (1-hop co-menção) e `get_entity_network` (CTE
recursiva, clamp de depth). Mock do pool asyncpg — verifica que o `entity_id`
vai SEMPRE parametrizado ($1), o clamp de depth e o mapeamento das linhas.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from graphql_api.datasources.postgres import (
    EntityNetworkEdgeRecord,
    EntityNetworkNodeRecord,
    PostgresDatasource,
    RelatedEntityRecord,
)


def _make_mock_pool(fetch_results=None):
    """Pool com conn.fetch retornando, em sequência, cada item de
    `fetch_results` (lista de listas de rows). Útil quando o método faz
    múltiplas queries (network: nós + arestas)."""
    pool = MagicMock()
    conn = AsyncMock()
    if fetch_results is None:
        conn.fetch = AsyncMock(return_value=[])
    else:
        conn.fetch = AsyncMock(side_effect=list(fetch_results))

    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm
    return pool, conn


class TestGetRelatedEntities:
    @pytest.mark.asyncio
    async def test_maps_other_end_and_weight(self):
        rows = [
            {
                "other_id": "Q216330",
                "weight": 42,
                "kind": "co_mention",
                "canonical_name": "Ministério da Educação",
                "type": "ORG",
                "wikidata_id": "Q216330",
            },
            {
                "other_id": "dgb_abc",
                "weight": 7,
                "kind": "co_mention",
                "canonical_name": "Política X",
                "type": "POLICY",
                "wikidata_id": None,
            },
        ]
        pool, conn = _make_mock_pool([rows])
        ds = PostgresDatasource(pool)

        result = await ds.get_related_entities("Q123", limit=12)

        assert len(result) == 2
        assert all(isinstance(r, RelatedEntityRecord) for r in result)
        assert result[0].entity_id == "Q216330"
        assert result[0].weight == 42
        assert result[1].entity_id == "dgb_abc"
        assert result[1].wikidata_id is None
        # entity_id SEMPRE parametrizado como $1 (anti-injection); limit como $2.
        args = conn.fetch.await_args.args
        assert args[1] == "Q123"
        assert args[2] == 12

    @pytest.mark.asyncio
    async def test_empty_entity_id_returns_empty_without_query(self):
        pool, conn = _make_mock_pool([[]])
        ds = PostgresDatasource(pool)
        result = await ds.get_related_entities("", limit=12)
        assert result == []
        conn.fetch.assert_not_awaited()


class TestGetEntityNetwork:
    @pytest.mark.asyncio
    async def test_returns_nodes_and_edges(self):
        node_rows = [
            {
                "entity_id": "Q1",
                "canonical_name": "Finep",
                "type": "ORG",
                "wikidata_id": "Q1",
            },
            {
                "entity_id": "Q2",
                "canonical_name": "MCTI",
                "type": "ORG",
                "wikidata_id": "Q2",
            },
        ]
        edge_rows = [
            {"src": "Q1", "dst": "Q2", "weight": 9, "kind": "co_mention"},
        ]
        pool, conn = _make_mock_pool([node_rows, edge_rows])
        ds = PostgresDatasource(pool)

        net = await ds.get_entity_network("Q1", depth=2, limit=50)

        assert [type(n) for n in net.nodes] == [EntityNetworkNodeRecord] * 2
        assert net.nodes[0].entity_id == "Q1"
        assert [type(e) for e in net.edges] == [EntityNetworkEdgeRecord]
        assert net.edges[0].src == "Q1"
        assert net.edges[0].dst == "Q2"
        assert net.edges[0].weight == 9
        # Duas queries: nós e arestas.
        assert conn.fetch.await_count == 2

    @pytest.mark.asyncio
    async def test_clamps_depth_to_max_2(self):
        pool, conn = _make_mock_pool([[], []])
        ds = PostgresDatasource(pool)

        await ds.get_entity_network("Q1", depth=99, limit=50)

        # Ambas as queries (nós + arestas) recebem depth clampado a 2 como $2.
        for call in conn.fetch.await_args_list:
            assert call.args[1] == "Q1"  # entity_id parametrizado
            assert call.args[2] == 2  # depth clampado

    @pytest.mark.asyncio
    async def test_clamps_negative_depth_to_zero(self):
        pool, conn = _make_mock_pool([[], []])
        ds = PostgresDatasource(pool)

        await ds.get_entity_network("Q1", depth=-5, limit=50)

        for call in conn.fetch.await_args_list:
            assert call.args[2] == 0

    @pytest.mark.asyncio
    async def test_empty_entity_id_returns_empty_without_query(self):
        pool, conn = _make_mock_pool([[], []])
        ds = PostgresDatasource(pool)
        net = await ds.get_entity_network("", depth=1, limit=50)
        assert net.nodes == []
        assert net.edges == []
        conn.fetch.assert_not_awaited()
