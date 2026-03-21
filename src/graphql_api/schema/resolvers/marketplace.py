from typing import Optional

import strawberry
from strawberry.types import Info

from graphql_api.auth.guards import IsAuthenticated
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
    recortes = []
    for r in data.get("recortes", []):
        recortes.append(
            MarketplaceRecorte(
                id=r.get("id", ""),
                title=r.get("title", ""),
                themes=r.get("themes", []),
                agencies=r.get("agencies", []),
                keywords=r.get("keywords", []),
            )
        )

    listing_id = data.get("id", "")

    has_liked: Optional[bool] = None
    has_followed: Optional[bool] = None

    if user_id is not None and ds is not None:
        has_liked = ds.has_liked_listing(user_id, listing_id)
        has_followed = ds.has_followed_listing(user_id, listing_id)

    return MarketplaceListing(
        id=listing_id,
        author_user_id=data.get("author_user_id", ""),
        author_display_name=data.get("author_display_name", ""),
        source_clipping_id=data.get("source_clipping_id", ""),
        name=data.get("name", ""),
        description=data.get("description"),
        recortes=recortes,
        prompt=data.get("prompt"),
        like_count=data.get("like_count", 0),
        follower_count=data.get("follower_count", 0),
        clone_count=data.get("clone_count", 0),
        published_at=data.get("published_at"),
        updated_at=data.get("updated_at"),
        active=data.get("active", True),
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
        if listing.get("author_user_id") != user_id:
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
        permission_classes=[IsAuthenticated],
    )
    def follow_marketplace_listing(self, info: Info, listing_id: str) -> bool:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id
        return ds.toggle_follow_marketplace(user_id, listing_id)

    @strawberry.mutation(
        description="Clona um listing do marketplace para os clippings do usuario",
        permission_classes=[IsAuthenticated],
    )
    def clone_marketplace_listing(self, info: Info, listing_id: str) -> bool:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id
        return ds.clone_marketplace_listing(user_id, listing_id)
