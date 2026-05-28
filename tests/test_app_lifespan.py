"""Integration tests do lifespan FastAPI (wiring de datasources + JWT).

Validam o comportamento end-to-end da Fase R1: que o app real (sem
`dependency_overrides`) constroi datasources a partir de env vars e usa o
JWT do header `Authorization` para popular `ctx.user`.

Estrategia de mock: patcham os *construtores* dos clients externos
(`typesense.Client`, `google.cloud.firestore.Client`, `asyncpg.create_pool`)
ANTES do startup do app, e o fetch de JWKS. Nao tocamos nos datasources em
si — eles sao reais. Isso garante que regressao no wiring (ex: env var
errada, esquecer de armazenar em app.state) seja detectada.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from graphql_api.app import create_app, get_graphql_context
from graphql_api.context import GraphQLContext, User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _typesense_conn_json() -> str:
    return json.dumps(
        {"host": "typesense.example.com", "port": 443, "protocol": "https", "apiKey": "ts-key"}
    )


@pytest.fixture
def env_stubs(monkeypatch):
    """Aplica env vars stub (formato producao) antes de criar o app."""
    monkeypatch.setenv("TYPESENSE_READ_CONN", _typesense_conn_json())
    monkeypatch.setenv("TYPESENSE_WRITE_CONN", _typesense_conn_json())
    monkeypatch.setenv("DATABASE_URL", "postgres://test:test@localhost:5432/test")
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("AUTH_JWKS_URL", "https://kc.example.com/realms/test/protocol/openid-connect/certs")
    monkeypatch.setenv("AUTH_ISSUER", "https://kc.example.com/realms/test")
    yield


@pytest.fixture
def mock_external_clients(env_stubs):
    """Patcha os construtores dos clients externos para nao tocar a rede."""
    # asyncpg.create_pool retorna um pool mockado com .close() async
    mock_pool = MagicMock()
    mock_pool.close = AsyncMock()

    with patch(
        "asyncpg.create_pool",
        new_callable=AsyncMock,
        return_value=mock_pool,
    ) as mock_create_pool, patch(
        "typesense.Client"
    ) as mock_typesense_class, patch(
        "google.cloud.firestore.Client"
    ) as mock_firestore_class, patch(
        "graphql_api.lifespan._prefetch_jwks",
        new_callable=AsyncMock,
        return_value={"keys": []},
    ) as mock_jwks:
        mock_typesense_class.return_value = MagicMock()
        mock_firestore_class.return_value = MagicMock()
        yield {
            "create_pool": mock_create_pool,
            "typesense": mock_typesense_class,
            "firestore": mock_firestore_class,
            "jwks": mock_jwks,
            "pool": mock_pool,
        }


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_populates_app_state_with_datasources(mock_external_clients):
    """Startup deve criar os 4 datasources e armazenar em app.state."""
    app = create_app()
    # `httpx.ASGITransport` nao dispara lifespan; chamamos o context diretamente.
    async with app.router.lifespan_context(app):
        assert app.state.typesense_ds is not None
        assert app.state.typesense_admin_ds is not None
        assert app.state.firestore_ds is not None
        assert app.state.postgres_ds is not None

    # Shutdown deve ter fechado o pool postgres.
    mock_external_clients["pool"].close.assert_awaited()


@pytest.mark.asyncio
async def test_lifespan_tolerates_missing_env_vars(monkeypatch):
    """Quando env vars estao ausentes, o app sobe com DSes=None (warning, nao crash)."""
    # Limpa as env vars que controlam datasources.
    for name in (
        "TYPESENSE_READ_CONN",
        "TYPESENSE_WRITE_CONN",
        "DATABASE_URL",
        "AUTH_JWKS_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    # Bloqueia que FirestoreDatasource.from_env tente ADC real (que pode
    # autenticar acidentalmente no dev local). Forcamos retorno None.
    with patch(
        "graphql_api.lifespan.FirestoreDatasource.from_env",
        return_value=None,
    ):
        app = create_app()
        async with app.router.lifespan_context(app):
            assert app.state.typesense_ds is None
            assert app.state.typesense_admin_ds is None
            assert app.state.firestore_ds is None
            assert app.state.postgres_ds is None


# ---------------------------------------------------------------------------
# get_context com Request (sem dependency_overrides)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_context_reads_datasources_from_app_state(mock_external_clients):
    """`get_context(request)` deve ler app.state e devolver contexto populado."""
    from starlette.requests import Request as StarletteRequest

    from graphql_api.context import get_context

    app = create_app()
    async with app.router.lifespan_context(app):
        # Cria um Request fake apontando para o mesmo app.
        scope = {
            "type": "http",
            "app": app,
            "headers": [],
            "method": "GET",
            "path": "/",
        }
        req = StarletteRequest(scope)
        ctx = await get_context(req)
        assert isinstance(ctx, GraphQLContext)
        assert ctx.typesense_ds is not None
        assert ctx.firestore_ds is not None
        assert ctx.postgres_ds is not None
        assert ctx.typesense_admin_ds is not None
        # Sem header Authorization → ctx.user fica None.
        assert ctx.user is None


# ---------------------------------------------------------------------------
# JWT path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_populates_user_when_authorization_header_valid(
    mock_external_clients, jwks_dict, rsa_private_pem
):
    """Header `Authorization: Bearer <jwt>` valido → ctx.user populado."""
    from graphql_api.context import get_context

    token = jwt.encode(
        {
            "sub": "user-abc",
            "email": "alice@example.com",
            "realm_access": {"roles": ["editor", "viewer"]},
            "iss": "https://kc.example.com/realms/test",
            "iat": datetime.now(tz=timezone.utc),
            "exp": datetime.now(tz=timezone.utc) + timedelta(hours=1),
        },
        rsa_private_pem,
        algorithm="RS256",
        headers={"kid": "test-key-1"},
    )

    app = create_app()
    async with app.router.lifespan_context(app):
        with patch(
            "graphql_api.auth.jwt._fetch_jwks",
            new_callable=AsyncMock,
            return_value=jwks_dict,
        ):
            from starlette.requests import Request as StarletteRequest

            scope = {
                "type": "http",
                "app": app,
                "headers": [(b"authorization", f"Bearer {token}".encode())],
                "method": "GET",
                "path": "/",
            }
            req = StarletteRequest(scope)
            ctx = await get_context(req)

        assert ctx.user is not None
        assert isinstance(ctx.user, User)
        assert ctx.user.id == "user-abc"
        assert ctx.user.email == "alice@example.com"
        # Keycloak realm_access.roles → User.roles
        assert "editor" in ctx.user.roles
        assert "viewer" in ctx.user.roles


@pytest.mark.asyncio
async def test_context_leaves_user_none_when_no_authorization_header(mock_external_clients):
    """Sem header → ctx.user fica None (queries publicas continuam funcionando)."""
    from starlette.requests import Request as StarletteRequest

    from graphql_api.context import get_context

    app = create_app()
    async with app.router.lifespan_context(app):
        req = StarletteRequest({"type": "http", "app": app, "headers": [], "method": "GET", "path": "/"})
        ctx = await get_context(req)
        assert ctx.user is None


@pytest.mark.asyncio
async def test_context_leaves_user_none_when_jwt_invalid(mock_external_clients):
    """Token invalido → ctx.user=None, sem levantar excecao."""
    from starlette.requests import Request as StarletteRequest

    from graphql_api.context import get_context

    app = create_app()
    async with app.router.lifespan_context(app):
        scope = {
            "type": "http",
            "app": app,
            "headers": [(b"authorization", b"Bearer not.a.real.jwt")],
            "method": "GET",
            "path": "/",
        }
        req = StarletteRequest(scope)
        with patch(
            "graphql_api.auth.jwt._fetch_jwks",
            new_callable=AsyncMock,
            return_value={"keys": []},
        ):
            ctx = await get_context(req)
        assert ctx.user is None


# ---------------------------------------------------------------------------
# Smoke: query GraphQL real ate o resolver com DSes do lifespan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graphql_query_uses_lifespan_datasources(mock_external_clients):
    """Query `articles` deve chegar ao TypesenseDatasource (real, com client mockado)."""
    # Mocka o client subjacente para devolver hits previsiveis.
    mock_client_instance = mock_external_clients["typesense"].return_value
    mock_client_instance.collections = MagicMock()
    mock_client_instance.collections.__getitem__ = MagicMock(
        return_value=MagicMock(
            documents=MagicMock(
                search=MagicMock(
                    return_value={
                        "hits": [
                            {
                                "document": {
                                    "unique_id": "x-1",
                                    "title": "Hello",
                                    "url": "https://example.com/x-1",
                                }
                            }
                        ],
                        "found": 1,
                    }
                )
            )
        )
    )

    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/graphql",
                json={"query": "{ articles { found articles { uniqueId title } } }"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert "errors" not in body, body
            assert body["data"]["articles"]["found"] == 1
            assert body["data"]["articles"]["articles"][0]["uniqueId"] == "x-1"


# ---------------------------------------------------------------------------
# Compat: dependency_overrides continua funcionando
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dependency_override_still_works(mock_external_clients):
    """`dependency_overrides[get_graphql_context]` precisa continuar funcionando para
    nao quebrar os testes em `tests/test_sse_endpoint.py` (e similares)."""

    async def _override():
        ctx = GraphQLContext()
        ctx.user = User(id="overridden-user", email="x@y", roles=[])
        return ctx

    app = create_app()
    app.dependency_overrides[get_graphql_context] = _override

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # ping nao usa DS — basta confirmar que o override foi aplicado e o
            # contexto eh utilizado pelo router.
            resp = await c.post("/graphql", json={"query": "{ ping }"})
            assert resp.status_code == 200
            assert resp.json()["data"]["ping"] == "pong"
