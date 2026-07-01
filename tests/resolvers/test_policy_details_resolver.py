from unittest.mock import AsyncMock

import pytest
import strawberry

from graphql_api.context import GraphQLContext
from graphql_api.schema.resolvers.entities import EntityQuery
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.types.entities import PolicyDetails


@strawberry.type
class _Query(HealthQuery, EntityQuery):
    pass


test_schema = strawberry.Schema(query=_Query)


def _make_ctx(postgres_ds=None):
    return GraphQLContext(postgres_ds=postgres_ds)


def _make_policy_details(**kwargs):
    defaults = dict(
        domain="GOVERNANCE",
        lifecycle_phase="ROUTINE",
        enabling_laws=["dgb_lei_456"],
        responsible_agencies=["secom"],
        target_population=["servidores públicos"],
        first_mentioned_date="2023-03-01",
        wikidata_id="Q9876",
        instance_of="Q28",
    )
    defaults.update(kwargs)
    return PolicyDetails(**defaults)


class TestPolicyDetailsResolver:
    @pytest.mark.asyncio
    async def test_retorna_policy_details_para_entity_policy(self):
        """Resolver deve retornar campos de PolicyDetails quando datasource retorna dados."""
        mock_pg = AsyncMock()
        mock_pg.get_policy_details = AsyncMock(return_value=_make_policy_details())

        query = """
        query {
            policyDetails(entityId: "Q9876") {
                domain
                lifecyclePhase
                enablingLaws
                responsibleAgencies
                targetPopulation
                firstMentionedDate
                wikidataId
                instanceOf
            }
        }
        """
        result = await test_schema.execute(query, context_value=_make_ctx(postgres_ds=mock_pg))

        assert result.errors is None
        data = result.data["policyDetails"]
        assert data["domain"] == "GOVERNANCE"
        assert data["lifecyclePhase"] == "ROUTINE"
        assert data["enablingLaws"] == ["dgb_lei_456"]
        assert data["responsibleAgencies"] == ["secom"]
        assert data["targetPopulation"] == ["servidores públicos"]
        assert data["firstMentionedDate"] == "2023-03-01"
        assert data["wikidataId"] == "Q9876"
        assert data["instanceOf"] == "Q28"
        mock_pg.get_policy_details.assert_awaited_once_with("Q9876")

    @pytest.mark.asyncio
    async def test_retorna_null_para_entity_nao_policy(self):
        """Resolver deve retornar null quando datasource retorna None (entidade não POLICY)."""
        mock_pg = AsyncMock()
        mock_pg.get_policy_details = AsyncMock(return_value=None)

        query = '{ policyDetails(entityId: "Q4294522") { domain } }'
        result = await test_schema.execute(query, context_value=_make_ctx(postgres_ds=mock_pg))

        assert result.errors is None
        assert result.data["policyDetails"] is None

    @pytest.mark.asyncio
    async def test_retorna_null_para_entity_inexistente(self):
        """Resolver deve retornar null quando datasource retorna None (entidade inexistente)."""
        mock_pg = AsyncMock()
        mock_pg.get_policy_details = AsyncMock(return_value=None)

        query = '{ policyDetails(entityId: "nao-existe-xyz") { domain } }'
        result = await test_schema.execute(query, context_value=_make_ctx(postgres_ds=mock_pg))

        assert result.errors is None
        assert result.data["policyDetails"] is None
        mock_pg.get_policy_details.assert_awaited_once_with("nao-existe-xyz")
