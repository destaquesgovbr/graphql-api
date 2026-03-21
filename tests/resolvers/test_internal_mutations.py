from unittest.mock import AsyncMock, MagicMock

import pytest
import strawberry

from graphql_api.context import GraphQLContext, ServiceAccount
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.internal_mutations import InternalMutation


@strawberry.type
class _Query(HealthQuery):
    pass


@strawberry.type
class _Mutation(InternalMutation):
    pass


test_schema = strawberry.Schema(query=_Query, mutation=_Mutation)


def _make_internal_context(postgres_ds=None, typesense_admin_ds=None):
    ctx = GraphQLContext(
        postgres_ds=postgres_ds,
        typesense_admin_ds=typesense_admin_ds,
    )
    ctx.service_account = ServiceAccount(email="worker@project.iam.gserviceaccount.com")
    return ctx


def _make_public_context():
    return GraphQLContext()


class TestUpsertFeaturesMutation:
    @pytest.mark.asyncio
    async def test_upsert_features_with_service_account(self):
        mock_pg = AsyncMock()
        mock_pg.upsert_features = AsyncMock(return_value=True)

        result = await test_schema.execute(
            """
            mutation($uniqueId: String!, $features: JSON!) {
                upsertFeatures(uniqueId: $uniqueId, features: $features)
            }
            """,
            variable_values={
                "uniqueId": "news-123",
                "features": {"sentiment_score": 0.85},
            },
            context_value=_make_internal_context(postgres_ds=mock_pg),
        )

        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["upsertFeatures"] is True
        mock_pg.upsert_features.assert_awaited_once_with(
            "news-123", {"sentiment_score": 0.85}
        )

    @pytest.mark.asyncio
    async def test_upsert_features_forbidden_without_service_account(self):
        result = await test_schema.execute(
            """
            mutation($uniqueId: String!, $features: JSON!) {
                upsertFeatures(uniqueId: $uniqueId, features: $features)
            }
            """,
            variable_values={
                "uniqueId": "news-123",
                "features": {"key": "value"},
            },
            context_value=_make_public_context(),
        )

        assert result.errors is not None
        assert len(result.errors) > 0
        assert "FORBIDDEN" in str(result.errors[0].message)


class TestBatchUpsertFeaturesMutation:
    @pytest.mark.asyncio
    async def test_batch_upsert_features_with_service_account(self):
        mock_pg = AsyncMock()
        mock_pg.batch_upsert_features = AsyncMock(return_value=(3, 0))

        result = await test_schema.execute(
            """
            mutation($items: [FeatureUpsertInput!]!) {
                batchUpsertFeatures(items: $items) {
                    processed
                    failed
                }
            }
            """,
            variable_values={
                "items": [
                    {"uniqueId": "news-1", "features": {"score": 0.9}},
                    {"uniqueId": "news-2", "features": {"score": 0.8}},
                    {"uniqueId": "news-3", "features": {"score": 0.7}},
                ]
            },
            context_value=_make_internal_context(postgres_ds=mock_pg),
        )

        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["batchUpsertFeatures"]["processed"] == 3
        assert result.data["batchUpsertFeatures"]["failed"] == 0
        mock_pg.batch_upsert_features.assert_awaited_once()


class TestUpdateTypesenseField:
    @pytest.mark.asyncio
    async def test_update_typesense_field(self):
        mock_ts_admin = MagicMock()
        mock_ts_admin.update_field = MagicMock(return_value=True)

        result = await test_schema.execute(
            """
            mutation($uniqueId: String!, $field: String!, $value: JSON!) {
                updateTypesenseField(uniqueId: $uniqueId, field: $field, value: $value)
            }
            """,
            variable_values={
                "uniqueId": "news-123",
                "field": "sentiment_score",
                "value": 0.85,
            },
            context_value=_make_internal_context(typesense_admin_ds=mock_ts_admin),
        )

        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["updateTypesenseField"] is True
        mock_ts_admin.update_field.assert_called_once_with(
            "news-123", "sentiment_score", 0.85
        )
