from datetime import datetime
from typing import Optional

import strawberry


@strawberry.type
class DeliveryChannels:
    email: bool = False
    telegram: bool = False
    push: bool = False


@strawberry.input
class DeliveryChannelsInput:
    email: bool = False
    telegram: bool = False
    push: bool = False


@strawberry.type
class Recorte:
    id: str
    title: str
    themes: list[str]
    agencies: list[str]
    keywords: list[str]


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
