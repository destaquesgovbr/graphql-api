"""Resolvers de queries do usuario autenticado (Fase 1A SSR)."""

import strawberry
from strawberry.types import Info


@strawberry.type
class UserQuery:
    @strawberry.field(
        description=(
            "True se o usuario autenticado tem o Telegram vinculado "
            "(`users/{id}/telegramLink/account` existe). Substitui o "
            "`getHasTelegram` do portal. Retorna False se nao logado ou sem doc."
        )
    )
    def current_user_has_telegram_linked(self, info: Info) -> bool:
        ctx = info.context
        user = getattr(ctx, "user", None)
        if user is None:
            # Sem sessao: False (nao erra) — espelha o comportamento do portal.
            return False
        ds = getattr(ctx, "firestore_ds", None)
        if ds is None:
            return False
        return ds.has_telegram_linked(user.id)
