import strawberry

from graphql_api.schema.types.theme import Agency, Theme


@strawberry.input
class PushSubscriptionInput:
    endpoint: str
    keys_p256dh: str
    keys_auth: str


@strawberry.input
class PushPreferencesInput:
    agencies: list[str] = strawberry.field(default_factory=list)
    themes: list[str] = strawberry.field(default_factory=list)
    enabled: bool = True


@strawberry.type
class PushPreferences:
    """Preferencias de push notification do usuario autenticado.

    Path canonico Firestore: `users/{userId}/pushPreferences/filters`.
    Por enquanto so expomos `agencies` (alinhado ao portal e ao GET
    `/api/push/preferences`).
    """

    agencies: list[str] = strawberry.field(default_factory=list)


@strawberry.type
class PushFiltersData:
    """Filtros disponiveis para a UI de preferencias de push.

    Reusa os tipos `Theme` e `Agency` ja expostos via `MetadataQuery`.
    """

    themes: list[Theme] = strawberry.field(default_factory=list)
    agencies: list[Agency] = strawberry.field(default_factory=list)
