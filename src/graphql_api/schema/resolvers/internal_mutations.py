import strawberry
from strawberry.scalars import JSON
from strawberry.types import Info

from graphql_api.auth.guards import IsInternal
from graphql_api.schema.types.internal import BatchResult, FeatureUpsertInput


@strawberry.type
class InternalMutation:
    @strawberry.mutation(
        description="Upsert features for a news item (merges JSONB)",
        permission_classes=[IsInternal],
    )
    async def upsert_features(
        self, info: Info, unique_id: str, features: JSON
    ) -> bool:
        ctx = info.context
        return await ctx.postgres_ds.upsert_features(unique_id, features)

    @strawberry.mutation(
        description="Batch upsert features for multiple news items",
        permission_classes=[IsInternal],
    )
    async def batch_upsert_features(
        self, info: Info, items: list[FeatureUpsertInput]
    ) -> BatchResult:
        ctx = info.context
        tuples = [(item.unique_id, item.features) for item in items]
        processed, failed = await ctx.postgres_ds.batch_upsert_features(tuples)
        return BatchResult(processed=processed, failed=failed)

    @strawberry.mutation(
        description="Update a single field in a Typesense document",
        permission_classes=[IsInternal],
    )
    def update_typesense_field(
        self, info: Info, unique_id: str, field: str, value: JSON
    ) -> bool:
        ctx = info.context
        return ctx.typesense_admin_ds.update_field(unique_id, field, value)
