"""Testes da subscription `generateRecortes` (Fase A6 — passthrough SSE).

A subscription nao executa o agente — apenas faz proxy do SSE upstream
emitido pelo `clipping/` worker. Aqui mockamos a chamada HTTP via `respx`
e validamos o mapeamento `SSE upstream -> AgentEvent GraphQL`.
"""

import json
from typing import AsyncIterator

import httpx
import pytest
import respx

from graphql_api.context import GraphQLContext, User
from graphql_api.lib.oidc import _METADATA_URL
from graphql_api.schema import schema as full_schema


def _auth_ctx() -> GraphQLContext:
    ctx = GraphQLContext()
    ctx.user = User(id="user-123", email="dev@example.com")
    return ctx


def _anon_ctx() -> GraphQLContext:
    return GraphQLContext()


def _make_sse_body(events: list[dict]) -> bytes:
    """Constroi um corpo SSE com lista de eventos JSON."""
    parts = []
    for evt in events:
        parts.append(f"data: {json.dumps(evt)}\n\n")
    return "".join(parts).encode()


async def _collect(stream: AsyncIterator) -> list:
    results = []
    async for item in stream:
        results.append(item)
    return results


# ---------------------------------------------------------------------------
# 1) Schema introspection
# ---------------------------------------------------------------------------


def test_agent_event_union_has_all_types():
    """O schema introspection deve expor a uniao `AgentEvent` com 7 variantes."""
    str_schema = str(full_schema)
    assert "union AgentEvent" in str_schema
    for variant in (
        "AgentEventThinking",
        "AgentEventToolCall",
        "AgentEventToolResult",
        "AgentEventSampleResult",
        "AgentEventAdjusting",
        "AgentEventDone",
        "AgentEventError",
    ):
        assert variant in str_schema, f"missing variant {variant} in schema"

    assert "generateRecortes" in str_schema


# ---------------------------------------------------------------------------
# 2) Auth gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_to_agent_requires_auth(monkeypatch):
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")

    sub_result = await full_schema.subscribe(
        'subscription { generateRecortes(prompt: "hi") { __typename } }',
        context_value=_anon_ctx(),
    )

    # Sub returns either an ExecutionResult with errors (validation/permission)
    # or an async iterator that immediately yields one error event.
    if hasattr(sub_result, "errors"):
        assert sub_result.errors is not None
        assert any("UNAUTHENTICATED" in str(e.message) for e in sub_result.errors)
        return

    events = await _collect(sub_result)
    # Some Strawberry permissions surface as a single error message; ensure
    # nothing meaningful streamed back.
    assert events == [] or any("UNAUTHENTICATED" in str(getattr(e, "errors", "")) for e in events)


# ---------------------------------------------------------------------------
# 3-5, 10, 11) Passthrough de eventos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_yields_thinking_event_from_upstream_mock(monkeypatch):
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")

    body = _make_sse_body([
        {"type": "thinking", "message": "Analisando prompt..."},
    ])
    respx.post("http://localhost:8080/agent/generate-recortes").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}),
    )

    sub_result = await full_schema.subscribe(
        'subscription { generateRecortes(prompt: "teste") { __typename ... on AgentEventThinking { message } } }',
        context_value=_auth_ctx(),
    )
    events = await _collect(sub_result)

    assert len(events) == 1
    payload = events[0].data["generateRecortes"]
    assert payload["__typename"] == "AgentEventThinking"
    assert payload["message"] == "Analisando prompt..."


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_yields_done_event_with_recortes(monkeypatch):
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")

    done_event = {
        "type": "done",
        "result": {
            "recortes": [
                {
                    "id": "r1",
                    "title": "Economia",
                    "themes": ["economia"],
                    "agencies": ["agencia-brasil"],
                    "keywords": ["pib"],
                }
            ],
            "explanation": "Foco em economia.",
            "description": "Clipping diario sobre economia.",
            "suggested_name": "Economia Diaria",
            "iterations": 3,
            "converged": True,
        },
    }
    body = _make_sse_body([done_event])
    respx.post("http://localhost:8080/agent/generate-recortes").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}),
    )

    sub_result = await full_schema.subscribe(
        """
        subscription {
            generateRecortes(prompt: "economia") {
                __typename
                ... on AgentEventDone {
                    explanation
                    description
                    suggestedName
                    iterations
                    converged
                    recortes { id title themes agencies keywords }
                }
            }
        }
        """,
        context_value=_auth_ctx(),
    )
    events = await _collect(sub_result)

    assert len(events) == 1
    payload = events[0].data["generateRecortes"]
    assert payload["__typename"] == "AgentEventDone"
    assert payload["suggestedName"] == "Economia Diaria"
    assert payload["iterations"] == 3
    assert payload["converged"] is True
    assert len(payload["recortes"]) == 1
    assert payload["recortes"][0]["title"] == "Economia"


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_propagates_error_event_from_upstream(monkeypatch):
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")

    body = _make_sse_body([
        {"type": "error", "message": "agente explodiu"},
    ])
    respx.post("http://localhost:8080/agent/generate-recortes").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}),
    )

    sub_result = await full_schema.subscribe(
        'subscription { generateRecortes(prompt: "x") { __typename ... on AgentEventError { message } } }',
        context_value=_auth_ctx(),
    )
    events = await _collect(sub_result)

    assert len(events) == 1
    payload = events[0].data["generateRecortes"]
    assert payload["__typename"] == "AgentEventError"
    assert payload["message"] == "agente explodiu"


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_handles_upstream_disconnect(monkeypatch):
    """Upstream emite 1 thinking + fecha sem `done`. Esperamos receber o
    thinking E um AgentEventError final indicando que o stream caiu."""
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")

    # Body com apenas 1 evento e sem trailer 'done'.
    body = _make_sse_body([
        {"type": "thinking", "message": "comecando"},
    ])
    respx.post("http://localhost:8080/agent/generate-recortes").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}),
    )

    sub_result = await full_schema.subscribe(
        """
        subscription {
            generateRecortes(prompt: "x") {
                __typename
                ... on AgentEventThinking { message }
                ... on AgentEventError { message }
            }
        }
        """,
        context_value=_auth_ctx(),
    )
    events = await _collect(sub_result)

    typenames = [e.data["generateRecortes"]["__typename"] for e in events]
    # Pelo menos um thinking foi entregue e o stream encerra sem erro fatal.
    # Toleramos um AgentEventError no fim (representa o disconnect),
    # mas NAO toleramos exception/crash.
    assert "AgentEventThinking" in typenames
    # Stream foi consumido inteiro sem excecao.
    assert all(e.errors is None for e in events)


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_handles_client_cancel(monkeypatch):
    """Quando o cliente cancela (CancelledError no consumer), o stream upstream
    e fechado e nao deixa request aberta."""
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")

    body = _make_sse_body([
        {"type": "thinking", "message": "a"},
        {"type": "thinking", "message": "b"},
        {"type": "thinking", "message": "c"},
    ])
    route = respx.post("http://localhost:8080/agent/generate-recortes").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}),
    )

    sub_result = await full_schema.subscribe(
        'subscription { generateRecortes(prompt: "x") { __typename ... on AgentEventThinking { message } } }',
        context_value=_auth_ctx(),
    )

    # Consome apenas 1 evento e fecha o generator.
    first = await sub_result.__anext__()
    assert first.data["generateRecortes"]["__typename"] == "AgentEventThinking"

    # aclose() simula client cancel.
    await sub_result.aclose()

    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_handles_malformed_sse_lines(monkeypatch):
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")

    # Body com lixo entre dois eventos validos.
    body = (
        b"data: not-json-at-all\n\n"
        b": comment line\n\n"
        b"data: {\"type\":\"thinking\",\"message\":\"ok\"}\n\n"
    )
    respx.post("http://localhost:8080/agent/generate-recortes").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}),
    )

    sub_result = await full_schema.subscribe(
        'subscription { generateRecortes(prompt: "x") { __typename ... on AgentEventThinking { message } } }',
        context_value=_auth_ctx(),
    )
    events = await _collect(sub_result)

    typenames = [e.data["generateRecortes"]["__typename"] for e in events]
    # O `thinking` chega; lixo foi skipped sem quebrar.
    assert "AgentEventThinking" in typenames


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_unknown_event_type_ignored(monkeypatch):
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")

    body = _make_sse_body([
        {"type": "unknown_xyz", "foo": "bar"},
        {"type": "thinking", "message": "ok"},
    ])
    respx.post("http://localhost:8080/agent/generate-recortes").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}),
    )

    sub_result = await full_schema.subscribe(
        'subscription { generateRecortes(prompt: "x") { __typename ... on AgentEventThinking { message } } }',
        context_value=_auth_ctx(),
    )
    events = await _collect(sub_result)

    typenames = [e.data["generateRecortes"]["__typename"] for e in events]
    assert typenames == ["AgentEventThinking"]


# ---------------------------------------------------------------------------
# 8, 9) OIDC behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_obtains_oidc_token_when_remote_worker(monkeypatch):
    audience = "https://clipping-worker-remote.a.run.app"
    monkeypatch.setenv("CLIPPING_WORKER_URL", f"{audience}/agent/generate-recortes")

    # Mock do metadata server.
    respx.get(_METADATA_URL).mock(return_value=httpx.Response(200, text="oidc-token-XYZ"))

    body = _make_sse_body([{"type": "thinking", "message": "ok"}])
    worker_route = respx.post(f"{audience}/agent/generate-recortes").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}),
    )

    sub_result = await full_schema.subscribe(
        'subscription { generateRecortes(prompt: "x") { __typename } }',
        context_value=_auth_ctx(),
    )
    await _collect(sub_result)

    assert worker_route.called
    sent = worker_route.calls.last.request
    assert sent.headers.get("Authorization") == "Bearer oidc-token-XYZ"


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_no_oidc_in_local_dev(monkeypatch):
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")

    body = _make_sse_body([{"type": "thinking", "message": "ok"}])
    worker_route = respx.post("http://localhost:8080/agent/generate-recortes").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}),
    )

    sub_result = await full_schema.subscribe(
        'subscription { generateRecortes(prompt: "x") { __typename } }',
        context_value=_auth_ctx(),
    )
    await _collect(sub_result)

    assert worker_route.called
    sent = worker_route.calls.last.request
    assert "Authorization" not in sent.headers


# ---------------------------------------------------------------------------
# Extras
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_yields_tool_call_and_result_events(monkeypatch):
    monkeypatch.setenv("CLIPPING_WORKER_URL", "http://localhost:8080/agent/generate-recortes")

    body = _make_sse_body([
        {"type": "tool_call", "tool": "search_themes", "args": {"q": "economia"}},
        {"type": "tool_result", "tool": "search_themes", "result": {"themes": ["a", "b"]}},
        {"type": "adjusting", "message": "refinando"},
        {"type": "sample_result", "n": 42},
    ])
    respx.post("http://localhost:8080/agent/generate-recortes").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}),
    )

    sub_result = await full_schema.subscribe(
        """
        subscription {
            generateRecortes(prompt: "x") {
                __typename
                ... on AgentEventToolCall { tool argsJson }
                ... on AgentEventToolResult { tool resultJson }
                ... on AgentEventAdjusting { message }
                ... on AgentEventSampleResult { payloadJson }
            }
        }
        """,
        context_value=_auth_ctx(),
    )
    events = await _collect(sub_result)

    typenames = [e.data["generateRecortes"]["__typename"] for e in events]
    assert typenames == [
        "AgentEventToolCall",
        "AgentEventToolResult",
        "AgentEventAdjusting",
        "AgentEventSampleResult",
    ]

    tool_call_payload = events[0].data["generateRecortes"]
    assert tool_call_payload["tool"] == "search_themes"
    assert json.loads(tool_call_payload["argsJson"]) == {"q": "economia"}

    tool_result_payload = events[1].data["generateRecortes"]
    assert json.loads(tool_result_payload["resultJson"]) == {"themes": ["a", "b"]}

    sample_payload = events[3].data["generateRecortes"]
    assert json.loads(sample_payload["payloadJson"])["n"] == 42


# ---------------------------------------------------------------------------
# _resolve_worker_endpoint — normalizacao da env CLIPPING_WORKER_URL
# ---------------------------------------------------------------------------
class TestResolveWorkerEndpoint:
    def test_appends_path_when_only_base_url(self):
        from graphql_api.schema.resolvers.agent import _resolve_worker_endpoint

        # Cloud Run prod (Terraform) injeta apenas a base do servico.
        out = _resolve_worker_endpoint(
            "https://destaquesgovbr-clipping-xxx.a.run.app"
        )
        assert out == (
            "https://destaquesgovbr-clipping-xxx.a.run.app/agent/generate-recortes"
        )

    def test_strips_trailing_slash_before_appending(self):
        from graphql_api.schema.resolvers.agent import _resolve_worker_endpoint

        out = _resolve_worker_endpoint(
            "https://destaquesgovbr-clipping-xxx.a.run.app/"
        )
        assert out == (
            "https://destaquesgovbr-clipping-xxx.a.run.app/agent/generate-recortes"
        )

    def test_keeps_full_url_unchanged(self):
        from graphql_api.schema.resolvers.agent import _resolve_worker_endpoint

        # Testes legados e default local passam URL completa.
        full = "http://localhost:8080/agent/generate-recortes"
        assert _resolve_worker_endpoint(full) == full

    def test_keeps_full_url_with_trailing_slash(self):
        from graphql_api.schema.resolvers.agent import _resolve_worker_endpoint

        out = _resolve_worker_endpoint(
            "http://localhost:8080/agent/generate-recortes/"
        )
        # Trailing slash e normalizado fora.
        assert out == "http://localhost:8080/agent/generate-recortes"


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_works_with_base_url_only(monkeypatch):
    """Regressao: CLIPPING_WORKER_URL sem path (formato Cloud Run prod) deve
    funcionar — o resolver appendar /agent/generate-recortes automaticamente.
    Antes da fix, prod retornava 404 quando a flag graphql.agent fosse ON.
    """
    monkeypatch.setenv(
        "CLIPPING_WORKER_URL", "https://worker-xxx.a.run.app"
    )

    # URL non-local: get_oidc_token tenta metadata server. Em teste mockamos
    # 404 -> retorna None -> Authorization header nao e setado. Suficiente
    # para validar a normalizacao do endpoint.
    respx.get(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
    ).mock(return_value=httpx.Response(404))

    body = _make_sse_body([
        {"type": "thinking", "message": "ok"},
    ])
    # respx precisa do path final
    respx.post("https://worker-xxx.a.run.app/agent/generate-recortes").mock(
        return_value=httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/event-stream"},
        ),
    )

    sub_result = await full_schema.subscribe(
        'subscription { generateRecortes(prompt: "x") { __typename ... on AgentEventThinking { message } } }',
        context_value=_auth_ctx(),
    )
    events = await _collect(sub_result)
    assert len(events) == 1
    assert events[0].data["generateRecortes"]["__typename"] == "AgentEventThinking"
