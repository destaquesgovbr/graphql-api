"""Testes do datasource Postgres para o grafo de entidades (Fase 6c).

Cobre `get_related_entities` (1-hop co-menção) e `get_entity_network` (CTE
recursiva, clamp de depth). Mock do pool asyncpg — verifica que o `entity_id`
vai SEMPRE parametrizado ($1), o clamp de depth e o mapeamento das linhas.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from graphql_api.datasources.postgres import (
    _ENTITY_NETWORK_EDGES_SQL,
    _ENTITY_NETWORK_EGO_EDGES_SQL,
    _ENTITY_NETWORK_EGO_NODES_SQL,
    _ENTITY_NETWORK_NODES_SQL,
    EntityNetworkEdgeRecord,
    EntityNetworkNodeRecord,
    PostgresDatasource,
    RelatedEntityRecord,
)


def _flat(sql: str) -> str:
    return " ".join(sql.split())


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

    @pytest.mark.asyncio
    async def test_legacy_mantem_sql_e_parametros(self):
        # Modo legado (default, usado pelo portal): mesmas queries recursivas e
        # os mesmos parâmetros posicionais de antes do modo ego.
        pool, conn = _make_mock_pool([[], []])
        ds = PostgresDatasource(pool)

        await ds.get_entity_network("Q1", depth=1, limit=50)

        nodes_call, edges_call = conn.fetch.await_args_list
        assert nodes_call.args == (_ENTITY_NETWORK_NODES_SQL, "Q1", 1)
        assert edges_call.args == (_ENTITY_NETWORK_EDGES_SQL, "Q1", 1, 50)

    def test_legacy_sql_so_ganha_left_joins_de_stats(self):
        nodes = _flat(_ENTITY_NETWORK_NODES_SQL)
        edges = _flat(_ENTITY_NETWORK_EDGES_SQL)
        assert "WITH RECURSIVE reachable" in nodes
        assert "LEFT JOIN entity_stats s ON s.entity_id = r.entity_id" in nodes
        assert "s.article_count AS article_count" in nodes
        assert "s.unique_content_count AS unique_content_count" in nodes
        assert "WITH RECURSIVE reachable" in edges
        assert "e.unique_content_count AS unique_content_weight" in edges
        assert edges.endswith("ORDER BY e.weight DESC LIMIT $3")

    @pytest.mark.asyncio
    async def test_legacy_mapeia_stats_e_tolera_ausencia(self):
        node_rows = [
            {
                "entity_id": "Q1",
                "canonical_name": "Finep",
                "type": "ORG",
                "wikidata_id": "Q1",
                "article_count": 120,
                "unique_content_count": 95,
            },
            # Entidade sem linha em entity_stats (LEFT JOIN → NULL).
            {
                "entity_id": "Q2",
                "canonical_name": "MCTI",
                "type": "ORG",
                "wikidata_id": "Q2",
                "article_count": None,
                "unique_content_count": None,
            },
        ]
        edge_rows = [
            {"src": "Q1", "dst": "Q2", "weight": 9, "kind": "co_mention", "unique_content_weight": 7},
        ]
        pool, _ = _make_mock_pool([node_rows, edge_rows])
        ds = PostgresDatasource(pool)

        net = await ds.get_entity_network("Q1")

        assert net.nodes[0].article_count == 120
        assert net.nodes[0].unique_content_count == 95
        assert net.nodes[1].article_count is None
        assert net.nodes[1].unique_content_count is None
        assert net.edges[0].unique_content_weight == 7


def _ego_rows():
    """Foco Q1 com vizinhos Q2 e Q3; aresta Q2–Q3 entre vizinhos."""
    node_rows = [
        {"entity_id": "Q1", "canonical_name": "Finep", "type": "ORG", "wikidata_id": "Q1"},
        {"entity_id": "Q2", "canonical_name": "MCTI", "type": "ORG", "wikidata_id": "Q2"},
        {"entity_id": "Q3", "canonical_name": "CNPq", "type": "ORG", "wikidata_id": "Q3"},
    ]
    edge_rows = [
        {"src": "Q1", "dst": "Q2", "weight": 40, "kind": "co_mention"},
        {"src": "Q1", "dst": "Q3", "weight": 3, "kind": "co_mention"},
        {"src": "Q2", "dst": "Q3", "weight": 25, "kind": "co_mention"},
    ]
    return node_rows, edge_rows


class TestGetEntityNetworkEgo:
    @pytest.mark.asyncio
    async def test_usa_sqls_ego_com_parametros(self):
        node_rows, edge_rows = _ego_rows()
        pool, conn = _make_mock_pool([node_rows, edge_rows])
        ds = PostgresDatasource(pool)

        await ds.get_entity_network(
            "Q1", depth=2, limit=80, ego_centered=True, max_nodes=10, include_neighbor_edges=True
        )

        nodes_call, edges_call = conn.fetch.await_args_list
        # $1 = foco, $2 = maxNodes, $3 = peso mínimo; depth é ignorado.
        assert nodes_call.args == (_ENTITY_NETWORK_EGO_NODES_SQL, "Q1", 10, 2)
        # $4 = incluir arestas entre vizinhos, $5 = corte dessas arestas.
        assert edges_call.args == (_ENTITY_NETWORK_EGO_EDGES_SQL, "Q1", 10, 2, True, 80)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("max_nodes", "limit", "esperado"),
        [(0, 0, (1, 1)), (-3, -9, (1, 1)), (999, 10_000, (100, 500)), (30, 50, (30, 50))],
    )
    async def test_clampa_max_nodes_e_limit(self, max_nodes, limit, esperado):
        pool, conn = _make_mock_pool([[], []])
        ds = PostgresDatasource(pool)

        await ds.get_entity_network("Q1", limit=limit, ego_centered=True, max_nodes=max_nodes)

        nodes_call, edges_call = conn.fetch.await_args_list
        assert nodes_call.args[2] == esperado[0]
        assert edges_call.args[2] == esperado[0]
        assert edges_call.args[5] == esperado[1]

    @pytest.mark.asyncio
    async def test_sem_arestas_entre_vizinhos(self):
        pool, conn = _make_mock_pool([[], []])
        ds = PostgresDatasource(pool)

        await ds.get_entity_network("Q1", ego_centered=True, include_neighbor_edges=False)

        assert conn.fetch.await_args_list[1].args[4] is False

    def test_sql_ego_nunca_corta_arestas_do_foco(self):
        sql = _flat(_ENTITY_NETWORK_EGO_EDGES_SQL)
        # Vizinhos = top $2 arestas do foco com weight >= $3; o LIMIT $5 só vale
        # para as arestas entre vizinhos; a união final não tem LIMIT.
        assert "e.weight >= $3" in sql
        assert "ORDER BY e.weight DESC, other_id ASC LIMIT $2" in sql
        assert "LIMIT $5" in sql
        tail = sql.rsplit("UNION ALL", 1)[1]
        assert "LIMIT" not in tail
        assert "$4::boolean" in sql
        assert "RECURSIVE" not in sql
        assert "RECURSIVE" not in _flat(_ENTITY_NETWORK_EGO_NODES_SQL)

    def test_sql_ego_nos_e_arestas_escolhem_os_mesmos_vizinhos(self):
        # Nós e arestas são duas queries: o critério de vizinhança precisa ser
        # idêntico (mesma ordem determinística + LIMIT) para não haver órfãos.
        criterio = "ORDER BY e.weight DESC, other_id ASC LIMIT $2"
        assert criterio in _flat(_ENTITY_NETWORK_EGO_NODES_SQL)
        assert criterio in _flat(_ENTITY_NETWORK_EGO_EDGES_SQL)
        assert "LEFT JOIN entity_stats s ON s.entity_id = r.entity_id" in _flat(_ENTITY_NETWORK_EGO_NODES_SQL)

    @pytest.mark.asyncio
    async def test_mantem_todas_as_arestas_do_foco(self):
        node_rows, edge_rows = _ego_rows()
        pool, _ = _make_mock_pool([node_rows, edge_rows])
        ds = PostgresDatasource(pool)

        net = await ds.get_entity_network("Q1", limit=1, ego_centered=True)

        pares = {(e.src, e.dst) for e in net.edges}
        assert {("Q1", "Q2"), ("Q1", "Q3")} <= pares
        assert ("Q2", "Q3") in pares

    @pytest.mark.asyncio
    async def test_sem_nos_ou_arestas_orfaos(self):
        # Defesa contra o grafo mudar entre as duas queries (DAG recalculando
        # entity_edges): aresta com ponta fora dos nós é descartada, e vizinho
        # sem aresta para o foco também.
        node_rows, edge_rows = _ego_rows()
        node_rows.append({"entity_id": "Q9", "canonical_name": "Solto", "type": "ORG", "wikidata_id": None})
        edge_rows.append({"src": "Q2", "dst": "Q7", "weight": 30, "kind": "co_mention"})
        pool, _ = _make_mock_pool([node_rows, edge_rows])
        ds = PostgresDatasource(pool)

        net = await ds.get_entity_network("Q1", ego_centered=True)

        ids = {n.entity_id for n in net.nodes}
        assert ids == {"Q1", "Q2", "Q3"}
        for e in net.edges:
            assert e.src in ids and e.dst in ids
        com_aresta = {e.src for e in net.edges} | {e.dst for e in net.edges}
        assert ids <= com_aresta

    @pytest.mark.asyncio
    async def test_empty_entity_id_returns_empty_without_query(self):
        pool, conn = _make_mock_pool([[], []])
        ds = PostgresDatasource(pool)
        net = await ds.get_entity_network("", ego_centered=True)
        assert net.nodes == []
        assert net.edges == []
        conn.fetch.assert_not_awaited()
