"""Testes do schema GraphQL para subscriptions (Fase A3).

Cobre mutations `subscribeToClipping`, `unsubscribeFromClipping`,
`updateMySubscription`. As mutations vivem em `ClippingMutation` (mesmo
namespace dos clippings) por D2 do plano (clipping-centric).
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import strawberry

from graphql_api.context import GraphQLContext, User
from graphql_api.datasources.firestore import SubscriptionData, UnauthorizedError
from graphql_api.schema.resolvers.clippings import ClippingMutation, ClippingQuery
from graphql_api.schema.resolvers.health import HealthQuery

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


@strawberry.type
class _Query(HealthQuery, ClippingQuery):
    pass


@strawberry.type
class _Mutation(ClippingMutation):
    pass


test_schema = strawberry.Schema(query=_Query, mutation=_Mutation)


def _sample_subscription(
    clipping_id: str = "clip-1",
    user_id: str = "user-123",
    role: str = "subscriber",
) -> SubscriptionData:
    return SubscriptionData(
        id=f"sub-{clipping_id}",
        clipping_id=clipping_id,
        user_id=user_id,
        role=role,  # type: ignore
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


def _make_mock_ds():
    ds = MagicMock()
    ds.subscribe_to_clipping.return_value = _sample_subscription()
    ds.unsubscribe_from_clipping.return_value = True
    ds.update_my_subscription.return_value = _sample_subscription(
        role="subscriber",
    )
    return ds


def _auth_ctx(ds, user_id="user-123"):
    ctx = GraphQLContext(firestore_ds=ds)
    ctx.user = User(id=user_id, email="test@example.com")
    return ctx


def _anon_ctx(ds):
    return GraphQLContext(firestore_ds=ds)


SUBSCRIBE_MUTATION = """
    mutation($input: SubscribeInput!) {
        subscribeToClipping(input: $input) {
            id
            clippingId
            userId
            role
            extraEmails
            webhookUrl
            active
            deliveryChannels {
                email
                telegram
                push
            }
        }
    }
"""

UNSUBSCRIBE_MUTATION = """
    mutation($clippingId: String!) {
        unsubscribeFromClipping(clippingId: $clippingId)
    }
"""

UPDATE_SUB_MUTATION = """
    mutation($clippingId: String!, $channels: DeliveryChannelsInput!, $extraEmails: [String!], $webhookUrl: String) {
        updateMySubscription(
            clippingId: $clippingId,
            channels: $channels,
            extraEmails: $extraEmails,
            webhookUrl: $webhookUrl
        ) {
            id
            role
            deliveryChannels { email telegram push }
            extraEmails
        }
    }
"""


class TestSubscribeToClipping:
    def test_subscribe_to_clipping_mutation_creates_sub(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            SUBSCRIBE_MUTATION,
            variable_values={
                "input": {
                    "clippingId": "clip-1",
                    "deliveryChannels": {
                        "email": True,
                        "telegram": False,
                        "push": False,
                    },
                    "extraEmails": [],
                    "webhookUrl": None,
                }
            },
            context_value=_auth_ctx(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        sub = result.data["subscribeToClipping"]
        assert sub["clippingId"] == "clip-1"
        assert sub["userId"] == "user-123"
        assert sub["role"] == "SUBSCRIBER"
        ds.subscribe_to_clipping.assert_called_once()

    def test_subscribe_requires_auth(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            SUBSCRIBE_MUTATION,
            variable_values={
                "input": {
                    "clippingId": "clip-1",
                    "deliveryChannels": {
                        "email": True,
                        "telegram": False,
                        "push": False,
                    },
                }
            },
            context_value=_anon_ctx(ds),
        )
        assert result.errors is not None
        assert "UNAUTHENTICATED" in str(result.errors[0].message)

    def test_subscribe_to_own_clipping_idempotent(self):
        """Autor já tem sub author; subscribeToClipping não duplica nem rebaixa.

        Datasource garante idempotência (preserva role do existing).
        """
        ds = _make_mock_ds()
        # Simula sub author preservada
        ds.subscribe_to_clipping.return_value = _sample_subscription(role="author")
        result = test_schema.execute_sync(
            SUBSCRIBE_MUTATION,
            variable_values={
                "input": {
                    "clippingId": "clip-1",
                    "deliveryChannels": {
                        "email": True,
                        "telegram": False,
                        "push": False,
                    },
                }
            },
            context_value=_auth_ctx(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        # Role permanece AUTHOR (não rebaixou)
        assert result.data["subscribeToClipping"]["role"] == "AUTHOR"


class TestUnsubscribeFromClipping:
    def test_unsubscribe_from_clipping_mutation(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            UNSUBSCRIBE_MUTATION,
            variable_values={"clippingId": "clip-1"},
            context_value=_auth_ctx(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["unsubscribeFromClipping"] is True
        ds.unsubscribe_from_clipping.assert_called_once_with("user-123", "clip-1")

    def test_unsubscribe_as_author_fails(self):
        """Autor não pode auto-unsubscribe — datasource levanta UnauthorizedError."""
        ds = _make_mock_ds()
        ds.unsubscribe_from_clipping.side_effect = UnauthorizedError(
            "author cannot unsubscribe from own clipping; use delete_clipping"
        )
        result = test_schema.execute_sync(
            UNSUBSCRIBE_MUTATION,
            variable_values={"clippingId": "clip-1"},
            context_value=_auth_ctx(ds),
        )
        assert result.errors is not None
        # A mensagem chega ao cliente — formato deixa para o resolver
        assert "unsubscribe" in str(result.errors[0].message).lower() or "FORBIDDEN" in str(
            result.errors[0].message
        )

    def test_unsubscribe_unauthenticated_fails(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            UNSUBSCRIBE_MUTATION,
            variable_values={"clippingId": "clip-1"},
            context_value=_anon_ctx(ds),
        )
        assert result.errors is not None
        assert "UNAUTHENTICATED" in str(result.errors[0].message)


class TestUpdateMySubscription:
    def test_update_my_subscription_changes_channels(self):
        ds = _make_mock_ds()
        # ds retorna sub com canais atualizados
        ds.update_my_subscription.return_value = SubscriptionData(
            id="sub-1",
            clipping_id="clip-1",
            user_id="user-123",
            role="subscriber",
            delivery_channels={
                "email": False,
                "telegram": True,
                "push": True,
                "webhook": False,
            },
            extra_emails=["x@y.com"],
            webhook_url="",
            active=True,
            subscribed_at=NOW,
        )
        result = test_schema.execute_sync(
            UPDATE_SUB_MUTATION,
            variable_values={
                "clippingId": "clip-1",
                "channels": {"email": False, "telegram": True, "push": True},
                "extraEmails": ["x@y.com"],
                "webhookUrl": None,
            },
            context_value=_auth_ctx(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        sub = result.data["updateMySubscription"]
        assert sub["deliveryChannels"]["telegram"] is True
        assert sub["deliveryChannels"]["push"] is True
        assert sub["extraEmails"] == ["x@y.com"]
        ds.update_my_subscription.assert_called_once()


# ---------------------------------------------------------------------------
# Fase A5 — Validacao webhook + webhookUrl
# ---------------------------------------------------------------------------
class TestSubscribeWebhookValidation:
    def test_subscription_with_webhook_true_requires_webhook_url(self):
        """`channels.webhook=True` sem `webhookUrl` -> validation error."""
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            SUBSCRIBE_MUTATION,
            variable_values={
                "input": {
                    "clippingId": "clip-1",
                    "deliveryChannels": {
                        "email": False,
                        "telegram": False,
                        "push": False,
                        "webhook": True,
                    },
                    "webhookUrl": None,
                }
            },
            context_value=_auth_ctx(ds),
        )
        assert result.errors is not None
        assert "webhookurl" in str(result.errors[0].message).lower()
        # Datasource nao deve ter sido chamado
        ds.subscribe_to_clipping.assert_not_called()

    def test_subscription_with_webhook_url_validates_https(self):
        """`webhookUrl=http://...` deve ser rejeitado (so https)."""
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            SUBSCRIBE_MUTATION,
            variable_values={
                "input": {
                    "clippingId": "clip-1",
                    "deliveryChannels": {
                        "email": False,
                        "telegram": False,
                        "push": False,
                        "webhook": True,
                    },
                    "webhookUrl": "http://insecure.example.com/hook",
                }
            },
            context_value=_auth_ctx(ds),
        )
        assert result.errors is not None
        assert "https" in str(result.errors[0].message).lower()

    def test_subscription_with_webhook_url_max_length(self):
        """`webhookUrl` com mais de 2048 chars deve ser rejeitado."""
        ds = _make_mock_ds()
        long_url = "https://example.com/" + ("x" * 2100)
        result = test_schema.execute_sync(
            SUBSCRIBE_MUTATION,
            variable_values={
                "input": {
                    "clippingId": "clip-1",
                    "deliveryChannels": {
                        "email": False,
                        "telegram": False,
                        "push": False,
                        "webhook": True,
                    },
                    "webhookUrl": long_url,
                }
            },
            context_value=_auth_ctx(ds),
        )
        assert result.errors is not None
        assert "2048" in str(result.errors[0].message) or "limite" in str(result.errors[0].message).lower()

    def test_subscription_with_valid_https_webhook_succeeds(self):
        ds = _make_mock_ds()
        # Mock retorna sub com webhook habilitado
        ds.subscribe_to_clipping.return_value = SubscriptionData(
            id="sub-1",
            clipping_id="clip-1",
            user_id="user-123",
            role="subscriber",
            delivery_channels={
                "email": False,
                "telegram": False,
                "push": False,
                "webhook": True,
            },
            extra_emails=[],
            webhook_url="https://example.com/hook",
            active=True,
            subscribed_at=NOW,
        )
        result = test_schema.execute_sync(
            SUBSCRIBE_MUTATION,
            variable_values={
                "input": {
                    "clippingId": "clip-1",
                    "deliveryChannels": {
                        "email": False,
                        "telegram": False,
                        "push": False,
                        "webhook": True,
                    },
                    "webhookUrl": "https://example.com/hook",
                }
            },
            context_value=_auth_ctx(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        sub = result.data["subscribeToClipping"]
        assert sub["webhookUrl"] == "https://example.com/hook"


class TestUpdateMySubscriptionWebhook:
    def test_update_my_subscription_with_webhook_persists_webhook_url(self):
        """Caminho de update via `updateMySubscription`: webhook=True + url valida."""
        ds = _make_mock_ds()
        ds.update_my_subscription.return_value = SubscriptionData(
            id="sub-1",
            clipping_id="clip-1",
            user_id="user-123",
            role="subscriber",
            delivery_channels={
                "email": False,
                "telegram": False,
                "push": False,
                "webhook": True,
            },
            extra_emails=[],
            webhook_url="https://example.com/notify",
            active=True,
            subscribed_at=NOW,
        )
        result = test_schema.execute_sync(
            """
            mutation($clippingId: String!, $channels: DeliveryChannelsInput!, $webhookUrl: String) {
                updateMySubscription(
                    clippingId: $clippingId,
                    channels: $channels,
                    webhookUrl: $webhookUrl
                ) {
                    id
                    deliveryChannels { email telegram push webhook }
                    webhookUrl
                }
            }
            """,
            variable_values={
                "clippingId": "clip-1",
                "channels": {
                    "email": False,
                    "telegram": False,
                    "push": False,
                    "webhook": True,
                },
                "webhookUrl": "https://example.com/notify",
            },
            context_value=_auth_ctx(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        sub = result.data["updateMySubscription"]
        assert sub["deliveryChannels"]["webhook"] is True
        assert sub["webhookUrl"] == "https://example.com/notify"
        # Confirma que o datasource recebeu webhook=True + url
        call_args = ds.update_my_subscription.call_args
        kwargs = call_args.kwargs
        assert kwargs["channels"]["webhook"] is True
        assert kwargs["webhook_url"] == "https://example.com/notify"

    def test_update_my_subscription_with_webhook_without_url_fails(self):
        ds = _make_mock_ds()
        result = test_schema.execute_sync(
            """
            mutation($clippingId: String!, $channels: DeliveryChannelsInput!, $webhookUrl: String) {
                updateMySubscription(
                    clippingId: $clippingId,
                    channels: $channels,
                    webhookUrl: $webhookUrl
                ) { id }
            }
            """,
            variable_values={
                "clippingId": "clip-1",
                "channels": {
                    "email": False,
                    "telegram": False,
                    "push": False,
                    "webhook": True,
                },
                "webhookUrl": None,
            },
            context_value=_auth_ctx(ds),
        )
        assert result.errors is not None
        assert "webhookurl" in str(result.errors[0].message).lower()
        ds.update_my_subscription.assert_not_called()


class TestSubscriptionRoleEnum:
    def test_subscription_role_enum_serializes_to_uppercase(self):
        """`AUTHOR` e `SUBSCRIBER` no schema introspection."""
        introspection = test_schema.execute_sync(
            """{
                __type(name: "SubscriptionRole") {
                    name
                    enumValues { name }
                }
            }"""
        )
        assert introspection.errors is None, f"Errors: {introspection.errors}"
        enum_type = introspection.data["__type"]
        assert enum_type["name"] == "SubscriptionRole"
        values = {v["name"] for v in enum_type["enumValues"]}
        assert values == {"AUTHOR", "SUBSCRIBER"}

    def test_user_subscription_type_in_schema(self):
        introspection = test_schema.execute_sync(
            """{
                __type(name: "UserSubscription") {
                    name
                    fields { name }
                }
            }"""
        )
        assert introspection.errors is None
        t = introspection.data["__type"]
        assert t is not None, "UserSubscription type missing from schema"
        field_names = {f["name"] for f in t["fields"]}
        assert {
            "id",
            "clippingId",
            "userId",
            "role",
            "deliveryChannels",
            "extraEmails",
            "webhookUrl",
            "active",
            "subscribedAt",
        }.issubset(field_names)
