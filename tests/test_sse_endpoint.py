"""Testes do endpoint SSE `/graphql/stream` (Fase A6-fixup).

Implementa a spec graphql-sse (distinct connections mode):
- POST/GET `/graphql/stream` com `{query, variables, operationName, extensions}`.
- Response `Content-Type: text/event-stream`.
- Eventos `event: next\\ndata: <JSON ExecutionResult>\\n\\n` por update.
- Evento `event: complete\\ndata:\\n\\n` ao final.

A subscription `generateRecortes` (Fase A6) faz passthrough de SSE upstream
mas o transport entre o cliente urql e o graphql-api passa a ser SSE
(antes era WebSocket; mudanca decidida em A6-fixup).
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from graphql_api.app import create_app, get_graphql_context
from graphql_api.context import GraphQLContext, User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_ctx_factory():
    async def _factory() -> GraphQLContext:
        ctx = GraphQLContext()
        ctx.user = User(id="user-123", email="dev@example.com")
        return ctx

    return _factory


def _anon_ctx_factory():
    async def _factory() -> GraphQLContext:
        return GraphQLContext()

    return _factory


def _make_sse_body(events: list[dict]) -> bytes:
    parts = []
    for evt in events:
        parts.append(f"data: {json.dumps(evt)}\n\n")
    return "".join(parts).encode()


def _parse_sse_events(text: str) -> list[tuple[str, str]]:
    """Parseia uma string SSE em pares (event, data)."""
    out: list[tuple[str, str]] = []
    event: str | None = None
    data_buf: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.rstrip("\r")
        if line == "":
            if event is not None:
                out.append((event, "\n".join(data_buf)))
            event = None
            data_buf = []
            continue
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_buf.append(line[len("data:"):].lstrip())
    if event is not None:
        out.append((event, "\n".join(data_buf)))
    return out


async def _stream_post(app, body: dict, *, headers: dict | None = None) -> tuple[int, dict, str]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with c.stream("POST", "/graphql/stream", json=body, headers=headers or {}) as resp:
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
            text = b"".join(chunks).decode()
            return resp.status_code, dict(resp.headers), text


async def _stream_get(app, params: dict, *, headers: dict | None = None) -> tuple[int, dict, str]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        url = "/graphql/stream?" + urlencode(params)
        async with c.stream("GET", url, headers=headers or {}) as resp:
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
            text = b"".join(chunks).decode()
            return resp.status_code, dict(resp.headers), text


# ---------------------------------------------------------------------------
# 13) Content-Type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_sse_endpoint_returns_text_event_stream_content_type(monkeypatch):
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")
    respx.post("http://localhost:8080/agent/generate-recortes").mock(
        return_value=httpx.Response(
            200,
            content=_make_sse_body([{"type": "thinking", "message": "ok"}]),
            headers={"content-type": "text/event-stream"},
        ),
    )

    app = create_app()
    app.dependency_overrides[get_graphql_context] = _auth_ctx_factory()

    status, headers, _body = await _stream_post(
        app,
        {"query": 'subscription { generateRecortes(prompt: "x") { __typename } }'},
    )

    assert status == 200
    assert headers.get("content-type", "").startswith("text/event-stream")
    assert headers.get("cache-control") == "no-cache"


# ---------------------------------------------------------------------------
# 14) `next` event format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_sse_endpoint_emits_next_events_with_correct_format(monkeypatch):
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")
    respx.post("http://localhost:8080/agent/generate-recortes").mock(
        return_value=httpx.Response(
            200,
            content=_make_sse_body([{"type": "thinking", "message": "ola"}]),
            headers={"content-type": "text/event-stream"},
        ),
    )

    app = create_app()
    app.dependency_overrides[get_graphql_context] = _auth_ctx_factory()

    status, _headers, body = await _stream_post(
        app,
        {
            "query": (
                "subscription { generateRecortes(prompt: \"x\") {"
                " __typename ... on AgentEventThinking { message } } }"
            ),
        },
    )

    assert status == 200
    events = _parse_sse_events(body)
    next_events = [(e, d) for (e, d) in events if e == "next"]
    assert len(next_events) >= 1

    # data deve ser JSON ExecutionResult valido com chave `data`.
    parsed = json.loads(next_events[0][1])
    assert "data" in parsed
    payload = parsed["data"]["generateRecortes"]
    assert payload["__typename"] == "AgentEventThinking"
    assert payload["message"] == "ola"


# ---------------------------------------------------------------------------
# 15) `complete` event when stream ends
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_sse_endpoint_emits_complete_event_when_stream_ends(monkeypatch):
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")
    respx.post("http://localhost:8080/agent/generate-recortes").mock(
        return_value=httpx.Response(
            200,
            content=_make_sse_body([{"type": "thinking", "message": "a"}]),
            headers={"content-type": "text/event-stream"},
        ),
    )

    app = create_app()
    app.dependency_overrides[get_graphql_context] = _auth_ctx_factory()

    status, _headers, body = await _stream_post(
        app,
        {"query": 'subscription { generateRecortes(prompt: "x") { __typename } }'},
    )

    assert status == 200
    events = _parse_sse_events(body)
    types = [e for (e, _d) in events]
    assert "complete" in types
    # `complete` deve ser o ultimo evento.
    assert types[-1] == "complete"


# ---------------------------------------------------------------------------
# 16) POST with JSON body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_sse_endpoint_accepts_post_with_json_body(monkeypatch):
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")
    respx.post("http://localhost:8080/agent/generate-recortes").mock(
        return_value=httpx.Response(
            200,
            content=_make_sse_body([{"type": "thinking", "message": "via-post"}]),
            headers={"content-type": "text/event-stream"},
        ),
    )

    app = create_app()
    app.dependency_overrides[get_graphql_context] = _auth_ctx_factory()

    status, _headers, body = await _stream_post(
        app,
        {
            "query": (
                "subscription Foo($p: String!) { generateRecortes(prompt: $p) {"
                " __typename ... on AgentEventThinking { message } } }"
            ),
            "variables": {"p": "x"},
            "operationName": "Foo",
        },
    )

    assert status == 200
    events = _parse_sse_events(body)
    next_data = json.loads([d for (e, d) in events if e == "next"][0])
    assert next_data["data"]["generateRecortes"]["message"] == "via-post"


# ---------------------------------------------------------------------------
# 17) GET with query string
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_sse_endpoint_accepts_get_with_query_string(monkeypatch):
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")
    respx.post("http://localhost:8080/agent/generate-recortes").mock(
        return_value=httpx.Response(
            200,
            content=_make_sse_body([{"type": "thinking", "message": "via-get"}]),
            headers={"content-type": "text/event-stream"},
        ),
    )

    app = create_app()
    app.dependency_overrides[get_graphql_context] = _auth_ctx_factory()

    status, _headers, body = await _stream_get(
        app,
        {
            "query": (
                "subscription Foo($p: String!) { generateRecortes(prompt: $p) {"
                " __typename ... on AgentEventThinking { message } } }"
            ),
            "variables": json.dumps({"p": "x"}),
            "operationName": "Foo",
        },
    )

    assert status == 200
    events = _parse_sse_events(body)
    next_data = json.loads([d for (e, d) in events if e == "next"][0])
    assert next_data["data"]["generateRecortes"]["message"] == "via-get"


# ---------------------------------------------------------------------------
# 18) Unauthenticated: erros via SSE (nao 401 — graphql-sse usa errors no payload)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_endpoint_unauthenticated_emits_errors_payload():
    app = create_app()
    app.dependency_overrides[get_graphql_context] = _anon_ctx_factory()

    status, _headers, body = await _stream_post(
        app,
        {"query": 'subscription { generateRecortes(prompt: "x") { __typename } }'},
    )

    # graphql-sse spec: status 200; erros vao no payload do evento `next`.
    assert status == 200
    events = _parse_sse_events(body)
    next_events = [json.loads(d) for (e, d) in events if e == "next"]
    # Pelo menos um payload tem `errors` com UNAUTHENTICATED.
    flat = json.dumps(next_events)
    assert "UNAUTHENTICATED" in flat
    # Sem `data` util.
    assert any(p.get("data") is None or not p.get("data") for p in next_events)
    # Stream encerra com complete.
    types = [e for (e, _d) in events]
    assert types[-1] == "complete"


# ---------------------------------------------------------------------------
# 19) Spec compliance gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_sse_endpoint_compliant_with_graphql_sse_spec_distinct_mode(monkeypatch):
    """Gate consolidado para graphql-sse distinct connections mode."""
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")
    respx.post("http://localhost:8080/agent/generate-recortes").mock(
        return_value=httpx.Response(
            200,
            content=_make_sse_body(
                [
                    {"type": "thinking", "message": "1"},
                    {"type": "thinking", "message": "2"},
                ]
            ),
            headers={"content-type": "text/event-stream"},
        ),
    )

    app = create_app()
    app.dependency_overrides[get_graphql_context] = _auth_ctx_factory()

    status, headers, body = await _stream_post(
        app,
        {
            "query": (
                "subscription { generateRecortes(prompt: \"x\") {"
                " __typename ... on AgentEventThinking { message } } }"
            ),
        },
        headers={"accept": "text/event-stream"},
    )

    # 1) status 200
    assert status == 200
    # 2) content-type text/event-stream
    assert headers.get("content-type", "").startswith("text/event-stream")
    # 3) eventos `next` com `data: <JSON ExecutionResult>`
    events = _parse_sse_events(body)
    next_events = [json.loads(d) for (e, d) in events if e == "next"]
    assert len(next_events) == 2
    assert all("data" in p for p in next_events)
    assert [p["data"]["generateRecortes"]["message"] for p in next_events] == ["1", "2"]
    # 4) evento `complete` terminando o stream
    assert events[-1][0] == "complete"


# ---------------------------------------------------------------------------
# Queries/mutations continuam funcionando em /graphql
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regular_graphql_endpoint_still_works(client):
    response = await client.post("/graphql", json={"query": "{ ping }"})
    assert response.status_code == 200
    assert response.json()["data"]["ping"] == "pong"


# ---------------------------------------------------------------------------
# WS protocols NAO devem mais ser importados em app.py
# ---------------------------------------------------------------------------


def test_app_does_not_import_ws_protocols():
    import inspect

    from graphql_api import app as app_module

    src = inspect.getsource(app_module)
    assert "GRAPHQL_TRANSPORT_WS_PROTOCOL" not in src
    assert "GRAPHQL_WS_PROTOCOL" not in src
    assert "subscription_protocols" not in src
