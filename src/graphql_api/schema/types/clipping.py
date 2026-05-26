"""Tipos Strawberry para clipping.

Fase A1: `DeliveryChannels` e `Recorte` sao gerados a partir dos modelos
Pydantic `DeliveryChannelsData` e `RecorteData` em
`graphql_api.datasources.firestore` via
`strawberry.experimental.pydantic.type`. Strawberry continua expondo nomes
camelCase no schema GraphQL (default), derivados de campos Python snake_case.

Fase A3: adiciona `SubscriptionRole`, `UserSubscription` e os campos
contextuais `isAuthor` / `mySubscription` em `Clipping`.

`UserSubscription` é gerado a partir de `SubscriptionData` (modelo Pydantic
em `datasources.firestore`). O campo `role` é mapeado para o enum Strawberry
`SubscriptionRole` (Pydantic guarda string `"author"|"subscriber"`; a
conversão acontece no construtor).
"""

from datetime import datetime
from enum import Enum
from typing import Optional

import strawberry
from strawberry.experimental.pydantic import type as pydantic_type
from strawberry.types import Info

from graphql_api.datasources.firestore import (
    DeliveryChannelsData,
    RecorteData,
    SubscriptionData,
)


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


# ---------------------------------------------------------------------------
# Subscription model (Fase A3)
# ---------------------------------------------------------------------------
@strawberry.enum
class SubscriptionRole(Enum):
    """Distingue o autor de um clipping dos seus subscribers."""

    AUTHOR = "author"
    SUBSCRIBER = "subscriber"


@pydantic_type(model=SubscriptionData)
class UserSubscription:
    """Subscription do usuário a um clipping (top-level `subscriptions/{id}`).

    Nomeada `UserSubscription` no schema GraphQL para evitar colidir com
    `strawberry.Subscription` (root type das subscriptions GraphQL, planejado
    para A6 — agent passthrough SSE).

    `delivery_channels` é tipado como `DeliveryChannels` (object type). O
    Pydantic guarda como dict — o resolver constrói o objeto antes de
    instanciar `UserSubscription` via `from_subscription_data` abaixo.
    """

    id: strawberry.auto
    clipping_id: strawberry.auto
    user_id: strawberry.auto
    role: SubscriptionRole
    delivery_channels: DeliveryChannels
    extra_emails: strawberry.auto
    webhook_url: Optional[str] = None
    active: strawberry.auto
    subscribed_at: strawberry.auto


def user_subscription_from_data(sub: SubscriptionData) -> UserSubscription:
    """Constrói `UserSubscription` (Strawberry) a partir de `SubscriptionData`.

    Coerce do dict `delivery_channels` para `DeliveryChannels` Strawberry e
    string `role` para `SubscriptionRole` enum.

    `webhook_url` Pydantic é `str` (default ""); no schema é nullable —
    convertemos `""` para `None` (mais ergonômico no cliente).
    """
    channels = sub.delivery_channels or {}
    dc = DeliveryChannels(
        email=bool(channels.get("email", False)),
        telegram=bool(channels.get("telegram", False)),
        push=bool(channels.get("push", False)),
    )
    role = SubscriptionRole(sub.role)
    webhook = sub.webhook_url if sub.webhook_url else None
    return UserSubscription(
        id=sub.id,
        clipping_id=sub.clipping_id,
        user_id=sub.user_id,
        role=role,
        delivery_channels=dc,
        extra_emails=list(sub.extra_emails),
        webhook_url=webhook,
        active=sub.active,
        subscribed_at=sub.subscribed_at,
    )


@strawberry.input
class SubscribeInput:
    """Input para `subscribeToClipping`."""

    clipping_id: str
    delivery_channels: DeliveryChannelsInput
    extra_emails: Optional[list[str]] = None
    webhook_url: Optional[str] = None


# ---------------------------------------------------------------------------
# Clipping (com campos contextuais A3)
# ---------------------------------------------------------------------------
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

    # Campo interno (não exposto): user_id do autor. Necessário para
    # `is_author` resolver. Strawberry só usa annotations decoradas;
    # `strawberry.field(init=True, ...)` mantém ele acessível ao código mas
    # *também* o expõe. Para mantê-lo "private" do schema, prefixamos com `_`
    # — strawberry ignora campos com underscore. Usamos `Private` para garantir.
    _author_user_id: strawberry.Private[Optional[str]] = None

    @strawberry.field(
        description="True se o usuário autenticado é o autor deste clipping."
    )
    def is_author(self, info: Info) -> bool:
        ctx = info.context
        user = getattr(ctx, "user", None)
        if user is None or self._author_user_id is None:
            return False
        return user.id == self._author_user_id

    @strawberry.field(
        description="Subscription do usuário autenticado para este clipping (None se não inscrito)."
    )
    async def my_subscription(self, info: Info) -> Optional[UserSubscription]:
        ctx = info.context
        user = getattr(ctx, "user", None)
        if user is None:
            return None
        loader = getattr(ctx, "subscription_loader", None)
        if loader is None:
            return None
        sub = await loader.load((user.id, self.id))
        if sub is None:
            return None
        return user_subscription_from_data(sub)


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
