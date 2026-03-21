import pytest
import strawberry
from strawberry.permission import PermissionExtension
from strawberry.types import Info

from graphql_api.auth.guards import IsAuthenticated, IsInternal
from graphql_api.context import GraphQLContext, User, ServiceAccount


def _make_schema(permission_class):
    """Create a minimal schema with a single guarded field."""

    @strawberry.type
    class Query:
        @strawberry.field(extensions=[PermissionExtension(permissions=[permission_class()])])
        def protected(self, info: Info) -> str:
            return "ok"

    return strawberry.Schema(query=Query)


@pytest.mark.asyncio
async def test_authenticated_guard_allows_with_user():
    schema = _make_schema(IsAuthenticated)
    ctx = GraphQLContext()
    ctx.user = User(id="u1", email="a@b.com", roles=[])

    result = await schema.execute("{ protected }", context_value=ctx)
    assert result.errors is None
    assert result.data == {"protected": "ok"}


@pytest.mark.asyncio
async def test_authenticated_guard_blocks_without_user():
    schema = _make_schema(IsAuthenticated)
    ctx = GraphQLContext()

    result = await schema.execute("{ protected }", context_value=ctx)
    assert result.errors is not None
    assert "UNAUTHENTICATED" in result.errors[0].message


@pytest.mark.asyncio
async def test_internal_guard_allows_with_service_account():
    schema = _make_schema(IsInternal)
    ctx = GraphQLContext()
    ctx.service_account = ServiceAccount(email="sa@proj.iam.gserviceaccount.com")

    result = await schema.execute("{ protected }", context_value=ctx)
    assert result.errors is None
    assert result.data == {"protected": "ok"}


@pytest.mark.asyncio
async def test_internal_guard_blocks_without_service_account():
    schema = _make_schema(IsInternal)
    ctx = GraphQLContext()

    result = await schema.execute("{ protected }", context_value=ctx)
    assert result.errors is not None
    assert "FORBIDDEN" in result.errors[0].message
