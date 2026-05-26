"""Tipos Strawberry para clipping.

Fase A1: `DeliveryChannels` e `Recorte` sao gerados a partir dos modelos
Pydantic `DeliveryChannelsData` e `RecorteData` em
`graphql_api.datasources.firestore` via
`strawberry.experimental.pydantic.type`. Strawberry continua expondo nomes
camelCase no schema GraphQL (default), derivados de campos Python snake_case.

`Clipping` continua sendo declarado como `@strawberry.type` por enquanto:
- `ClippingData.recortes` e `list[dict]` (mocks legados) e
- `ClippingData.delivery_channels` e `Optional[dict]`

Migrar `Clipping` para `@pydantic_type` exige unificar todos esses dicts em
`RecorteData`/`DeliveryChannelsData` no datasource (e nos ~30 mocks dos
resolvers), trabalho que pertence a Fase A2/A3.

Os tipos de entrada (`ClippingInput`, `DeliveryChannelsInput`, `RecorteInput`)
permanecem dataclasses Strawberry puros — sao construidos pelos clientes, nao
por leitura do Firestore.
"""

from datetime import datetime
from typing import Optional

import strawberry
from strawberry.experimental.pydantic import type as pydantic_type

from graphql_api.datasources.firestore import DeliveryChannelsData, RecorteData


@pydantic_type(model=DeliveryChannelsData, all_fields=True)
class DeliveryChannels:
    pass


@strawberry.input
class DeliveryChannelsInput:
    email: bool = False
    telegram: bool = False
    push: bool = False


@pydantic_type(model=RecorteData, all_fields=True)
class Recorte:
    pass


@strawberry.input
class RecorteInput:
    title: str
    themes: list[str] = strawberry.field(default_factory=list)
    agencies: list[str] = strawberry.field(default_factory=list)
    keywords: list[str] = strawberry.field(default_factory=list)


@strawberry.type
class Clipping:
    id: str
    name: str
    description: Optional[str] = None
    recortes: list[Recorte] = strawberry.field(default_factory=list)
    prompt: Optional[str] = None
    schedule_time: Optional[str] = None
    delivery_channels: Optional[DeliveryChannels] = None
    active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@strawberry.input
class ClippingInput:
    name: str
    description: Optional[str] = None
    recortes: list[RecorteInput] = strawberry.field(default_factory=list)
    prompt: Optional[str] = None
    schedule_time: Optional[str] = None
    delivery_channels: Optional[DeliveryChannelsInput] = None


@strawberry.type
class EstimateResult:
    total_estimate: int
