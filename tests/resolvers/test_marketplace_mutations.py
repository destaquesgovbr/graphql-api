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
        cloneMarketplaceListing(listingId: $listingId)
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


class TestCloneMarketplaceListing:
    def test_clone_creates_copy(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            CLONE_MUTATION,
            variable_values={"listingId": "listing-1"},
            context_value=_authenticated_context(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["cloneMarketplaceListing"] is True
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
