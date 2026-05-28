"""Entrypoint FastAPI da GraphQL API.

Queries/mutations sao expostas via `GraphQLRouter` (Strawberry) em
`/graphql`. Subscriptions sao expostas via um endpoint custom
`/graphql/stream` que implementa a spec graphql-sse (distinct connections
mode) — substituiu o transport WebSocket originalmente proposto na Fase
A6, fixup decidido apos avaliacao do cliente urql no portal (B1/B4) e do
formato natural de SSE do upstream (clipping worker).

Referencia da spec: https://github.com/enisdenjo/graphql-sse
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from strawberry.fastapi import GraphQLRouter

from graphql_api.context import GraphQLContext, get_context
from graphql_api.lifespan import lifespan
from graphql_api.schema import schema

logger = logging.getLogger(__name__)


async def get_graphql_context(request: Request) -> GraphQLContext:
    """Dependency override-able pelo FastAPI para injecao em testes.

    Em producao chama `get_context(request)` que:
      - le datasources de `request.app.state` (populados pelo lifespan);
      - valida JWT do header `Authorization` e popula `ctx.user`;
      - falha silenciosa em auth → queries publicas continuam respondendo.

    Em testes, `app.dependency_overrides[get_graphql_context] = factory` injeta
    um contexto com `user` populado sem precisar passar pelo lifespan/JWT.

    Compat com testes legados que mockam via `patch("graphql_api.app.get_context", mock)`
    onde `mock` nao aceita `request`: detectamos isso via `inspect.signature` e
    chamamos sem argumentos. Path normal (producao) chama com request.
    """
    import inspect

    fn = get_context
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # pragma: no cover - C-implementado, raro
        params = {}
    if params:
        return await fn(request)
    return await fn()


def _format_sse_event(event: str, data: str = "") -> str:
    """Formata um evento SSE conforme spec.

    `event: <event>\\ndata: <data>\\n\\n`. Quando `data` e vazio, mantem
    a linha `data:` (compativel com graphql-sse para `complete`).
    """
    return f"event: {event}\ndata: {data}\n\n"


def _serialize_errors(errors: Any) -> list[dict[str, Any]]:
    """Converte uma lista de erros GraphQL para o formato JSON da spec."""
    out: list[dict[str, Any]] = []
    if not errors:
        return out
    for e in errors:
        err: dict[str, Any] = {"message": str(getattr(e, "message", e))}
        path = getattr(e, "path", None)
        if path is not None:
            err["path"] = list(path)
        out.append(err)
    return out


async def _graphql_sse_stream(
    *,
    query: str | None,
    variables: dict[str, Any] | None,
    operation_name: str | None,
    context: GraphQLContext,
) -> AsyncIterator[str]:
    """Async generator que emite eventos SSE conforme graphql-sse spec.

    Distinct connections mode: 1 subscription por conexao. Emite eventos
    `next` (um por update) e termina com `complete`.
    """
    try:
        sub_result = await schema.subscribe(
            query or "",
            variable_values=variables or {},
            operation_name=operation_name,
            context_value=context,
        )

        if hasattr(sub_result, "__aiter__"):
            async for result in sub_result:
                payload: dict[str, Any] = {"data": result.data}
                errors = _serialize_errors(getattr(result, "errors", None))
                if errors:
                    payload["errors"] = errors
                yield _format_sse_event("next", json.dumps(payload))
        else:
            # Validacao/permissao falhou antes de abrir o async iterator.
            errors = _serialize_errors(getattr(sub_result, "errors", None))
            payload = {"data": None}
            if errors:
                payload["errors"] = errors
            yield _format_sse_event("next", json.dumps(payload))
    except Exception as exc:  # pragma: no cover - safety net
        logger.exception("graphql-sse stream failed: %s", exc)
        payload = {"data": None, "errors": [{"message": str(exc)}]}
        yield _format_sse_event("next", json.dumps(payload))
    finally:
        yield _format_sse_event("complete", "")


async def _parse_request_body(request: Request) -> dict[str, Any]:
    """Extrai `{query, variables, operationName}` de POST (JSON) ou GET (query string)."""
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        return body

    # GET — variables vem como JSON-encoded string.
    params = dict(request.query_params)
    variables = params.get("variables")
    if isinstance(variables, str):
        try:
            params["variables"] = json.loads(variables)
        except json.JSONDecodeError:
            params["variables"] = {}
    return params


def create_app() -> FastAPI:
    app = FastAPI(title="DGB GraphQL API", docs_url=None, redoc_url=None, lifespan=lifespan)

    # CORS middleware (gap-fix pre-rollout).
    #
    # `/graphql/stream` e consumido pelo portal (cross-origin no dev local;
    # mesma origem em prod). `/graphql` precisa estar acessivel para widgets
    # embarcaveis externos (iframes 3rd-party). Default `*` e seguro porque
    # o schema publico (sem JWT) so expoe queries read-only de
    # noticias/temas/agencias/widgets — mutations e queries autenticadas
    # exigem header `Authorization`. Em prod, infra pode setar
    # `CORS_ALLOW_ORIGINS=https://destaques.gov.br,https://staging.destaques.gov.br`
    # para restringir.
    cors_origins = [
        o.strip()
        for o in os.environ.get("CORS_ALLOW_ORIGINS", "*").split(",")
        if o.strip()
    ] or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
        expose_headers=["Cache-Control"],
    )

    # Strawberry usa o `context_getter` como FastAPI dep. Passar
    # `get_graphql_context` direto faz com que `dependency_overrides[get_graphql_context]`
    # tambem funcione no endpoint /graphql (antes so funcionava em /graphql/stream).
    # Queries/mutations apenas — subscriptions migradas para /graphql/stream.
    graphql_router = GraphQLRouter(schema, context_getter=get_graphql_context)
    app.include_router(graphql_router, prefix="/graphql")

    @app.api_route("/graphql/stream", methods=["GET", "POST"])
    async def graphql_sse_endpoint(
        request: Request,
        context: GraphQLContext = Depends(get_graphql_context),
    ) -> StreamingResponse:
        body = await _parse_request_body(request)
        query = body.get("query")
        variables = body.get("variables") or {}
        if isinstance(variables, str):
            try:
                variables = json.loads(variables)
            except json.JSONDecodeError:
                variables = {}
        operation_name = body.get("operationName")

        generator = _graphql_sse_stream(
            query=query,
            variables=variables,
            operation_name=operation_name,
            context=context,
        )

        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                # Desabilita buffering em proxies (Nginx/Cloud Run).
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok"})

    return app
