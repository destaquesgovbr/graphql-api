"""Testes do resolver `myFollowedListings` (Fase 1A SSR).

Substitui o `getFollows` do portal. Junta as subscriptions do user
(`role=subscriber` AND `active=true`) contra os listings do marketplace
(`active=true`) por `sourceClippingId`.

- follow cujo listing esta ativo -> presente com os campos da subscription
- follow cujo listing esta inativo/ausente -> excluido
- sem follows -> lista vazia
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import strawberry

from graphql_api.context import GraphQLContext, User
from graphql_api.datasources.firestore import (
    FollowedListingResult,
    MarketplaceListingData,
    SubscriptionData,
)
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.marketplace import MarketplaceQuery

NOW = datetime(2024, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


@strawberry.type
class _Query(HealthQuery, MarketplaceQuery):
    pass


test_schema = strawberry.Schema(query=_Query)


def _listing(listing_id: str, source_clipping_id: str, active: bool = True) -> dict:
    return {
        "id": listing_id,
        "authorUserId": "author-1",
        "authorDisplayName": "Alice",
        "sourceClippingId": source_clipping_id,
        "name": f"Listing {listing_id}",
        "description": "desc",
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
        "likeCount": 5,
        "followerCount": 3,
        "cloneCount": 1,
        "publishedAt": NOW,
        "updatedAt": NOW,
        "active": active,
    }


def _subscription(clipping_id: str, user_id: str = "user-123") -> SubscriptionData:
    return SubscriptionData(
        id=f"sub-{clipping_id}",
        clipping_id=clipping_id,
        user_id=user_id,
        role="subscriber",
        delivery_channels={
            "email": True,
            "telegram": True,
            "push": False,
            "webhook": False,
        },
        extra_emails=["extra@example.com"],
        webhook_url="https://hook.example.com/x",
        active=True,
        subscribed_at=NOW,
    )


def _result(listing_dict: dict, sub: SubscriptionData) -> FollowedListingResult:
    return FollowedListingResult(
        listing=MarketplaceListingData.model_validate(listing_dict),
        subscription=sub,
    )


def _authenticated_context(ds, user_id="user-123") -> GraphQLContext:
    ctx = GraphQLContext(firestore_ds=ds)
    ctx.user = User(id=user_id, email="test@example.com")
    return ctx


QUERY = """
    {
        myFollowedListings {
            id
            name
            sourceClippingId
            active
            deliveryChannels { email telegram push webhook }
            extraEmails
            webhookUrl
            followedAt
        }
    }
"""


class TestMyFollowedListings:
    def test_returns_active_followed_listings_with_joined_fields(self):
        ds = MagicMock()
        ds.get_followed_listings.return_value = [
            _result(_listing("listing-1", "clip-1"), _subscription("clip-1")),
        ]
        result = test_schema.execute_sync(
            QUERY, context_value=_authenticated_context(ds)
        )
        assert result.errors is None, f"Errors: {result.errors}"
        items = result.data["myFollowedListings"]
        assert len(items) == 1
        item = items[0]
        assert item["id"] == "listing-1"
        assert item["sourceClippingId"] == "clip-1"
        assert item["active"] is True
        # Campos juntados da subscription
        assert item["deliveryChannels"]["email"] is True
        assert item["deliveryChannels"]["telegram"] is True
        assert item["extraEmails"] == ["extra@example.com"]
        assert item["webhookUrl"] == "https://hook.example.com/x"
        assert item["followedAt"] is not None
        ds.get_followed_listings.assert_called_once_with("user-123")

    def test_empty_when_no_follows(self):
        ds = MagicMock()
        ds.get_followed_listings.return_value = []
        result = test_schema.execute_sync(
            QUERY, context_value=_authenticated_context(ds)
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["myFollowedListings"] == []

    def test_datasource_excludes_inactive_or_missing_listings(self):
        """O datasource e responsavel por filtrar listings inativos/ausentes.

        Aqui validamos a logica do datasource diretamente (mock do `db`):
        uma sub aponta para listing ativo (incluido) e outra para listing
        inativo (excluido); uma terceira sub aponta para clipping sem listing
        (excluida).
        """
        from graphql_api.datasources.firestore import FirestoreDatasource

        db = MagicMock()
        ds = FirestoreDatasource(db=db)

        subs = [
            _subscription("clip-active"),
            _subscription("clip-inactive"),
            _subscription("clip-no-listing"),
        ]

        # subscriptions where userId == X AND role == subscriber AND active == True
        subs_query = MagicMock()
        subs_docs = []
        for s in subs:
            d = MagicMock()
            d.id = s.id
            d.to_dict.return_value = {
                "clippingId": s.clipping_id,
                "userId": s.user_id,
                "role": "subscriber",
                "deliveryChannels": s.delivery_channels,
                "extraEmails": s.extra_emails,
                "webhookUrl": s.webhook_url,
                "active": True,
                "subscribedAt": s.subscribed_at,
            }
            subs_docs.append(d)
        subs_query.stream.return_value = iter(subs_docs)

        # marketplace where sourceClippingId == clip AND active == True
        def marketplace_query_for(clip_id):
            q = MagicMock()
            if clip_id == "clip-active":
                ldoc = MagicMock()
                ldoc.id = "listing-active"
                ldoc.to_dict.return_value = _listing("listing-active", "clip-active")
                q.stream.return_value = iter([ldoc])
            else:
                # inactive (filtrado pelo where active==True) ou sem listing
                q.stream.return_value = iter([])
            return q

        subscriptions_col = MagicMock()
        marketplace_col = MagicMock()

        def collection(name):
            if name == "subscriptions":
                return subscriptions_col
            if name == "marketplace":
                return marketplace_col
            return MagicMock()

        db.collection.side_effect = collection

        # subscriptions chain: .where().where().where() -> subs_query
        subscriptions_col.where.return_value.where.return_value.where.return_value = (
            subs_query
        )

        # marketplace chain: .where(sourceClippingId).where(active) -> per clip
        clip_holder = {}

        def mp_where(field, op, value):
            if field == "sourceClippingId":
                clip_holder["clip"] = value
                inner = MagicMock()
                inner.where.return_value = marketplace_query_for(value)
                return inner
            return MagicMock()

        marketplace_col.where.side_effect = mp_where

        results = ds.get_followed_listings("user-123")
        clip_ids = {r.subscription.clipping_id for r in results}
        assert clip_ids == {"clip-active"}
        assert results[0].listing.id == "listing-active"
