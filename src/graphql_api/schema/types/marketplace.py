"""Tipos Strawberry para marketplace.

Fase A1: `MarketplaceListing` continua sendo definido manualmente como
`@strawberry.type` porque carrega campos contextuais (`has_liked`,
`has_followed`) que sao calculados pelo resolver e nao existem no modelo
Pydantic da fronteira Firestore (`MarketplaceListingData`).

`MarketplaceRecorte` poderia ser gerado a partir de `RecorteData`, mas
permanece manual aqui para nao misturar a hierarquia de tipos do clipping com
a do marketplace — duas listas independentes que podem evoluir de forma
diferente (ex.: campos analytics no recorte do marketplace).
"""

from datetime import datetime
from typing import Optional

import strawberry
from strawberry.types import Info

from graphql_api.schema.types.clipping import Release, release_from_data


@strawberry.type
class MarketplaceRecorte:
    id: str
    title: str
    themes: list[str]
    agencies: list[str]
    keywords: list[str]


@strawberry.type
class MarketplaceListing:
    id: str
    author_user_id: str
    author_display_name: str
    source_clipping_id: str
    name: str
    description: Optional[str] = None
    recortes: list[MarketplaceRecorte] = strawberry.field(default_factory=list)
    prompt: Optional[str] = None
    schedule: Optional[str] = None
    like_count: int = 0
    follower_count: int = 0
    clone_count: int = 0
    published_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    active: bool = True
    has_liked: Optional[bool] = None
    has_followed: Optional[bool] = None

    @strawberry.field(
        description=(
            "Entregas historicas (releases) deste listing publico. "
            "Ordenadas por createdAt desc. Conteudo PUBLICO: nao exige "
            "autenticacao, pois um listing ativo ja e publico. Listing "
            "inativo/despublicado nunca expoe releases (retorna lista vazia)."
        )
    )
    def releases(
        self,
        info: Info,
        limit: int = 10,
        before: Optional[datetime] = None,
    ) -> list[Release]:
        # Seguranca: so listing ATIVO expoe releases. Como `self` ja foi
        # resolvido a partir do listing, um listing inativo/despublicado
        # (`active=False`) nunca vaza releases — defesa em profundidade
        # (o `marketplaceListing(id)` ja filtra inativos, mas garantimos aqui
        # tambem para qualquer caminho que construa o tipo).
        if not self.active:
            return []

        ctx = info.context
        ds = getattr(ctx, "firestore_ds", None)
        if ds is None:
            return []

        # Clamp limit no intervalo [1, 100] para evitar abuso.
        safe_limit = max(1, min(int(limit), 100))

        data_list = ds.get_releases(
            self.source_clipping_id, limit=safe_limit, before=before
        )
        return [release_from_data(d) for d in data_list]


@strawberry.type
class MarketplaceListingsResult:
    listings: list[MarketplaceListing]
    total: int


@strawberry.input
class PublishInput:
    name: str
    description: Optional[str] = None
