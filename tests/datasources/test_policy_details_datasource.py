import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphql_api.datasources.postgres import PostgresDatasource
from graphql_api.schema.types.entities import PolicyDetails


def _make_mock_pool(fetchrow_result=None):
    """Cria pool asyncpg mockado com acquire como async context manager."""
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm
    return pool, conn


def _policy_row(extra=None):
    """Linha fictícia de entity_registry com type=POLICY."""
    if extra is None:
        extra = json.dumps({
            "domain": "SOCIAL",
            "lifecycle_phase": "IMPLEMENTATION",
            "enabling_laws": ["dgb_lei_123"],
            "responsible_agencies": ["mds"],
            "target_population": ["idosos", "famílias de baixa renda"],
            "first_mentioned_date": "2024-01-15",
            "wikidata_id": "Q12345",
            "instance_of": "Q28",
        })
    return {"type": "POLICY", "extra": extra}


class TestGetPolicyDetails:
    @pytest.mark.asyncio
    async def test_policy_details_retorna_dados_para_entity_policy(self):
        """Deve retornar PolicyDetails populado quando a entidade é do tipo POLICY."""
        pool, conn = _make_mock_pool(fetchrow_result=_policy_row())
        ds = PostgresDatasource(pool)

        result = await ds.get_policy_details("Q99999")

        assert result is not None
        assert isinstance(result, PolicyDetails)
        assert result.domain == "SOCIAL"
        assert result.lifecycle_phase == "IMPLEMENTATION"
        assert result.enabling_laws == ["dgb_lei_123"]
        assert result.responsible_agencies == ["mds"]
        assert result.target_population == ["idosos", "famílias de baixa renda"]
        assert result.first_mentioned_date == "2024-01-15"
        assert result.wikidata_id == "Q12345"
        assert result.instance_of == "Q28"
        conn.fetchrow.assert_awaited_once_with(
            "SELECT type, extra FROM entity_registry WHERE entity_id = $1",
            "Q99999",
        )

    @pytest.mark.asyncio
    async def test_policy_details_retorna_none_para_entity_nao_policy(self):
        """Deve retornar None quando a entidade existe mas não é do tipo POLICY."""
        row = {"type": "ORG", "extra": None}
        pool, conn = _make_mock_pool(fetchrow_result=row)
        ds = PostgresDatasource(pool)

        result = await ds.get_policy_details("Q4294522")

        assert result is None

    @pytest.mark.asyncio
    async def test_policy_details_retorna_none_para_entity_inexistente(self):
        """Deve retornar None quando entity_id não existe na tabela."""
        pool, conn = _make_mock_pool(fetchrow_result=None)
        ds = PostgresDatasource(pool)

        result = await ds.get_policy_details("inexistente-xyz")

        assert result is None

    @pytest.mark.asyncio
    async def test_policy_details_extra_nulo_retorna_campos_vazios(self):
        """Deve retornar PolicyDetails com listas vazias e opcionais nulos se extra=None."""
        row = {"type": "POLICY", "extra": None}
        pool, _ = _make_mock_pool(fetchrow_result=row)
        ds = PostgresDatasource(pool)

        result = await ds.get_policy_details("Q00001")

        assert result is not None
        assert result.domain is None
        assert result.lifecycle_phase is None
        assert result.enabling_laws == []
        assert result.responsible_agencies == []
        assert result.target_population == []
        assert result.first_mentioned_date is None
        assert result.wikidata_id is None
        assert result.instance_of is None

    @pytest.mark.asyncio
    async def test_policy_details_extra_como_dict_asyncpg_codec(self):
        """Deve funcionar quando asyncpg retorna extra já como dict (com codec JSONB)."""
        extra_dict = {
            "domain": "HEALTH",
            "lifecycle_phase": "ANNOUNCED",
            "enabling_laws": [],
            "responsible_agencies": ["ms"],
            "target_population": ["toda população"],
            "first_mentioned_date": None,
            "wikidata_id": None,
            "instance_of": "Q4830453",
        }
        row = {"type": "POLICY", "extra": extra_dict}
        pool, _ = _make_mock_pool(fetchrow_result=row)
        ds = PostgresDatasource(pool)

        result = await ds.get_policy_details("Q11111")

        assert result is not None
        assert result.domain == "HEALTH"
        assert result.lifecycle_phase == "ANNOUNCED"
        assert result.responsible_agencies == ["ms"]
        assert result.instance_of == "Q4830453"
