import re
from typing import Optional

import strawberry
from strawberry.types import Info

from graphql_api.auth.guards import IsAuthenticated
from graphql_api.datasources.firestore import ClippingData, UnauthorizedError
from graphql_api.lib.cron import is_valid_cron
from graphql_api.schema.types.clipping import (
    Clipping,
    ClippingInput,
    DeliveryChannels,
    DeliveryChannelsInput,
    EstimateResult,
    Recorte,
    SubscribeInput,
    UserSubscription,
    user_subscription_from_data,
)

# Regex pragmático para email — mesmo conjunto usado em pyfilters; não pretende
# ser RFC 5322 completo, só rejeitar lixo óbvio antes de cair no datasource.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Limite arbitrário do plano A4: cada clipping pode endereçar até 20 emails
# externos. Mais que isso vira lista de distribuição (fora do escopo).
_MAX_EXTRA_EMAILS = 20


def _to_graphql_clipping(data: ClippingData) -> Clipping:
    recortes = []
    for r in data.recortes:
        recortes.append(
            Recorte(
                id=r.get("id", ""),
                title=r.get("title", ""),
                themes=r.get("themes", []),
                agencies=r.get("agencies", []),
                keywords=r.get("keywords", []),
            )
        )

    delivery_channels = None
    if data.delivery_channels:
        delivery_channels = DeliveryChannels(
            email=data.delivery_channels.get("email", False),
            telegram=data.delivery_channels.get("telegram", False),
            push=data.delivery_channels.get("push", False),
        )

    return Clipping(
        id=data.id,
        name=data.name,
        description=data.description,
        recortes=recortes,
        prompt=data.prompt,
        schedule=data.schedule or "",
        schedule_time=data.schedule_time,
        next_run_at=data.next_run_at,
        start_date=data.start_date,
        end_date=data.end_date,
        extra_emails=list(data.extra_emails or []),
        include_history=bool(data.include_history),
        delivery_channels=delivery_channels,
        active=data.active,
        created_at=data.created_at,
        updated_at=data.updated_at,
        _author_user_id=data.author_user_id,
    )


def _validate_schedule_input(input: ClippingInput) -> None:
    """Valida campos cron-relacionados do input. Lança ValueError com mensagem
    voltada ao usuário; o resolver re-emite como erro GraphQL.
    """
    if not is_valid_cron(input.schedule):
        raise ValueError(
            f"schedule inválido: '{input.schedule}' não é uma expressão cron de 5 campos"
        )

    if input.extra_emails:
        if len(input.extra_emails) > _MAX_EXTRA_EMAILS:
            raise ValueError(
                f"extraEmails excede o limite máximo de {_MAX_EXTRA_EMAILS} endereços"
            )
        for email in input.extra_emails:
            if not _EMAIL_RE.match(email):
                raise ValueError(f"email inválido em extraEmails: '{email}'")


def _input_to_dict(input: ClippingInput) -> dict:
    recortes = []
    for r in input.recortes:
        recortes.append(
            {
                "id": "",
                "title": r.title,
                "themes": r.themes,
                "agencies": r.agencies,
                "keywords": r.keywords,
            }
        )

    result: dict = {
        "name": input.name,
        "description": input.description,
        "recortes": recortes,
        "prompt": input.prompt,
        "schedule": input.schedule,
        "schedule_time": input.schedule_time,
        "start_date": input.start_date,
        "end_date": input.end_date,
        "extra_emails": list(input.extra_emails or []),
        "include_history": bool(input.include_history) if input.include_history is not None else False,
    }

    if input.delivery_channels is not None:
        result["delivery_channels"] = {
            "email": input.delivery_channels.email,
            "telegram": input.delivery_channels.telegram,
            "push": input.delivery_channels.push,
        }

    return result


def _channels_input_to_dict(channels: DeliveryChannelsInput) -> dict:
    """Converte `DeliveryChannelsInput` para dict (canônico no Firestore).

    Inclui `webhook=False` por compat com modelo de subscription (A5 vai
    expor `webhook` no input; por enquanto sempre False).
    """
    return {
        "email": channels.email,
        "telegram": channels.telegram,
        "push": channels.push,
        "webhook": False,
    }


@strawberry.type
class ClippingQuery:
    @strawberry.field(
        description="Lista todos os clippings do usuario autenticado (autorados + inscritos)",
        permission_classes=[IsAuthenticated],
    )
    def clippings(self, info: Info) -> list[Clipping]:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id
        # A2: get_my_clippings retorna [(ClippingData, SubscriptionData)].
        # A3: `isAuthor` e `mySubscription` resolvem via campos contextuais.
        results = ds.get_my_clippings(user_id)
        return [_to_graphql_clipping(r.clipping) for r in results]

    @strawberry.field(
        description="Busca um clipping por ID",
        permission_classes=[IsAuthenticated],
    )
    def clipping(self, info: Info, id: str) -> Optional[Clipping]:
        ctx = info.context
        ds = ctx.firestore_ds
        # A2: get_clipping é top-level, não exige user_id no path.
        # Autorização granular fica para A3 quando expusermos mySubscription.
        data = ds.get_clipping(id)
        if data is None:
            return None
        return _to_graphql_clipping(data)

    @strawberry.field(
        description="Estima o numero de artigos para um clipping",
        permission_classes=[IsAuthenticated],
    )
    def clipping_estimate(
        self,
        info: Info,
        themes: list[str] = [],
        agencies: list[str] = [],
        keywords: list[str] = [],
    ) -> EstimateResult:
        # Placeholder: return a mock estimate based on filter count
        total = len(themes) * 10 + len(agencies) * 5 + len(keywords) * 3
        return EstimateResult(total_estimate=max(total, 0))


@strawberry.type
class ClippingMutation:
    @strawberry.mutation(
        description="Cria um novo clipping",
        permission_classes=[IsAuthenticated],
    )
    def create_clipping(self, info: Info, input: ClippingInput) -> Clipping:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id
        # A4: validar schedule + extraEmails antes de tocar o datasource.
        _validate_schedule_input(input)
        data = _input_to_dict(input)
        result = ds.create_clipping(user_id, data)
        return _to_graphql_clipping(result)

    @strawberry.mutation(
        description="Atualiza um clipping existente",
        permission_classes=[IsAuthenticated],
    )
    def update_clipping(self, info: Info, id: str, input: ClippingInput) -> Clipping:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id
        # A4: validar schedule + extraEmails antes de tocar o datasource.
        _validate_schedule_input(input)
        data = _input_to_dict(input)
        # Calcular nextRunAt *no resolver* também — assim o teste de mock
        # pode inspecionar `data["next_run_at"]` sem depender da
        # implementação real do datasource. O datasource também calcula
        # (idempotente).
        from datetime import datetime, timezone

        from graphql_api.lib.cron import calculate_next_run

        if data.get("schedule"):
            data["next_run_at"] = calculate_next_run(
                data["schedule"],
                datetime.now(tz=timezone.utc),
                start_date=data.get("start_date"),
                end_date=data.get("end_date"),
            )
        result = ds.update_clipping(user_id, id, data)
        return _to_graphql_clipping(result)

    @strawberry.mutation(
        description="Deleta um clipping",
        permission_classes=[IsAuthenticated],
    )
    def delete_clipping(self, info: Info, id: str) -> bool:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id
        return ds.delete_clipping(user_id, id)

    @strawberry.mutation(
        description="Envia um clipping manualmente",
        permission_classes=[IsAuthenticated],
    )
    def send_clipping(self, info: Info, id: str) -> bool:
        # Placeholder: would trigger the actual send pipeline
        return True

    # -- Subscription mutations (Fase A3) -----------------------------------
    @strawberry.mutation(
        description="Inscreve o usuário autenticado em um clipping (role=subscriber)",
        permission_classes=[IsAuthenticated],
    )
    def subscribe_to_clipping(self, info: Info, input: SubscribeInput) -> UserSubscription:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id
        sub = ds.subscribe_to_clipping(
            user_id=user_id,
            clipping_id=input.clipping_id,
            channels=_channels_input_to_dict(input.delivery_channels),
            extra_emails=list(input.extra_emails or []),
            webhook_url=input.webhook_url or "",
        )
        return user_subscription_from_data(sub)

    @strawberry.mutation(
        description="Remove a inscrição do usuário em um clipping",
        permission_classes=[IsAuthenticated],
    )
    def unsubscribe_from_clipping(self, info: Info, clipping_id: str) -> bool:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id
        try:
            return ds.unsubscribe_from_clipping(user_id, clipping_id)
        except UnauthorizedError as exc:
            # Autor não pode self-unsubscribe — propagar como erro GraphQL
            raise PermissionError(str(exc)) from exc

    @strawberry.mutation(
        description="Atualiza canais de entrega da inscrição do usuário",
        permission_classes=[IsAuthenticated],
    )
    def update_my_subscription(
        self,
        info: Info,
        clipping_id: str,
        channels: DeliveryChannelsInput,
        extra_emails: Optional[list[str]] = None,
        webhook_url: Optional[str] = None,
    ) -> Optional[UserSubscription]:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id
        sub = ds.update_my_subscription(
            user_id=user_id,
            clipping_id=clipping_id,
            channels=_channels_input_to_dict(channels),
            extra_emails=list(extra_emails or []),
            webhook_url=webhook_url or "",
        )
        if sub is None:
            return None
        return user_subscription_from_data(sub)
