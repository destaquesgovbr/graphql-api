"""Testes para a query policies() e o tipo PolicyListItem."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphql_api.schema.types.entities import PolicyListItem
from graphql_api.schema.resolvers.entities import EntityQuery


def _make_info(rows):
    ds = MagicMock()
    ds.list_policies = AsyncMock(return_value=rows)
    ctx = MagicMock()
    ctx.postgres_ds = ds
    info = MagicMock()
    info.context = ctx
    return info


MOCK_ROWS = [
    {
        "entity_id": "dgb_bolsa-familia",
        "canonical_name": "Bolsa Família",
        "domain": "SOCIAL",
        "lifecycle_phase": "ROUTINE",
        "wikidata_id": "Q327254",
        "aliases": ["PBF"],
        "article_count": 312,
    },
    {
        "entity_id": "dgb_novo-pac",
        "canonical_name": "Novo PAC",
        "domain": "ECONOMIC",
        "lifecycle_phase": "IMPLEMENTATION",
        "wikidata_id": None,
        "aliases": [],
        "article_count": 89,
    },
]


class TestPoliciesQuery:
    @pytest.mark.asyncio
    async def test_retorna_lista_de_policy_list_items(self):
        info = _make_info(MOCK_ROWS)
        query = EntityQuery()
        result = await query.policies(info)
        assert len(result) == 2
        assert all(isinstance(p, PolicyListItem) for p in result)

    @pytest.mark.asyncio
    async def test_campos_mapeados_corretamente(self):
        info = _make_info(MOCK_ROWS)
        query = EntityQuery()
        result = await query.policies(info)
        bf = next(p for p in result if p.entity_id == "dgb_bolsa-familia")
        assert bf.canonical_name == "Bolsa Família"
        assert bf.domain == "SOCIAL"
        assert bf.lifecycle_phase == "ROUTINE"
        assert bf.article_count == 312
        assert "PBF" in bf.aliases

    @pytest.mark.asyncio
    async def test_filtro_domain_passado_ao_datasource(self):
        info = _make_info([MOCK_ROWS[0]])
        query = EntityQuery()
        await query.policies(info, domain="SOCIAL")
        info.context.postgres_ds.list_policies.assert_called_once_with(
            "SOCIAL", None, 20, 0
        )

    @pytest.mark.asyncio
    async def test_filtro_lifecycle_phase_passado_ao_datasource(self):
        info = _make_info([MOCK_ROWS[0]])
        query = EntityQuery()
        await query.policies(info, lifecycle_phase="ROUTINE")
        info.context.postgres_ds.list_policies.assert_called_once_with(
            None, "ROUTINE", 20, 0
        )

    @pytest.mark.asyncio
    async def test_limit_clamped_a_200(self):
        info = _make_info([])
        query = EntityQuery()
        await query.policies(info, limit=500)
        info.context.postgres_ds.list_policies.assert_called_once_with(
            None, None, 200, 0
        )

    @pytest.mark.asyncio
    async def test_lista_vazia_quando_sem_policies(self):
        info = _make_info([])
        query = EntityQuery()
        result = await query.policies(info)
        assert result == []

    @pytest.mark.asyncio
    async def test_domain_none_quando_nao_preenchido(self):
        rows = [{**MOCK_ROWS[0], "domain": None, "lifecycle_phase": None}]
        info = _make_info(rows)
        query = EntityQuery()
        result = await query.policies(info)
        assert result[0].domain is None
        assert result[0].lifecycle_phase is None
