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

import json
from datetime import datetime
from enum import Enum
from typing import Optional

import strawberry
from strawberry.experimental.pydantic import type as pydantic_type
from strawberry.types import Info

from graphql_api.datasources.firestore import (
    DeliveryChannelsData,
    RecorteData,
    ReleaseData,
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
    webhook: bool = False


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
        webhook=bool(channels.get("webhook", False)),
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


# ---------------------------------------------------------------------------
# Release (Fase A5)
# ---------------------------------------------------------------------------
@pydantic_type(model=ReleaseData)
class Release:
    """Entrega historica de um clipping.

    Gerado a partir do `ReleaseData` Pydantic. Campos sao expostos em
    camelCase pelo Strawberry (conversao automatica de snake_case).
    `digest` (texto raw) e mantido fora do schema — apenas `digestHtml`
    (renderizado) e `digestPreview` (resumo curto computado) sao expostos.
    """

    id: strawberry.auto
    clipping_id: strawberry.auto
    clipping_name: strawberry.auto
    digest_html: strawberry.auto
    articles_count: strawberry.auto
    created_at: strawberry.auto
    release_url: strawberry.auto
    ref_time: strawberry.auto
    since_hours: strawberry.auto
    # Resumo curto (<=150 chars) derivado do `digest` raw. Campo explicito
    # (nao `strawberry.auto`): o `digest` raw nao e exposto no schema; so este
    # preview computado. Exposto como `digestPreview` (camelCase).
    digest_preview: Optional[str] = None
    # Recortes (filtros) do clipping fonte. So populado pelo resolver top-level
    # `release(id)`; nos outros contextos que retornam `Release`
    # (`clipping.releases`, `marketplaceListing.releases`) fica vazio — esses
    # consumidores nao selecionam o campo.
    recortes: list[Recorte] = strawberry.field(default_factory=list)
    # Id do listing de marketplace ATIVO do clipping fonte (para link "ver no
    # marketplace"); None se nao publicado. Tambem so populado pelo
    # `release(id)` top-level.
    marketplace_listing_id: Optional[str] = None


def _digest_preview(digest: str) -> str:
    """Deriva o resumo curto (<=150 chars) do `digest` raw.

    Replica EXATAMENTE a logica do portal SSR (`lib/release-utils.ts`):
    tenta `JSON.parse(digest).intro`; em qualquer falha, cai para o digest
    raw fatiado. Vazio -> "".
    """
    if not digest:
        return ""
    try:
        parsed = json.loads(digest) or {}
        intro = parsed.get("intro") or ""
        return intro[:150]
    except Exception:
        return digest[:150]


def release_from_data(data: ReleaseData) -> Release:
    """Constroi o tipo Strawberry `Release` a partir do Pydantic `ReleaseData`."""
    return Release(
        id=data.id,
        clipping_id=data.clipping_id,
        clipping_name=data.clipping_name,
        digest_html=data.digest_html,
        articles_count=data.articles_count,
        created_at=data.created_at,
        release_url=data.release_url,
        ref_time=data.ref_time,
        since_hours=data.since_hours,
        digest_preview=_digest_preview(data.digest),
    )


@strawberry.input
class SubscribeInput:
    """Input para `subscribeToClipping`."""

    clipping_id: str
    delivery_channels: DeliveryChannelsInput
    extra_emails: Optional[list[str]] = None
    webhook_url: Optional[str] = None


# ---------------------------------------------------------------------------
# Clipping (com campos contextuais A3 + cron A4)
# ---------------------------------------------------------------------------
@strawberry.type
class Clipping:
    id: str
    name: str
    description: Optional[str] = None
    recortes: list[Recorte] = strawberry.field(default_factory=list)
    prompt: Optional[str] = None

    # A4: cron + janela
    schedule: str = ""
    schedule_time: Optional[str] = None  # legacy (readable)
    next_run_at: Optional[datetime] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    extra_emails: list[str] = strawberry.field(default_factory=list)
    include_history: bool = False

    delivery_channels: Optional[DeliveryChannels] = None
    active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Identidade do autor + estado de marketplace (consumidos pelo portal para
    # badges e links). `author_user_id` é exposto publicamente; `is_author`
    # (abaixo) continua derivando do mesmo dado via `_author_user_id`.
    author_user_id: Optional[str] = None
    published_to_marketplace: bool = False
    marketplace_listing_id: Optional[str] = None

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

    @strawberry.field(
        description=(
            "Entregas historicas (releases) deste clipping. "
            "Ordenadas por createdAt desc. Apenas autor ou subscriber podem ver."
        )
    )
    async def releases(
        self,
        info: Info,
        limit: int = 20,
        before: Optional[datetime] = None,
    ) -> list["Release"]:
        ctx = info.context
        user = getattr(ctx, "user", None)
        if user is None:
            raise PermissionError("UNAUTHENTICATED: login obrigatorio para ver releases")

        # Autorizacao: autor pelo `_author_user_id` OU subscriber (via
        # subscription_loader). Se nao for nenhum dos dois, nega.
        is_author = (
            self._author_user_id is not None and user.id == self._author_user_id
        )
        is_subscriber = False
        if not is_author:
            loader = getattr(ctx, "subscription_loader", None)
            if loader is not None:
                sub = await loader.load((user.id, self.id))
                is_subscriber = sub is not None
        if not (is_author or is_subscriber):
            raise PermissionError(
                "FORBIDDEN: apenas autor ou subscribers podem ver releases"
            )

        # Clamp limit no intervalo [1, 100] para evitar abuso.
        safe_limit = max(1, min(int(limit), 100))

        releases_loader = getattr(ctx, "releases_loader", None)
        if releases_loader is None:
            # Fallback direto ao datasource (testes sem context dataloader).
            ds = getattr(ctx, "firestore_ds", None)
            if ds is None:
                return []
            data_list = ds.get_releases(self.id, limit=safe_limit, before=before)
        else:
            data_list = await releases_loader.load(
                (self.id, safe_limit, before)
            )

        return [release_from_data(d) for d in data_list]


@strawberry.input
class ClippingInput:
    """Input do `createClipping` / `updateClipping`.

    Fase A4: `schedule` é **obrigatório**, expresso como cron de 5 campos.
    `nextRunAt` NÃO aparece aqui — é calculado pelo backend a partir de
    `schedule` + `startDate` + `endDate`.
    """

    name: str
    schedule: str
    description: Optional[str] = None
    recortes: list[RecorteInput] = strawberry.field(default_factory=list)
    prompt: Optional[str] = None
    schedule_time: Optional[str] = None  # legacy, opcional
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    extra_emails: Optional[list[str]] = None
    include_history: Optional[bool] = None
    delivery_channels: Optional[DeliveryChannelsInput] = None


@strawberry.type
class EstimateResult:
    total_estimate: int
