from datetime import datetime, timezone
from unittest.mock import MagicMock

import strawberry

from graphql_api.context import GraphQLContext, User
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.marketplace import MarketplaceQuery


@strawberry.type
class _Query(HealthQuery, MarketplaceQuery):
    pass


test_schema = strawberry.Schema(query=_Query)

NOW = datetime(2024, 8, 1, 12, 0, 0, tzinfo=timezone.utc)

SAMPLE_LISTING = {
    "id": "listing-1",
    "author_user_id": "author-1",
    "author_display_name": "Alice",
    "source_clipping_id": "clip-src-1",
    "name": "Top Economia",
    "description": "Melhores recortes de economia",
    "recortes": [
        {
            "id": "r1",
            "title": "Economia",
            "themes": ["economia"],
            "agencies": ["agencia-brasil"],
            "keywords": ["pib"],
        }
    ],
    "prompt": "Resuma",
    "like_count": 5,
    "follower_count": 3,
    "clone_count": 1,
    "published_at": NOW,
    "updated_at": NOW,
    "active": True,
}


def _make_mock_ds(listings=None):
    ds = MagicMock()
    if listings is None:
        listings = [SAMPLE_LISTING]
    ds.get_marketplace_listings.return_value = {
        "listings": listings,
        "total": len(listings),
    }
    ds.get_marketplace_listing.return_value = SAMPLE_LISTING
    ds.has_liked_listing.return_value = True
    ds.has_followed_listing.return_value = False
    return ds


def _authenticated_context(ds):
    ctx = GraphQLContext(firestore_ds=ds)
    ctx.user = User(id="user-123", email="test@example.com")
    return ctx


def _anonymous_context(ds):
    return GraphQLContext(firestore_ds=ds)


LISTINGS_QUERY = """
    query($page: Int!) {
        marketplaceListings(page: $page) {
            listings {
                id
                name
                description
                authorDisplayName
                likeCount
                followerCount
                cloneCount
                hasLiked
                hasFollowed
                recortes {
                    id
                    title
                    themes
                }
            }
            total
        }
    }
"""

DETAIL_QUERY = """
    query($id: String!) {
        marketplaceListing(id: $id) {
            id
            name
            authorUserId
            authorDisplayName
            sourceClippingId
            description
            prompt
            likeCount
            hasLiked
            hasFollowed
        }
    }
"""


class TestMarketplaceListings:
    def test_marketplace_listings_returns_paginated(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            LISTINGS_QUERY,
            variable_values={"page": 1},
            context_value=_anonymous_context(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        data = result.data["marketplaceListings"]
        assert data["total"] == 1
        assert len(data["listings"]) == 1
        listing = data["listings"][0]
        assert listing["id"] == "listing-1"
        assert listing["name"] == "Top Economia"
        assert listing["likeCount"] == 5
        assert listing["followerCount"] == 3
        ds.get_marketplace_listings.assert_called_once_with(offset=0, limit=20)

    def test_marketplace_listing_detail(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            DETAIL_QUERY,
            variable_values={"id": "listing-1"},
            context_value=_anonymous_context(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        data = result.data["marketplaceListing"]
        assert data["id"] == "listing-1"
        assert data["name"] == "Top Economia"
        assert data["authorUserId"] == "author-1"
        assert data["sourceClippingId"] == "clip-src-1"
        assert data["prompt"] == "Resuma"
        ds.get_marketplace_listing.assert_called_once_with("listing-1")

    def test_listing_personalization_authenticated(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            DETAIL_QUERY,
            variable_values={"id": "listing-1"},
            context_value=_authenticated_context(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        data = result.data["marketplaceListing"]
        assert data["hasLiked"] is True
        assert data["hasFollowed"] is False
        ds.has_liked_listing.assert_called_once_with("user-123", "listing-1")
        ds.has_followed_listing.assert_called_once_with("user-123", "listing-1")

    def test_listing_no_personalization_anonymous(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            DETAIL_QUERY,
            variable_values={"id": "listing-1"},
            context_value=_anonymous_context(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        data = result.data["marketplaceListing"]
        assert data["hasLiked"] is None
        assert data["hasFollowed"] is None
        ds.has_liked_listing.assert_not_called()
        ds.has_followed_listing.assert_not_called()
