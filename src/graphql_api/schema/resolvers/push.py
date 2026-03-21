import strawberry
from strawberry.types import Info

from graphql_api.auth.guards import IsAuthenticated
from graphql_api.schema.types.push import PushPreferencesInput, PushSubscriptionInput


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

        ref = ds._db.collection("users").document(user_id)
        ref.set({"push_preferences": doc_data}, merge=True)

        return True
