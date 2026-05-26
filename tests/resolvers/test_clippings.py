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
                    "schedule": "0 8 * * *",
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
                "input": {"name": "Updated Name", "schedule": "0 8 * * *"},
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

    @pytest.mark.asyncio
    async def test_clipping_my_subscription_returns_subscription(self, mock_firestore_ds):
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
        result = await test_schema.execute(
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

    @pytest.mark.asyncio
    async def test_clipping_my_subscription_null_when_not_subscribed(self, mock_firestore_ds):
        """Clipping retornado por `clipping(id)` (público) sem sub do user."""
        mock_firestore_ds.get_clipping.return_value = _sample_clipping_data(id="clip-pub")
        mock_firestore_ds.get_subscriptions_for_user_and_clippings.return_value = {}
        result = await test_schema.execute(
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

    @pytest.mark.asyncio
    async def test_my_subscription_uses_dataloader_no_n_plus_1(self, mock_firestore_ds):
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
        result = await test_schema.execute(
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

    def test_clipping_estimate_uses_recortes_only(self, mock_firestore_ds):
        """O endpoint estimate ignora schedule/datas — só conta recortes (gate A4)."""
        result = test_schema.execute_sync(
            """
            query {
                clippingEstimate(themes: ["a"], agencies: ["b"], keywords: ["c"]) {
                    totalEstimate
                }
            }
            """,
            context_value=_make_authenticated_context(mock_firestore_ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        # 1*10 + 1*5 + 1*3 = 18 — schedule não entra na conta.
        assert result.data["clippingEstimate"]["totalEstimate"] == 18


# ---------------------------------------------------------------------------
# Fase A4: cron + novos campos
# ---------------------------------------------------------------------------
class TestClippingA4ScheduleFields:
    """Testes da TDD checklist da Fase A4 — schedule, nextRunAt, datas, extraEmails, includeHistory."""

    _BASE_INPUT = {
        "name": "Diário",
        "description": "All-day",
        "recortes": [],
        "schedule": "0 8 * * *",
    }

    def _ctx(self, ds):
        return _make_authenticated_context(ds)

    def test_create_clipping_with_schedule_valid(self, mock_firestore_ds):
        result = test_schema.execute_sync(
            """
            mutation($input: ClippingInput!) {
                createClipping(input: $input) { id schedule }
            }
            """,
            variable_values={"input": self._BASE_INPUT},
            context_value=self._ctx(mock_firestore_ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        # datasource recebeu o schedule
        call_kwargs = mock_firestore_ds.create_clipping.call_args
        passed_data = call_kwargs.args[1] if call_kwargs.args else call_kwargs.kwargs.get("data")
        assert passed_data["schedule"] == "0 8 * * *"

    def test_create_clipping_with_invalid_cron_raises_validation_error(self, mock_firestore_ds):
        bad = dict(self._BASE_INPUT, schedule="garbage cron")
        result = test_schema.execute_sync(
            """
            mutation($input: ClippingInput!) {
                createClipping(input: $input) { id }
            }
            """,
            variable_values={"input": bad},
            context_value=self._ctx(mock_firestore_ds),
        )
        assert result.errors is not None and len(result.errors) > 0
        assert "schedule" in str(result.errors[0].message).lower() or "cron" in str(
            result.errors[0].message
        ).lower()
        mock_firestore_ds.create_clipping.assert_not_called()

    def test_create_clipping_calculates_next_run_at(self):
        """schedule '0 8 * * *' criado às 10:00 UTC → next_run_at amanhã 08:00 UTC."""
        from datetime import datetime, timezone

        from graphql_api.lib.cron import calculate_next_run

        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        nxt = calculate_next_run("0 8 * * *", now)
        assert nxt == datetime(2026, 1, 2, 8, 0, 0, tzinfo=timezone.utc)

    def test_create_clipping_with_start_date_future(self):
        from datetime import datetime, timezone

        from graphql_api.lib.cron import calculate_next_run

        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        start = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        nxt = calculate_next_run("0 8 * * *", now, start_date=start)
        assert nxt is not None and nxt >= start

    def test_create_clipping_with_end_date_in_past_yields_null_next_run(self):
        from datetime import datetime, timezone

        from graphql_api.lib.cron import calculate_next_run

        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert calculate_next_run("0 8 * * *", now, end_date=end) is None

    def test_update_clipping_recalculates_next_run_at_when_schedule_changes(self, mock_firestore_ds):
        """Atualizar `schedule` deve fazer datasource receber `next_run_at` recalculado."""
        result = test_schema.execute_sync(
            """
            mutation($id: String!, $input: ClippingInput!) {
                updateClipping(id: $id, input: $input) { id }
            }
            """,
            variable_values={
                "id": "clip-1",
                "input": dict(self._BASE_INPUT, schedule="0 9 * * *"),
            },
            context_value=self._ctx(mock_firestore_ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        args, kwargs = mock_firestore_ds.update_clipping.call_args
        # update_clipping(user_id, clipping_id, data)
        data = args[2] if len(args) >= 3 else kwargs.get("data")
        assert data["schedule"] == "0 9 * * *"
        assert "next_run_at" in data
        assert data["next_run_at"] is not None

    def test_update_clipping_recalculates_next_run_at_when_start_date_changes(self, mock_firestore_ds):
        from datetime import datetime, timezone

        future = datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
        result = test_schema.execute_sync(
            """
            mutation($id: String!, $input: ClippingInput!) {
                updateClipping(id: $id, input: $input) { id }
            }
            """,
            variable_values={
                "id": "clip-1",
                "input": dict(self._BASE_INPUT, startDate=future),
            },
            context_value=self._ctx(mock_firestore_ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        args, kwargs = mock_firestore_ds.update_clipping.call_args
        data = args[2] if len(args) >= 3 else kwargs.get("data")
        # next_run_at >= 2030-01-01
        assert data.get("next_run_at") is not None
        assert data["next_run_at"] >= datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_create_clipping_extra_emails_validates_format(self, mock_firestore_ds):
        bad = dict(self._BASE_INPUT, extraEmails=["valid@x.com", "not-an-email"])
        result = test_schema.execute_sync(
            """
            mutation($input: ClippingInput!) {
                createClipping(input: $input) { id }
            }
            """,
            variable_values={"input": bad},
            context_value=self._ctx(mock_firestore_ds),
        )
        assert result.errors is not None and len(result.errors) > 0
        assert "email" in str(result.errors[0].message).lower()
        mock_firestore_ds.create_clipping.assert_not_called()

    def test_create_clipping_extra_emails_max_count_20(self, mock_firestore_ds):
        emails = [f"u{i}@example.com" for i in range(21)]
        result = test_schema.execute_sync(
            """
            mutation($input: ClippingInput!) {
                createClipping(input: $input) { id }
            }
            """,
            variable_values={"input": dict(self._BASE_INPUT, extraEmails=emails)},
            context_value=self._ctx(mock_firestore_ds),
        )
        assert result.errors is not None and len(result.errors) > 0
        assert "20" in str(result.errors[0].message) or "max" in str(
            result.errors[0].message
        ).lower()

    def test_clipping_input_schedule_required(self, mock_firestore_ds):
        """Schema GraphQL exige `schedule` no input — query sem schedule falha."""
        from graphql_api.schema.types.clipping import ClippingInput

        # Strawberry expõe os campos via `__strawberry_definition__`.
        sd = ClippingInput.__strawberry_definition__
        schedule_field = next((f for f in sd.fields if f.python_name == "schedule"), None)
        assert schedule_field is not None
        # Non-null = não opcional. Validamos via SDL para evitar acoplar com
        # a estrutura interna de Strawberry (`StrawberryField.type` muda entre
        # versões).
        import strawberry

        @strawberry.type
        class _Q:
            ok: bool = True

        @strawberry.type
        class _M(ClippingMutation):
            pass

        schema = strawberry.Schema(query=_Q, mutation=_M)
        sdl = str(schema)
        # ClippingInput aparece com `schedule: String!` (non-null)
        assert "schedule: String!" in sdl

    def test_legacy_schedule_time_still_readable(self):
        """Clipping antigo com `scheduleTime` no Firestore continua parseando."""
        from graphql_api.datasources.firestore import ClippingData

        legacy_doc = {
            "id": "clip-legacy",
            "name": "Legacy",
            "recortes": [],
            "scheduleTime": "08:00",  # legacy
            "active": True,
        }
        clipping = ClippingData.model_validate(legacy_doc)
        assert clipping.schedule_time == "08:00"

    def test_include_history_default_false(self, mock_firestore_ds):
        """Quando `includeHistory` não é informado no input → datasource recebe False."""
        result = test_schema.execute_sync(
            """
            mutation($input: ClippingInput!) {
                createClipping(input: $input) { id }
            }
            """,
            variable_values={"input": self._BASE_INPUT},
            context_value=self._ctx(mock_firestore_ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        args, kwargs = mock_firestore_ds.create_clipping.call_args
        data = args[1] if len(args) >= 2 else kwargs.get("data")
        assert data["include_history"] is False
