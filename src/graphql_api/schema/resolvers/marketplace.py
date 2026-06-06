from typing import Optional

import strawberry
from strawberry.types import Info

from graphql_api.auth.guards import IsAuthenticated
from graphql_api.datasources.firestore import ClippingData, MarketplaceListingData
from graphql_api.schema.resolvers.clippings import _to_graphql_clipping
from graphql_api.schema.types.clipping import Clipping
from graphql_api.schema.types.marketplace import (
    MarketplaceListing,
    MarketplaceListingsResult,
    MarketplaceRecorte,
    PublishInput,
)

PAGE_SIZE = 20


def _doc_to_listing(
    data: dict, user_id: Optional[str] = None, ds=None
) -> MarketplaceListing:
    # Boundary camelCase <-> snake_case via Pydantic. `model_validate` aceita
    # ambos os formatos por causa do `populate_by_name=True` em
    # `MarketplaceListingData`. O `id` precisa estar no dict para validar o
    # modelo, mas a maioria dos call-sites ja inclui — fallback para "" se
    # ausente (compat com fixtures legados).
    payload = {**(data or {})}
    payload.setdefault("id", payload.get("id", ""))
    listing_data = MarketplaceListingData.model_validate(payload)

    recortes = [
        MarketplaceRecorte(
            id=r.get("id", ""),
            title=r.get("title", ""),
            themes=r.get("themes", []),
            agencies=r.get("agencies", []),
            keywords=r.get("keywords", []),
        )
        for r in listing_data.recortes
    ]

    has_liked: Optional[bool] = None
    has_followed: Optional[bool] = None

    if user_id is not None and ds is not None:
        has_liked = ds.has_liked_listing(user_id, listing_data.id)
        has_followed = ds.has_followed_listing(user_id, listing_data.id)

    return MarketplaceListing(
        id=listing_data.id,
        author_user_id=listing_data.author_user_id,
        author_display_name=listing_data.author_display_name,
        source_clipping_id=listing_data.source_clipping_id,
        name=listing_data.name,
        description=listing_data.description,
        recortes=recortes,
        prompt=listing_data.prompt,
        schedule=listing_data.schedule,
        like_count=listing_data.like_count,
        follower_count=listing_data.follower_count,
        clone_count=listing_data.clone_count,
        published_at=listing_data.published_at,
        updated_at=listing_data.updated_at,
        active=listing_data.active,
        has_liked=has_liked,
        has_followed=has_followed,
    )


@strawberry.type
class MarketplaceQuery:
    @strawberry.field(description="Lista listings do marketplace com paginacao")
    def marketplace_listings(
        self, info: Info, page: int = 1
    ) -> MarketplaceListingsResult:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = getattr(ctx, "user", None)
        user_id = user_id.id if user_id is not None else None

        offset = (page - 1) * PAGE_SIZE
        result = ds.get_marketplace_listings(offset=offset, limit=PAGE_SIZE)
        listings_data = result.get("listings", [])
        total = result.get("total", 0)

        listings = [_doc_to_listing(d, user_id=user_id, ds=ds) for d in listings_data]
        return MarketplaceListingsResult(listings=listings, total=total)

    @strawberry.field(description="Busca um listing do marketplace por ID")
    def marketplace_listing(
        self, info: Info, id: str
    ) -> Optional[MarketplaceListing]:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = getattr(ctx, "user", None)
        user_id = user_id.id if user_id is not None else None

        data = ds.get_marketplace_listing(id)
        if data is None:
            return None
        return _doc_to_listing(data, user_id=user_id, ds=ds)


@strawberry.type
class MarketplaceMutation:
    @strawberry.mutation(
        description="Publica um clipping no marketplace",
        permission_classes=[IsAuthenticated],
    )
    def publish_to_marketplace(
        self, info: Info, clipping_id: str, input: PublishInput
    ) -> MarketplaceListing:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id

        data = ds.publish_to_marketplace(
            user_id=user_id,
            clipping_id=clipping_id,
            name=input.name,
            description=input.description,
        )
        return _doc_to_listing(data, user_id=user_id, ds=ds)

    @strawberry.mutation(
        description="Remove um listing do marketplace (somente o dono)",
        permission_classes=[IsAuthenticated],
    )
    def unpublish_from_marketplace(self, info: Info, listing_id: str) -> bool:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id

        listing = ds.get_marketplace_listing(listing_id)
        if listing is None:
            return False
        # Boundary camelCase: usa Pydantic para extrair authorUserId; suporta
        # tanto `author_user_id` (mocks legados) quanto `authorUserId`
        # (Firestore producao).
        payload = {**listing}
        payload.setdefault("id", payload.get("id", listing_id))
        listing_data = MarketplaceListingData.model_validate(payload)
        if listing_data.author_user_id != user_id:
            raise PermissionError("FORBIDDEN")
        return ds.unpublish_from_marketplace(listing_id)

    @strawberry.mutation(
        description="Curte/descurte um listing do marketplace",
        permission_classes=[IsAuthenticated],
    )
    def like_marketplace_listing(self, info: Info, listing_id: str) -> bool:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id
        return ds.toggle_like_marketplace(user_id, listing_id)

    @strawberry.mutation(
        description="Segue/deixa de seguir um listing do marketplace",
        deprecation_reason=(
            "Use `subscribeToClipping(input)` passando `sourceClippingId` do "
            "listing. Esta mutation continua funcional para retro-compat e "
            "será removida em R1."
        ),
        permission_classes=[IsAuthenticated],
    )
    def follow_marketplace_listing(self, info: Info, listing_id: str) -> bool:
        """Backward-compat: delega para `subscribe_to_clipping` (Fase A3).

        Lookup do listing → `sourceClippingId` → subscribe com channels default
        (apenas email = True). A interface pública continua bool/listingId
        para não quebrar clientes legados durante o rollout.
        """
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id

        listing = ds.get_marketplace_listing(listing_id)
        if listing is None:
            return False
        # `sourceClippingId` (camelCase) é o canônico; fallback snake_case
        # para fixtures legados.
        source_clipping_id = (
            listing.get("sourceClippingId") or listing.get("source_clipping_id")
        )
        if not source_clipping_id:
            return False

        ds.subscribe_to_clipping(
            user_id=user_id,
            clipping_id=source_clipping_id,
            channels={
                "email": True,
                "telegram": False,
                "push": False,
                "webhook": False,
            },
            extra_emails=[],
            webhook_url="",
        )
        return True

    @strawberry.mutation(
        description=(
            "Clona um listing do marketplace para os clippings do usuario. "
            "Retorna o Clipping recem-criado (gap-fix pre-rollout: era Boolean! "
            "antes; portal B3 precisa do `id` para redirecionar para "
            "/minha-conta/clipping/[id])."
        ),
        permission_classes=[IsAuthenticated],
    )
    def clone_marketplace_listing(self, info: Info, listing_id: str) -> Clipping:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id
        result = ds.clone_marketplace_listing(user_id, listing_id)
        # Aceitamos `ClippingData` (Pydantic) ou dict — o datasource ainda nao
        # tem implementacao real (so mocks); o contrato e "retorna o clipping
        # recem-criado". Para retrocompatibilidade defensiva, normalizamos
        # ambos os formatos.
        if isinstance(result, ClippingData):
            return _to_graphql_clipping(result)
        if isinstance(result, dict):
            return _to_graphql_clipping(ClippingData.model_validate(result))
        raise ValueError(
            "clone_marketplace_listing deve retornar ClippingData ou dict"
        )
