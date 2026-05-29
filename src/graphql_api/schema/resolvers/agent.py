"""Resolver da subscription `generateRecortes` (Fase A6 — passthrough SSE).

A subscription se conecta ao `clipping/` worker via HTTP POST com
`text/event-stream` e relaya cada `data: {...}` como uma variante de
`AgentEvent`. O agente fica no worker (decisao D0 do plano), nao aqui.

Em producao, obtemos um OIDC token via metadata server para se autenticar
no worker Cloud Run. Em dev local (`localhost`), nao ha header
`Authorization`.

Erros e disconnects upstream sao convertidos em `AgentEventError` para
manter o contrato com o cliente (a stream sempre fecha gracefully).
"""

from __future__ import annotations

import json
import logging
import os
from typing import AsyncGenerator

import httpx
import strawberry
from strawberry.types import Info

from graphql_api.auth.guards import IsAuthenticated
from graphql_api.datasources.firestore import RecorteData
from graphql_api.lib.oidc import get_oidc_token
from graphql_api.schema.types.agent import (
    AgentEvent,
    AgentEventAdjusting,
    AgentEventDone,
    AgentEventError,
    AgentEventSampleResult,
    AgentEventThinking,
    AgentEventToolCall,
    AgentEventToolResult,
)
from graphql_api.schema.types.clipping import Recorte

logger = logging.getLogger(__name__)

_DEFAULT_WORKER_URL = "http://localhost:8080/agent/generate-recortes"


def _audience_from_worker_url(url: str) -> str:
    """O audience OIDC e o origin do servico Cloud Run.

    `https://worker-xyz.a.run.app/agent/generate-recortes`
        -> `https://worker-xyz.a.run.app`
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_event(line: str) -> dict | None:
    """Parseia uma linha SSE `data: {...}`. Retorna None se invalida."""
    if not line.startswith("data:"):
        return None
    payload = line[len("data:"):].strip()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        logger.debug("malformed SSE data line skipped: %r", payload[:200])
        return None


def _to_agent_event(evt: dict) -> AgentEvent | None:
    """Mapeia um evento upstream para uma variante de `AgentEvent`."""
    etype = evt.get("type")
    if etype == "thinking":
        return AgentEventThinking(message=str(evt.get("message", "")))
    if etype == "tool_call":
        return AgentEventToolCall(
            tool=str(evt.get("tool", "")),
            args_json=json.dumps(evt.get("args", {})),
        )
    if etype == "tool_result":
        return AgentEventToolResult(
            tool=str(evt.get("tool", "")),
            result_json=json.dumps(evt.get("result", {})),
        )
    if etype == "sample_result":
        payload = {k: v for k, v in evt.items() if k != "type"}
        return AgentEventSampleResult(payload_json=json.dumps(payload))
    if etype == "adjusting":
        return AgentEventAdjusting(message=str(evt.get("message", "")))
    if etype == "done":
        result = evt.get("result") or {}
        recortes_raw = result.get("recortes") or []
        recortes: list[Recorte] = []
        for r in recortes_raw:
            try:
                # Recorte e gerado via strawberry.experimental.pydantic.type
                # a partir de RecorteData, entao precisamos da factory pydantic.
                pyd = RecorteData.model_validate(r)
                recortes.append(Recorte.from_pydantic(pyd))
            except Exception as e:
                logger.warning("failed to parse recorte: %s", e)
                continue
        return AgentEventDone(
            recortes=recortes,
            explanation=str(result.get("explanation", "")),
            description=str(result.get("description", "")),
            suggested_name=str(result.get("suggested_name", "")),
            iterations=int(result.get("iterations", 0)),
            converged=bool(result.get("converged", False)),
        )
    if etype == "error":
        return AgentEventError(message=str(evt.get("message", "")))
    logger.info("unknown agent event type ignored: %r", etype)
    return None


_ENDPOINT_PATH = "/agent/generate-recortes"


def _resolve_worker_endpoint(raw: str) -> str:
    """Normaliza `CLIPPING_WORKER_URL` para o endpoint completo.

    Cloud Run (via Terraform) injeta apenas a base URL do servico
    (`https://destaquesgovbr-clipping-*.run.app`); testes legados passam
    a URL completa com `/agent/generate-recortes`. Aceitamos ambos:
    se a URL ja termina com o path, mantemos; senao appendamos.
    """
    stripped = raw.rstrip("/")
    if stripped.endswith(_ENDPOINT_PATH):
        return stripped
    return stripped + _ENDPOINT_PATH


async def _stream_upstream(prompt: str) -> AsyncGenerator[AgentEvent, None]:
    """Conecta ao worker via SSE e emite `AgentEvent`s."""
    worker_url = _resolve_worker_endpoint(
        os.getenv("CLIPPING_WORKER_URL", _DEFAULT_WORKER_URL)
    )
    audience = _audience_from_worker_url(worker_url)

    headers = {"Accept": "text/event-stream"}
    token = await get_oidc_token(audience)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = {"prompt": prompt, "stream": True}

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", worker_url, headers=headers, json=body
            ) as resp:
                if resp.status_code != 200:
                    yield AgentEventError(
                        message=f"upstream returned status {resp.status_code}",
                    )
                    return

                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    evt = _parse_event(line)
                    if evt is None:
                        continue
                    out = _to_agent_event(evt)
                    if out is None:
                        continue
                    yield out
    except httpx.HTTPError as e:
        logger.warning("upstream stream failed: %s", e)
        yield AgentEventError(message=f"upstream disconnected: {e}")


@strawberry.type
class AgentSubscription:
    @strawberry.subscription(
        description=(
            "Gera recortes em tempo real via agente LLM. Passthrough do SSE "
            "do worker clipping. Requer autenticacao."
        ),
        permission_classes=[IsAuthenticated],
    )
    async def generate_recortes(
        self, info: Info, prompt: str
    ) -> AsyncGenerator[AgentEvent, None]:
        async for event in _stream_upstream(prompt):
            yield event
