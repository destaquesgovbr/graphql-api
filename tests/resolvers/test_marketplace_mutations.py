from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import strawberry

from graphql_api.context import GraphQLContext, User
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

SAMPLE_LISTING = {
    "id": "listing-1",
    "author_user_id": "author-1",
    "author_display_name": "Alice",
    "source_clipping_id": "clip-src-1",
    "name": "Top Economia",
    "description": "Melhores recortes de economia",
    "recortes": [],
    "prompt": None,
    "like_count": 5,
    "follower_count": 3,
    "clone_count": 1,
    "published_at": NOW,
    "updated_at": NOW,
    "active": True,
}


def _make_mock_ds():
    ds = MagicMock()
    ds.publish_to_marketplace.return_value = {
        **SAMPLE_LISTING,
        "id": "listing-new",
        "author_user_id": "user-123",
        "author_display_name": "Test User",
        "source_clipping_id": "clip-42",
        "name": "Meu Listing",
        "description": "Descricao",
    }
    ds.get_marketplace_listing.return_value = SAMPLE_LISTING
    ds.unpublish_from_marketplace.return_value = True
    ds.toggle_like_marketplace.return_value = True
    ds.toggle_follow_marketplace.return_value = True
    ds.clone_marketplace_listing.return_value = True
    ds.has_liked_listing.return_value = False
    ds.has_followed_listing.return_value = False
    ds.get_marketplace_listings.return_value = {"listings": [], "total": 0}
    return ds


def _authenticated_context(ds, user_id="user-123", email="test@example.com"):
    ctx = GraphQLContext(firestore_ds=ds)
    ctx.user = User(id=user_id, email=email)
    return ctx


def _anonymous_context(ds):
    return GraphQLContext(firestore_ds=ds)


PUBLISH_MUTATION = """
    mutation($clippingId: String!, $input: PublishInput!) {
        publishToMarketplace(clippingId: $clippingId, input: $input) {
            id
            name
            description
            authorUserId
        }
    }
"""

UNPUBLISH_MUTATION = """
    mutation($listingId: String!) {
        unpublishFromMarketplace(listingId: $listingId)
    }
"""

LIKE_MUTATION = """
    mutation($listingId: String!) {
        likeMarketplaceListing(listingId: $listingId)
    }
"""

CLONE_MUTATION = """
    mutation($listingId: String!) {
        cloneMarketplaceListing(listingId: $listingId) {
            id
            name
        }
    }
"""


class TestPublishToMarketplace:
    def test_publish_to_marketplace(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            PUBLISH_MUTATION,
            variable_values={
                "clippingId": "clip-42",
                "input": {"name": "Meu Listing", "description": "Descricao"},
            },
            context_value=_authenticated_context(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        data = result.data["publishToMarketplace"]
        assert data["id"] == "listing-new"
        assert data["name"] == "Meu Listing"
        assert data["authorUserId"] == "user-123"
        ds.publish_to_marketplace.assert_called_once_with(
            user_id="user-123",
            clipping_id="clip-42",
            name="Meu Listing",
            description="Descricao",
        )

    def test_publish_unauthenticated_fails(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            PUBLISH_MUTATION,
            variable_values={
                "clippingId": "clip-42",
                "input": {"name": "Meu Listing"},
            },
            context_value=_anonymous_context(ds),
        )
        assert result.errors is not None
        assert "UNAUTHENTICATED" in str(result.errors[0].message)


class TestUnpublishFromMarketplace:
    def test_unpublish_only_owner(self):
        """Non-owner gets FORBIDDEN error when trying to unpublish."""
        ds = _make_mock_ds()
        # listing author is "author-1", but user is "other-user"
        result = test_schema.execute_sync(
            UNPUBLISH_MUTATION,
            variable_values={"listingId": "listing-1"},
            context_value=_authenticated_context(ds, user_id="other-user"),
        )
        assert result.errors is not None
        assert "FORBIDDEN" in str(result.errors[0].message)

    def test_unpublish_owner_succeeds(self):
        ds = _make_mock_ds()
        # listing author is "author-1", authenticate as "author-1"
        result = test_schema.execute_sync(
            UNPUBLISH_MUTATION,
            variable_values={"listingId": "listing-1"},
            context_value=_authenticated_context(ds, user_id="author-1"),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["unpublishFromMarketplace"] is True
        ds.unpublish_from_marketplace.assert_called_once_with("listing-1")


class TestLikeToggle:
    def test_like_toggles(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            LIKE_MUTATION,
            variable_values={"listingId": "listing-1"},
            context_value=_authenticated_context(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["likeMarketplaceListing"] is True
        ds.toggle_like_marketplace.assert_called_once_with("user-123", "listing-1")

    def test_like_unauthenticated_fails(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            LIKE_MUTATION,
            variable_values={"listingId": "listing-1"},
            context_value=_anonymous_context(ds),
        )
        assert result.errors is not None
        assert "UNAUTHENTICATED" in str(result.errors[0].message)


class TestFollowMarketplaceListingDeprecation:
    """Fase A3: `followMarketplaceListing` deprecada em favor de
    `subscribeToClipping`. Mantém-se funcional para compat com clientes legados.
    """

    def test_follow_marketplace_listing_marked_deprecated(self):
        """Schema introspection deve marcar a mutation como deprecated."""
        from graphql_api.schema import schema as full_schema

        result = full_schema.execute_sync(
            """{
                __type(name: "Mutation") {
                    fields(includeDeprecated: true) {
                        name
                        isDeprecated
                        deprecationReason
                    }
                }
            }"""
        )
        assert result.errors is None
        fields = result.data["__type"]["fields"]
        follow = next(
            (f for f in fields if f["name"] == "followMarketplaceListing"), None
        )
        assert follow is not None, "followMarketplaceListing missing from schema"
        assert follow["isDeprecated"] is True
        assert follow["deprecationReason"] is not None
        assert "subscribeToClipping" in follow["deprecationReason"]

    def test_follow_marketplace_listing_still_works(self):
        """Backward compat: mutation antiga continua funcional internamente.

        Após A3, `followMarketplaceListing` delega para `subscribe_to_clipping`
        usando o `sourceClippingId` do listing — mas a interface pública (bool
        return, listingId arg) é mantida.
        """
        ds = _make_mock_ds()
        # Para o delegado: lookup do listing → sourceClippingId; subscribe ds call.
        ds.get_marketplace_listing.return_value = {**SAMPLE_LISTING, "sourceClippingId": "clip-src-1"}
        # subscribe_to_clipping retorna uma SubscriptionData mocka
        from graphql_api.datasources.firestore import SubscriptionData

        ds.subscribe_to_clipping.return_value = SubscriptionData(
            id="sub-1",
            clipping_id="clip-src-1",
            user_id="user-123",
            role="subscriber",
            delivery_channels={
                "email": True,
                "telegram": False,
                "push": False,
                "webhook": False,
            },
            extra_emails=[],
            webhook_url="",
            active=True,
            subscribed_at=NOW,
        )

        result = test_schema.execute_sync(
            """
            mutation($listingId: String!) {
                followMarketplaceListing(listingId: $listingId)
            }
            """,
            variable_values={"listingId": "listing-1"},
            context_value=_authenticated_context(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["followMarketplaceListing"] is True
        # Verifica que delegou para subscribe_to_clipping
        ds.subscribe_to_clipping.assert_called_once()


class TestCloneMarketplaceListing:
    def test_clone_creates_copy(self):
        # Gap-fix pre-rollout: cloneMarketplaceListing agora retorna Clipping!
        # (antes Boolean!). O datasource retorna ClippingData; mock atualizado.
        from graphql_api.datasources.firestore import ClippingData

        ds = _make_mock_ds()
        ds.clone_marketplace_listing.return_value = ClippingData.model_validate(
            {
                "id": "clip-novo-1",
                "name": "Clone Top Economia",
                "description": "Clone",
                "recortes": [],
                "active": True,
                "author_user_id": "user-123",
                "schedule": "0 8 * * *",
                "next_run_at": NOW,
                "created_at": NOW,
                "updated_at": NOW,
            }
        )
        result = test_schema.execute_sync(
            CLONE_MUTATION,
            variable_values={"listingId": "listing-1"},
            context_value=_authenticated_context(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["cloneMarketplaceListing"]["id"] == "clip-novo-1"
        ds.clone_marketplace_listing.assert_called_once_with("user-123", "listing-1")

    def test_clone_unauthenticated_fails(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            CLONE_MUTATION,
            variable_values={"listingId": "listing-1"},
            context_value=_anonymous_context(ds),
        )
        assert result.errors is not None
        assert "UNAUTHENTICATED" in str(result.errors[0].message)
