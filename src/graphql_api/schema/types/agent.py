"""Tipos GraphQL para a subscription `generateRecortes` (Fase A6).

A subscription faz passthrough do SSE upstream do `clipping/` worker — o
agente nao roda no graphql-api (decisao arquitetural D0 do
PLANO-ATUALIZACAO-v2.md §3). Cada `data: {...}` do upstream e mapeado
em uma variante do union `AgentEvent`.

Convencao: payloads arbitrarios (`args`, `result`, `sample_result`) ficam
como JSON strings (`*_json`) para evitar explosao de tipos no schema. O
cliente parsea no front-end.
"""

from __future__ import annotations

from typing import Annotated, Union

import strawberry

from graphql_api.schema.types.clipping import Recorte


@strawberry.type
class AgentEventThinking:
    message: str


@strawberry.type
class AgentEventToolCall:
    tool: str
    args_json: str


@strawberry.type
class AgentEventToolResult:
    tool: str
    result_json: str


@strawberry.type
class AgentEventSampleResult:
    payload_json: str


@strawberry.type
class AgentEventAdjusting:
    message: str


@strawberry.type
class AgentEventDone:
    recortes: list[Recorte]
    explanation: str
    description: str
    suggested_name: str
    iterations: int
    converged: bool


@strawberry.type
class AgentEventError:
    message: str


AgentEvent = Annotated[
    Union[
        AgentEventThinking,
        AgentEventToolCall,
        AgentEventToolResult,
        AgentEventSampleResult,
        AgentEventAdjusting,
        AgentEventDone,
        AgentEventError,
    ],
    strawberry.union("AgentEvent"),
]
