from unittest.mock import MagicMock

import strawberry

from graphql_api.context import GraphQLContext, User
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.push import PushMutation


@strawberry.type
class _Query(HealthQuery):
    pass


@strawberry.type
class _Mutation(PushMutation):
    pass


test_schema = strawberry.Schema(query=_Query, mutation=_Mutation)


def _make_mock_firestore_ds():
    ds = MagicMock()
    # Mock the Firestore chain: db.collection().document().collection().document().set()
    ds._db = MagicMock()
    return ds


def _make_authenticated_context(firestore_ds):
    ctx = GraphQLContext(firestore_ds=firestore_ds)
    ctx.user = User(id="user-123", email="test@example.com")
    return ctx


def _make_unauthenticated_context(firestore_ds):
    ctx = GraphQLContext(firestore_ds=firestore_ds)
    return ctx


class TestSyncPushSubscription:
    def test_sync_push_unauthenticated_error(self):
        mock_ds = _make_mock_firestore_ds()
        ctx = _make_unauthenticated_context(mock_ds)

        result = test_schema.execute_sync(
            """
            mutation($sub: PushSubscriptionInput!) {
                syncPushSubscription(subscription: $sub)
            }
            """,
            variable_values={
                "sub": {
                    "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                    "keysP256dh": "BNcRd...",
                    "keysAuth": "tBH...",
                },
            },
            context_value=ctx,
        )

        assert result.errors is not None
        assert len(result.errors) > 0
        assert "UNAUTHENTICATED" in str(result.errors[0].message)

    def test_sync_push_authenticated_succeeds(self):
        mock_ds = _make_mock_firestore_ds()
        ctx = _make_authenticated_context(mock_ds)

        result = test_schema.execute_sync(
            """
            mutation($sub: PushSubscriptionInput!) {
                syncPushSubscription(subscription: $sub)
            }
            """,
            variable_values={
                "sub": {
                    "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                    "keysP256dh": "BNcRd...",
                    "keysAuth": "tBH...",
                },
            },
            context_value=ctx,
        )

        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["syncPushSubscription"] is True

        # Verify Firestore was called to save the subscription
        mock_db = mock_ds._db
        mock_db.collection.assert_called_with("users")


class TestUpdatePushPreferences:
    def test_update_push_preferences(self):
        mock_ds = _make_mock_firestore_ds()
        ctx = _make_authenticated_context(mock_ds)

        result = test_schema.execute_sync(
            """
            mutation($prefs: PushPreferencesInput!) {
                updatePushPreferences(preferences: $prefs)
            }
            """,
            variable_values={
                "prefs": {
                    "agencies": ["agencia-brasil", "gov-br"],
                    "themes": ["economia", "saude"],
                    "enabled": True,
                },
            },
            context_value=ctx,
        )

        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["updatePushPreferences"] is True

        # Verify Firestore was called
        mock_db = mock_ds._db
        mock_db.collection.assert_called_with("users")
