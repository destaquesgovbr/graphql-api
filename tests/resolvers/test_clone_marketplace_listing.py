"""Testes para `cloneMarketplaceListing` retornando `Clipping` (gap-fix pre-rollout).

Hoje a mutation retorna `Boolean!` mas o portal (B3) espera o ID do
clipping novo para redirecionar (`/minha-conta/clipping/[id]`). Schema gap.

Apos o fix:
- Schema: `cloneMarketplaceListing(listingId): Clipping!`
- Resolver chama `ds.clone_marketplace_listing(user_id, listing_id)`
  esperando um `ClippingData` (ou dict) — convertido para Strawberry
  `Clipping` antes de retornar.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import strawberry

from graphql_api.context import GraphQLContext, User
from graphql_api.datasources.firestore import ClippingData
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.marketplace import (
    MarketplaceMutation,
    MarketplaceQuery,
)


@strawberry.type
class _Query(HealthQuery, MarketplaceQuery):
    pass


@strawberry.type
class _Mutation(MarketplaceMutation):
    pass


test_schema = strawberry.Schema(query=_Query, mutation=_Mutation)

NOW = datetime(2024, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_cloned_clipping(**overrides) -> ClippingData:
    base = {
        "id": "clip-clone-new",
        "name": "Clone de Top Economia",
        "description": "Clone do listing",
        "recortes": [],
        "prompt": None,
        "active": True,
        "author_user_id": "user-123",
        "schedule": "0 8 * * *",
        "schedule_time": None,
        "next_run_at": NOW,
        "start_date": None,
        "end_date": None,
        "extra_emails": [],
        "include_history": False,
        "delivery_channels": {
            "email": True,
            "telegram": False,
            "push": False,
            "webhook": False,
        },
        "created_at": NOW,
        "updated_at": NOW,
        "cloned_from": "clip-src-1",
    }
    base.update(overrides)
    return ClippingData.model_validate(base)


def _make_mock_ds(cloned: ClippingData | None = None):
    ds = MagicMock()
    ds.clone_marketplace_listing.return_value = cloned or _make_cloned_clipping()
    return ds


def _authenticated_context(ds, user_id="user-123"):
    ctx = GraphQLContext(firestore_ds=ds)
    ctx.user = User(id=user_id, email="dev@example.com")
    return ctx


def _anonymous_context(ds):
    return GraphQLContext(firestore_ds=ds)


CLONE_MUTATION = """
    mutation($listingId: String!) {
        cloneMarketplaceListing(listingId: $listingId) {
            id
            name
            isAuthor
        }
    }
"""


class TestCloneMarketplaceListingReturnsClipping:
    def test_clone_marketplace_listing_returns_clipping_with_id(self):
        """A mutation retorna um Clipping com `id` do clipping novo."""
        ds = _make_mock_ds(_make_cloned_clipping(id="clip-novo-42"))
        result = test_schema.execute_sync(
            CLONE_MUTATION,
            variable_values={"listingId": "listing-1"},
            context_value=_authenticated_context(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        clipping = result.data["cloneMarketplaceListing"]
        assert clipping["id"] == "clip-novo-42"
        assert clipping["name"] == "Clone de Top Economia"

    def test_clone_marketplace_listing_creates_clipping_for_caller(self):
        """O datasource e chamado com (user_id, listing_id) — caller vira autor."""
        ds = _make_mock_ds(_make_cloned_clipping(author_user_id="user-123"))
        result = test_schema.execute_sync(
            CLONE_MUTATION,
            variable_values={"listingId": "listing-XYZ"},
            context_value=_authenticated_context(ds, user_id="user-123"),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        ds.clone_marketplace_listing.assert_called_once_with("user-123", "listing-XYZ")
        # `isAuthor` true porque author_user_id == ctx.user.id
        assert result.data["cloneMarketplaceListing"]["isAuthor"] is True

    def test_clone_marketplace_listing_requires_auth(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            CLONE_MUTATION,
            variable_values={"listingId": "listing-1"},
            context_value=_anonymous_context(ds),
        )
        assert result.errors is not None
        assert "UNAUTHENTICATED" in str(result.errors[0].message)
