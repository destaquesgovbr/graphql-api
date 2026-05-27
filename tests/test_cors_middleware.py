"""Testes do CORS middleware na FastAPI app.

Razao: `/graphql/stream` e consumido pelo portal (cross-origin no dev
local; mesma origem em prod) e `/graphql` precisa estar acessivel para
widgets embarcaveis externos (iframes 3rd-party).

Default `*` para o widget endpoint (publico). Em prod, infra pode setar
`CORS_ALLOW_ORIGINS=https://destaques.gov.br,https://staging.destaques.gov.br`
para restringir.

NB: `create_app()` le `CORS_ALLOW_ORIGINS` em tempo de chamada (via
`os.environ.get`), entao basta setar a env var antes de chamar
`create_app()` — nao precisa de `importlib.reload` (que quebra
dependency_overrides em outros testes).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from graphql_api.app import create_app


@pytest.fixture
def app_default(monkeypatch):
    """Cria app com defaults de CORS (allow_origins=*)."""
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    return create_app()


@pytest.fixture
def app_restricted(monkeypatch):
    """Cria app com CORS restrito a origens especificas."""
    monkeypatch.setenv(
        "CORS_ALLOW_ORIGINS",
        "https://destaques.gov.br,https://staging.destaques.gov.br",
    )
    return create_app()


@pytest.mark.asyncio
async def test_cors_preflight_returns_200_with_allow_origin(app_default):
    """OPTIONS preflight em /graphql retorna 200 e Access-Control-Allow-Origin."""
    transport = ASGITransport(app=app_default)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.options(
            "/graphql",
            headers={
                "Origin": "https://external-widget.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, Authorization",
            },
        )
    assert resp.status_code == 200
    # Default `*` permite qualquer origem.
    allow_origin = resp.headers.get("access-control-allow-origin")
    assert allow_origin in ("*", "https://external-widget.example.com")
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    assert "POST" in allow_methods


@pytest.mark.asyncio
async def test_graphql_endpoint_has_cors_headers_in_response(app_default):
    """POST /graphql com Origin retorna header Access-Control-Allow-Origin."""
    transport = ASGITransport(app=app_default)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/graphql",
            json={"query": "{ __typename }"},
            headers={"Origin": "https://destaques.gov.br"},
        )
    # Query simples nao precisa autenticar — __typename funciona sem auth.
    assert resp.status_code == 200
    # CORS header deve estar presente na response.
    assert "access-control-allow-origin" in {k.lower() for k in resp.headers.keys()}


@pytest.mark.asyncio
async def test_cors_origins_env_var_overrides_default(app_restricted):
    """`CORS_ALLOW_ORIGINS` env var restringe `Access-Control-Allow-Origin`.

    Preflight de origem permitida retorna 200 com o origin echoed; preflight
    de origem nao permitida nao retorna o header (FastAPI/Starlette omite-o
    em vez de retornar 403).
    """
    transport = ASGITransport(app=app_restricted)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Origem permitida — header presente
        resp_ok = await c.options(
            "/graphql",
            headers={
                "Origin": "https://destaques.gov.br",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        # Origem NAO permitida
        resp_bad = await c.options(
            "/graphql",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )

    assert resp_ok.headers.get("access-control-allow-origin") == "https://destaques.gov.br"
    # Para origem nao permitida o Starlette CORS middleware nao seta o header.
    assert resp_bad.headers.get("access-control-allow-origin") != "https://evil.example.com"
