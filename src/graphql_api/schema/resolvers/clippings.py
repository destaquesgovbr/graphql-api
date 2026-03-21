from typing import Optional

import strawberry
from strawberry.types import Info

from graphql_api.auth.guards import IsAuthenticated
from graphql_api.datasources.firestore import ClippingData
from graphql_api.schema.types.clipping import (
    Clipping,
    ClippingInput,
    DeliveryChannels,
    EstimateResult,
    Recorte,
)


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
        schedule_time=data.schedule_time,
        delivery_channels=delivery_channels,
        active=data.active,
        created_at=data.created_at,
        updated_at=data.updated_at,
    )


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
        "schedule_time": input.schedule_time,
    }

    if input.delivery_channels is not None:
        result["delivery_channels"] = {
            "email": input.delivery_channels.email,
            "telegram": input.delivery_channels.telegram,
            "push": input.delivery_channels.push,
        }

    return result


@strawberry.type
class ClippingQuery:
    @strawberry.field(
        description="Lista todos os clippings do usuario autenticado",
        permission_classes=[IsAuthenticated],
    )
    def clippings(self, info: Info) -> list[Clipping]:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id
        items = ds.get_clippings(user_id)
        return [_to_graphql_clipping(c) for c in items]

    @strawberry.field(
        description="Busca um clipping por ID",
        permission_classes=[IsAuthenticated],
    )
    def clipping(self, info: Info, id: str) -> Optional[Clipping]:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id
        data = ds.get_clipping(user_id, id)
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
        data = _input_to_dict(input)
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
