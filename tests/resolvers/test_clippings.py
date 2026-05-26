from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import strawberry
from strawberry.test import BaseGraphQLTestClient

from graphql_api.context import GraphQLContext, User
from graphql_api.dataloaders import create_subscription_loader
from graphql_api.datasources.firestore import (
    ClippingData,
    MyClippingResult,
    SubscriptionData,
)
from graphql_api.schema.resolvers.clippings import ClippingMutation, ClippingQuery
from graphql_api.schema.resolvers.health import HealthQuery


@strawberry.type
class _Query(HealthQuery, ClippingQuery):
    pass


@strawberry.type
class _Mutation(ClippingMutation):
    pass


test_schema = strawberry.Schema(query=_Query, mutation=_Mutation)


def _sample_clipping_data(**overrides) -> ClippingData:
    now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    defaults = dict(
        id="clip-1",
        name="Meu Clipping",
        description="Clipping de teste",
        recortes=[
            {
                "id": "r1",
                "title": "Economia",
                "themes": ["economia"],
                "agencies": ["agencia-brasil"],
                "keywords": ["pib"],
            }
        ],
        prompt="Resuma as noticias",
        schedule_time="08:00",
        delivery_channels={"email": True, "telegram": False, "push": False},
        active=True,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return ClippingData(**defaults)


def _sample_subscription(clipping_id: str = "clip-1", role: str = "author") -> SubscriptionData:
    now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    return SubscriptionData(
        id=f"sub-{clipping_id}",
        clipping_id=clipping_id,
        user_id="user-123",
        role=role,
        delivery_channels={"email": True, "telegram": False, "push": False, "webhook": False},
        extra_emails=[],
        webhook_url="",
        active=True,
        subscribed_at=now,
    )


def _make_mock_firestore_ds():
    ds = MagicMock()
    ds.get_my_clippings.return_value = [
        MyClippingResult(clipping=_sample_clipping_data(), subscription=_sample_subscription())
    ]
    ds.get_clipping.return_value = _sample_clipping_data()
    ds.create_clipping.return_value = _sample_clipping_data(id="clip-new")
    ds.update_clipping.return_value = _sample_clipping_data(name="Updated")
    ds.delete_clipping.return_value = True
    return ds


def _make_authenticated_context(firestore_ds):
    ctx = GraphQLContext(firestore_ds=firestore_ds)
    ctx.user = User(id="user-123", email="test@example.com")
    return ctx


def _make_unauthenticated_context(firestore_ds):
    ctx = GraphQLContext(firestore_ds=firestore_ds)
    return ctx


class StrawberryTestClient(BaseGraphQLTestClient):
    def request(self, body, headers=None, files=None):
        import json
        resp = self._client.post(  # type: ignore
            "/graphql",
            json=body if isinstance(body, dict) else json.loads(body),
        )
        return resp.json()


@pytest.fixture
def mock_firestore_ds():
    return _make_mock_firestore_ds()


class TestClippingsUnauthenticated:
    def test_clippings_unauthenticated_returns_error(self, mock_firestore_ds):
        result = test_schema.execute_sync(
            "{ clippings { id name } }",
            context_value=_make_unauthenticated_context(mock_firestore_ds),
        )
        assert result.errors is not None
        assert len(result.errors) > 0
        assert "UNAUTHENTICATED" in str(result.errors[0].message)


class TestClippingsAuthenticated:
    def test_clippings_authenticated_returns_list(self, mock_firestore_ds):
        result = test_schema.execute_sync(
            """
            {
                clippings {
                    id
                    name
                    description
                    prompt
                    scheduleTime
                    active
                    recortes {
                        id
                        title
                        themes
                        agencies
                        keywords
                    }
                    deliveryChannels {
                        email
                        telegram
                        push
                    }
                }
            }
            """,
            context_value=_make_authenticated_context(mock_firestore_ds),
        )
        assert result.errors is None
        clippings = result.data["clippings"]
        assert len(clippings) == 1
        clip = clippings[0]
        assert clip["id"] == "clip-1"
        assert clip["name"] == "Meu Clipping"
        assert clip["active"] is True
        assert len(clip["recortes"]) == 1
        assert clip["recortes"][0]["title"] == "Economia"
        assert clip["deliveryChannels"]["email"] is True

    def test_clipping_by_id(self, mock_firestore_ds):
        result = test_schema.execute_sync(
            """
            query($id: String!) {
                clipping(id: $id) {
                    id
                    name
                }
            }
            """,
            variable_values={"id": "clip-1"},
            context_value=_make_authenticated_context(mock_firestore_ds),
        )
        assert result.errors is None
        assert result.data["clipping"]["id"] == "clip-1"
        assert result.data["clipping"]["name"] == "Meu Clipping"


class TestClippingMutations:
    def test_create_clipping_mutation(self, mock_firestore_ds):
        result = test_schema.execute_sync(
            """
            mutation($input: ClippingInput!) {
                createClipping(input: $input) {
                    id
                    name
                    description
                }
            }
            """,
            variable_values={
                "input": {
                    "name": "Novo Clipping",
                    "description": "Descricao",
                }
            },
            context_value=_make_authenticated_context(mock_firestore_ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["createClipping"]["id"] == "clip-new"
        mock_firestore_ds.create_clipping.assert_called_once()

    def test_delete_clipping_mutation(self, mock_firestore_ds):
        result = test_schema.execute_sync(
            """
            mutation($id: String!) {
                deleteClipping(id: $id)
            }
            """,
            variable_values={"id": "clip-1"},
            context_value=_make_authenticated_context(mock_firestore_ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["deleteClipping"] is True
        # Nova API: delete_clipping(user_id, clipping_id)
        mock_firestore_ds.delete_clipping.assert_called_once_with("user-123", "clip-1")

    def test_update_clipping_mutation(self, mock_firestore_ds):
        result = test_schema.execute_sync(
            """
            mutation($id: String!, $input: ClippingInput!) {
                updateClipping(id: $id, input: $input) {
                    id
                    name
                }
            }
            """,
            variable_values={
                "id": "clip-1",
                "input": {"name": "Updated Name"},
            },
            context_value=_make_authenticated_context(mock_firestore_ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["updateClipping"]["name"] == "Updated"


class TestClippingContextFields:
    """Fase A3: campos contextuais `isAuthor` e `mySubscription`."""

    def _ctx_with_loader(self, ds, user_id="user-123"):
        ctx = GraphQLContext(firestore_ds=ds)
        ctx.user = User(id=user_id, email="t@e.com")
        # Dataloader injetado no contexto
        ctx.subscription_loader = create_subscription_loader(ds)
        return ctx

    def test_my_clippings_query_returns_authored_and_subscribed(self, mock_firestore_ds):
        """user-123 tem 1 sub author + 1 subscriber → 2 clippings na lista."""
        mock_firestore_ds.get_my_clippings.return_value = [
            MyClippingResult(
                clipping=_sample_clipping_data(id="clip-own"),
                subscription=_sample_subscription(clipping_id="clip-own", role="author"),
            ),
            MyClippingResult(
                clipping=_sample_clipping_data(id="clip-sub"),
                subscription=_sample_subscription(clipping_id="clip-sub", role="subscriber"),
            ),
        ]
        result = test_schema.execute_sync(
            "{ clippings { id } }",
            context_value=self._ctx_with_loader(mock_firestore_ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        ids = {c["id"] for c in result.data["clippings"]}
        assert ids == {"clip-own", "clip-sub"}

    def test_clipping_is_author_true_for_author(self, mock_firestore_ds):
        mock_firestore_ds.get_my_clippings.return_value = [
            MyClippingResult(
                clipping=_sample_clipping_data(id="clip-1", author_user_id="user-123"),
                subscription=_sample_subscription(role="author"),
            )
        ]
        result = test_schema.execute_sync(
            "{ clippings { id isAuthor } }",
            context_value=self._ctx_with_loader(mock_firestore_ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["clippings"][0]["isAuthor"] is True

    def test_clipping_is_author_false_for_subscriber(self, mock_firestore_ds):
        mock_firestore_ds.get_my_clippings.return_value = [
            MyClippingResult(
                clipping=_sample_clipping_data(id="clip-1", author_user_id="other-user"),
                subscription=_sample_subscription(role="subscriber"),
            )
        ]
        result = test_schema.execute_sync(
            "{ clippings { id isAuthor } }",
            context_value=self._ctx_with_loader(mock_firestore_ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["clippings"][0]["isAuthor"] is False

    def test_clipping_my_subscription_returns_subscription(self, mock_firestore_ds):
        sub = _sample_subscription(clipping_id="clip-1", role="subscriber")
        mock_firestore_ds.get_my_clippings.return_value = [
            MyClippingResult(
                clipping=_sample_clipping_data(id="clip-1"),
                subscription=sub,
            )
        ]
        mock_firestore_ds.get_subscriptions_for_user_and_clippings.return_value = {
            "clip-1": sub
        }
        result = test_schema.execute_sync(
            """
            {
                clippings {
                    id
                    mySubscription { id role deliveryChannels { email telegram push } }
                }
            }
            """,
            context_value=self._ctx_with_loader(mock_firestore_ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        my_sub = result.data["clippings"][0]["mySubscription"]
        assert my_sub is not None
        assert my_sub["role"] == "SUBSCRIBER"
        assert my_sub["deliveryChannels"]["email"] is True

    def test_clipping_my_subscription_null_when_not_subscribed(self, mock_firestore_ds):
        """Clipping retornado por `clipping(id)` (público) sem sub do user."""
        mock_firestore_ds.get_clipping.return_value = _sample_clipping_data(id="clip-pub")
        mock_firestore_ds.get_subscriptions_for_user_and_clippings.return_value = {}
        result = test_schema.execute_sync(
            """
            query($id: String!) {
                clipping(id: $id) {
                    id
                    mySubscription { id }
                }
            }
            """,
            variable_values={"id": "clip-pub"},
            context_value=self._ctx_with_loader(mock_firestore_ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["clipping"]["mySubscription"] is None

    def test_my_subscription_uses_dataloader_no_n_plus_1(self, mock_firestore_ds):
        """5 clippings em uma query → 1 chamada ao datasource para subs."""
        mock_firestore_ds.get_my_clippings.return_value = [
            MyClippingResult(
                clipping=_sample_clipping_data(id=f"clip-{i}"),
                subscription=_sample_subscription(clipping_id=f"clip-{i}"),
            )
            for i in range(5)
        ]
        mock_firestore_ds.get_subscriptions_for_user_and_clippings.return_value = {
            f"clip-{i}": _sample_subscription(clipping_id=f"clip-{i}") for i in range(5)
        }
        result = test_schema.execute_sync(
            "{ clippings { id mySubscription { id } } }",
            context_value=self._ctx_with_loader(mock_firestore_ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert len(result.data["clippings"]) == 5
        # Assertção explícita anti-N+1: 1 (e apenas 1) chamada de query subs.
        assert mock_firestore_ds.get_subscriptions_for_user_and_clippings.call_count == 1


class TestClippingEstimate:
    def test_clipping_estimate(self, mock_firestore_ds):
        result = test_schema.execute_sync(
            """
            query {
                clippingEstimate(themes: ["economia", "saude"], agencies: ["agencia-brasil"]) {
                    totalEstimate
                }
            }
            """,
            context_value=_make_authenticated_context(mock_firestore_ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        estimate = result.data["clippingEstimate"]["totalEstimate"]
        # 2 themes * 10 + 1 agency * 5 = 25
        assert estimate == 25
