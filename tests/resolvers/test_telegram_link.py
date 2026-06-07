"""Testes do resolver `currentUserHasTelegramLinked` (Fase 1A SSR).

Substitui o `getHasTelegram` do portal. Le `users/{user.id}/telegramLink/account`
e retorna se o doc existe.

- doc presente -> True
- doc ausente -> False
- anonimo (sem sessao) -> False (nao erra)
"""

from unittest.mock import MagicMock

import strawberry

from graphql_api.context import GraphQLContext, User
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.user import UserQuery


@strawberry.type
class _Query(HealthQuery, UserQuery):
    pass


test_schema = strawberry.Schema(query=_Query)


QUERY = "{ currentUserHasTelegramLinked }"


def _make_ds(has_telegram: bool) -> MagicMock:
    ds = MagicMock()
    ds.has_telegram_linked.return_value = has_telegram
    return ds


def _authenticated_context(ds, user_id="user-123") -> GraphQLContext:
    ctx = GraphQLContext(firestore_ds=ds)
    ctx.user = User(id=user_id, email="test@example.com")
    return ctx


def _anonymous_context(ds) -> GraphQLContext:
    return GraphQLContext(firestore_ds=ds)


class TestCurrentUserHasTelegramLinked:
    def test_returns_true_when_doc_present(self):
        ds = _make_ds(has_telegram=True)
        result = test_schema.execute_sync(
            QUERY, context_value=_authenticated_context(ds)
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["currentUserHasTelegramLinked"] is True
        ds.has_telegram_linked.assert_called_once_with("user-123")

    def test_returns_false_when_doc_absent(self):
        ds = _make_ds(has_telegram=False)
        result = test_schema.execute_sync(
            QUERY, context_value=_authenticated_context(ds)
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["currentUserHasTelegramLinked"] is False
        ds.has_telegram_linked.assert_called_once_with("user-123")

    def test_returns_false_when_anonymous(self):
        ds = _make_ds(has_telegram=True)
        result = test_schema.execute_sync(
            QUERY, context_value=_anonymous_context(ds)
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["currentUserHasTelegramLinked"] is False
        # Sem sessao: nao consulta o datasource.
        ds.has_telegram_linked.assert_not_called()
