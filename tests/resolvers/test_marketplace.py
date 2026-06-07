from datetime import datetime, timezone
from unittest.mock import MagicMock

import strawberry

from graphql_api.context import GraphQLContext, User
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.marketplace import MarketplaceQuery, _doc_to_listing


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


RELEASES_QUERY = """
    query($id: String!, $limit: Int, $before: DateTime) {
        marketplaceListing(id: $id) {
            id
            active
            releases(limit: $limit, before: $before) {
                id
                clippingId
                clippingName
                digestHtml
                articlesCount
                releaseUrl
                refTime
                sinceHours
                createdAt
            }
        }
    }
"""


def _release_doc(rid: str, created: datetime):
    """Constroi um ReleaseData (camelCase, como vem do Firestore)."""
    from graphql_api.datasources.firestore import ReleaseData

    return ReleaseData.model_validate(
        {
            "id": rid,
            "clippingId": "clip-src-1",
            "clippingName": "Top Economia",
            "digestHtml": f"<p>{rid}</p>",
            "articlesCount": 7,
            "createdAt": created,
            "releaseUrl": f"/clipping/release/{rid}",
            "refTime": created,
            "sinceHours": 24,
        }
    )


class TestMarketplaceListingReleases:
    """Releases PUBLICAS de um listing ativo (sem auth)."""

    def test_active_listing_returns_releases_without_auth(self):
        ds = _make_mock_ds()
        ds.get_releases.return_value = [
            _release_doc("rel-1", NOW),
            _release_doc("rel-2", datetime(2024, 7, 30, 12, 0, tzinfo=timezone.utc)),
        ]
        # Contexto ANONIMO — releases publicas nao exigem login.
        result = test_schema.execute_sync(
            RELEASES_QUERY,
            variable_values={"id": "listing-1", "limit": 10, "before": None},
            context_value=_anonymous_context(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        listing = result.data["marketplaceListing"]
        assert listing["active"] is True
        releases = listing["releases"]
        assert len(releases) == 2
        assert releases[0]["id"] == "rel-1"
        assert releases[0]["articlesCount"] == 7
        assert releases[0]["digestHtml"] == "<p>rel-1</p>"
        # Le do sourceClippingId do listing, com o limit clampado.
        ds.get_releases.assert_called_once_with(
            "clip-src-1", limit=10, before=None
        )

    def test_inactive_listing_never_leaks_releases(self):
        # Listing inativo/despublicado: o `marketplaceListing(id)` ja filtra
        # inativos (retorna None em producao), mas mesmo que o tipo seja
        # construido com active=False, o resolver NUNCA chama get_releases e
        # retorna lista vazia.
        ds = _make_mock_ds()
        ds.get_marketplace_listing.return_value = {
            **SAMPLE_LISTING,
            "active": False,
        }
        ds.get_releases.return_value = [_release_doc("rel-1", NOW)]
        result = test_schema.execute_sync(
            RELEASES_QUERY,
            variable_values={"id": "listing-1", "limit": 10, "before": None},
            context_value=_anonymous_context(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        listing = result.data["marketplaceListing"]
        assert listing["active"] is False
        assert listing["releases"] == []
        # Defesa em profundidade: get_releases NUNCA e tocado p/ listing inativo.
        ds.get_releases.assert_not_called()

    def test_nonexistent_listing_returns_null(self):
        ds = _make_mock_ds()
        ds.get_marketplace_listing.return_value = None
        ds.get_releases.return_value = [_release_doc("rel-1", NOW)]
        result = test_schema.execute_sync(
            RELEASES_QUERY,
            variable_values={"id": "missing", "limit": 10, "before": None},
            context_value=_anonymous_context(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["marketplaceListing"] is None
        ds.get_releases.assert_not_called()

    def test_before_cursor_is_forwarded(self):
        ds = _make_mock_ds()
        ds.get_releases.return_value = []
        cursor = "2024-07-31T00:00:00+00:00"
        result = test_schema.execute_sync(
            RELEASES_QUERY,
            variable_values={"id": "listing-1", "limit": 5, "before": cursor},
            context_value=_anonymous_context(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["marketplaceListing"]["releases"] == []
        # `before` chega como datetime ao datasource (coerce do scalar DateTime).
        ds.get_releases.assert_called_once()
        call = ds.get_releases.call_args
        assert call.args[0] == "clip-src-1"
        assert call.kwargs["limit"] == 5
        assert isinstance(call.kwargs["before"], datetime)

    def test_limit_is_clamped_to_max_100(self):
        ds = _make_mock_ds()
        ds.get_releases.return_value = []
        result = test_schema.execute_sync(
            RELEASES_QUERY,
            variable_values={"id": "listing-1", "limit": 9999, "before": None},
            context_value=_anonymous_context(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        ds.get_releases.assert_called_once_with(
            "clip-src-1", limit=100, before=None
        )


class TestListingScheduleExposure:
    def test_doc_to_listing_exposes_schedule(self):
        # R1-05: o `schedule` é gravado no listing pelo publish, mas não era
        # exposto no tipo GraphQL — sumia no path GraphQL com a flag ON.
        listing = _doc_to_listing(
            {**SAMPLE_LISTING, "schedule": "0 8 * * *"}
        )
        assert listing.schedule == "0 8 * * *"

    def test_doc_to_listing_schedule_defaults_none(self):
        listing = _doc_to_listing({**SAMPLE_LISTING})
        assert listing.schedule is None
