import strawberry
from strawberry.types import Info

from graphql_api.auth.guards import IsAuthenticated
from graphql_api.schema.resolvers.metadata import MetadataQuery
from graphql_api.schema.types.push import (
    PushFiltersData,
    PushPreferences,
    PushPreferencesInput,
    PushSubscriptionInput,
)


@strawberry.type
class PushQuery:
    """Queries para leitura de preferencias de push (gap-fix pre-rollout).

    Espelha o GET `/api/push/preferences` e `/api/push/filters-data` do
    portal. Path canonico: `users/{userId}/pushPreferences/filters` com
    campo `agencies: [str]`.
    """

    @strawberry.field(
        description="Preferencias de push notification do usuario autenticado",
        permission_classes=[IsAuthenticated],
    )
    def push_preferences(self, info: Info) -> PushPreferences:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id

        doc_ref = (
            ds._db.collection("users")
            .document(user_id)
            .collection("pushPreferences")
            .document("filters")
        )
        snapshot = doc_ref.get()
        if not getattr(snapshot, "exists", False):
            return PushPreferences(agencies=[])

        data = snapshot.to_dict() or {}
        agencies = data.get("agencies") or []
        return PushPreferences(agencies=list(agencies))

    @strawberry.field(
        description=(
            "Filtros disponiveis (temas e agencias) para o usuario escolher na "
            "UI de preferencias de push."
        )
    )
    def push_filters_data(self, info: Info) -> PushFiltersData:
        # Delegacao para `MetadataQuery` para evitar duplicar a chamada ao
        # Typesense — o shape de `Theme` e `Agency` ja eh exatamente o que
        # o portal espera.
        metadata = MetadataQuery()
        themes = metadata.themes(info)
        agencies = metadata.agencies(info)
        return PushFiltersData(themes=themes, agencies=agencies)


@strawberry.type
class PushMutation:
    @strawberry.mutation(
        description="Sync a push notification subscription",
        permission_classes=[IsAuthenticated],
    )
    def sync_push_subscription(
        self,
        info: Info,
        subscription: PushSubscriptionInput,
    ) -> bool:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id

        doc_data = {
            "endpoint": subscription.endpoint,
            "keys_p256dh": subscription.keys_p256dh,
            "keys_auth": subscription.keys_auth,
        }

        ref = ds._db.collection("users").document(user_id).collection("push_subscriptions")
        # Use endpoint as a deterministic doc ID to allow upsert
        import hashlib

        doc_id = hashlib.md5(subscription.endpoint.encode()).hexdigest()
        ref.document(doc_id).set(doc_data)

        return True

    @strawberry.mutation(
        description="Update push notification preferences",
        permission_classes=[IsAuthenticated],
    )
    def update_push_preferences(
        self,
        info: Info,
        preferences: PushPreferencesInput,
    ) -> bool:
        ctx = info.context
        ds = ctx.firestore_ds
        user_id = ctx.user.id

        doc_data = {
            "agencies": preferences.agencies,
            "themes": preferences.themes,
            "enabled": preferences.enabled,
        }

        # IMPORTANTE: gravar no MESMO local que `push_preferences` (query) lê —
        # subcoleção `users/{uid}/pushPreferences/filters`. Antes gravava em
        # `users/{uid}.push_preferences` (campo aninhado no doc), divergente da
        # leitura, causando falha silenciosa (update OK mas read vazio).
        doc_ref = (
            ds._db.collection("users")
            .document(user_id)
            .collection("pushPreferences")
            .document("filters")
        )
        doc_ref.set(doc_data, merge=True)

        return True
