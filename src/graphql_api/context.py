import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from strawberry.fastapi import BaseContext

if TYPE_CHECKING:
    from fastapi import Request

    from graphql_api.datasources.firestore import FirestoreDatasource
    from graphql_api.datasources.postgres import PostgresDatasource
    from graphql_api.datasources.typesense import TypesenseDatasource
    from graphql_api.datasources.typesense_admin import TypesenseAdminDatasource

logger = logging.getLogger(__name__)

# Cache (email normalizado → stableUserId) com TTL, para evitar um read no
# Firestore a cada request autenticado. O stableUserId do portal é estável,
# então um TTL curto é seguro.
_STABLE_ID_TTL = 600.0
_stable_id_cache: dict[str, tuple[str, float]] = {}


def _resolve_stable_user_id(firestore_ds: Any, email: str) -> Optional[str]:
    """Resolve (com cache) o stableUserId por email via FirestoreDatasource.
    Espelha o `resolveStableUser` do portal para alinhar a identidade. Retorna
    `None` se não houver doc — o chamador mantém o `sub` do JWT como fallback.
    """
    key = email.lower().strip()
    if not key:
        return None
    now = time.monotonic()
    cached = _stable_id_cache.get(key)
    if cached is not None and cached[1] > now:
        return cached[0]
    stable = firestore_ds.resolve_stable_user_id(key)
    if stable:
        _stable_id_cache[key] = (stable, now + _STABLE_ID_TTL)
    return stable


@dataclass
class User:
    id: str
    email: str
    roles: list[str] = field(default_factory=list)


@dataclass
class ServiceAccount:
    email: str
    is_service_account: bool = True


class GraphQLContext(BaseContext):
    def __init__(
        self,
        typesense_ds: Optional["TypesenseDatasource"] = None,
        firestore_ds: Optional["FirestoreDatasource"] = None,
        postgres_ds: Optional["PostgresDatasource"] = None,
        typesense_admin_ds: Optional["TypesenseAdminDatasource"] = None,
    ):
        super().__init__()
        self.user: Optional[User] = None
        self.service_account: Optional[ServiceAccount] = None
        self.typesense_ds = typesense_ds
        self.firestore_ds = firestore_ds
        self.postgres_ds = postgres_ds
        self.typesense_admin_ds = typesense_admin_ds
        # Fase A3: dataloader populado por request em `get_context()` quando
        # houver firestore_ds. Mantém-se Optional para os testes que injetam
        # contexto manualmente sem dataloader.
        self.subscription_loader: Optional[Any] = None
        # Fase A5: dataloader de releases (batch por clipping_id).
        self.releases_loader: Optional[Any] = None
        # Fase 1 (features): dataloader de features por unique_id (batch Postgres),
        # usado pelo campo lazy `Article.features`.
        self.features_loader: Optional[Any] = None
        if firestore_ds is not None:
            # Import local para evitar ciclo (dataloaders importa firestore).
            from graphql_api.dataloaders import (
                create_releases_loader,
                create_subscription_loader,
            )

            self.subscription_loader = create_subscription_loader(firestore_ds)
            self.releases_loader = create_releases_loader(firestore_ds)
        if postgres_ds is not None:
            from graphql_api.dataloaders import create_features_loader

            self.features_loader = create_features_loader(postgres_ds)

    @property
    def is_authenticated(self) -> bool:
        return self.user is not None

    @property
    def is_internal(self) -> bool:
        return self.service_account is not None


def _extract_bearer_token(request: "Request") -> Optional[str]:
    """Extrai o token do header `Authorization: Bearer <token>`. Case-insensitive."""
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


async def get_context(request: Optional["Request"] = None) -> GraphQLContext:
    """Constrói o `GraphQLContext` para um request.

    - Quando `request` é None (testes legacy que patcham este símbolo sem
      passar args), retorna contexto vazio — compat com mocks pré-lifespan.
    - Quando `request` é fornecido (path normal via FastAPI dep), lê os
      datasources de `request.app.state` (populados pelo lifespan em
      `graphql_api.lifespan`) e tenta resolver o usuário via JWT no header
      `Authorization`.

    Falhas de auth (token expirado/inválido) deixam `ctx.user = None` —
    queries públicas continuam funcionando, queries autenticadas falham com
    UNAUTHENTICATED via `IsAuthenticated` permission.
    """
    if request is None:
        return GraphQLContext()

    state = request.app.state
    ctx = GraphQLContext(
        typesense_ds=getattr(state, "typesense_ds", None),
        firestore_ds=getattr(state, "firestore_ds", None),
        postgres_ds=getattr(state, "postgres_ds", None),
        typesense_admin_ds=getattr(state, "typesense_admin_ds", None),
    )

    token = _extract_bearer_token(request)
    if token is None:
        return ctx

    # Auth de usuário (Keycloak JWT). Falha silenciosa: deixa ctx.user=None.
    jwks_url = os.environ.get("AUTH_JWKS_URL")
    if jwks_url:
        try:
            from graphql_api.auth.jwt import verify_jwt

            issuer = os.environ.get("AUTH_ISSUER")
            user = await verify_jwt(token, jwks_url, issuer=issuer)
            if user is not None:
                # Alinha a identidade ao portal: o `authorUserId`/`userId` dos
                # dados é o stableUserId (id do doc `users` por email), não o
                # `sub` cru do JWT. Resolve por email; mantém o `sub` se não
                # houver doc ou se o Firestore falhar.
                if ctx.firestore_ds is not None and user.email:
                    try:
                        stable = _resolve_stable_user_id(
                            ctx.firestore_ds, user.email
                        )
                        if stable:
                            user.id = stable
                    except Exception as exc:
                        logger.warning(
                            "falha ao resolver stableUserId por email: %s", exc
                        )
                ctx.user = user
                return ctx
        except Exception as exc:  # pragma: no cover - defensivo
            logger.warning("falha inesperada validando JWT: %s", exc)

    # Auth de service account (Google OIDC). Apenas quando JWT do user falhou e
    # `SERVICE_ACCOUNT_AUDIENCE` está configurado.
    sa_audience = os.environ.get("SERVICE_ACCOUNT_AUDIENCE")
    if sa_audience:
        try:
            from graphql_api.auth.service_account import verify_service_account

            sa = await verify_service_account(token, sa_audience)
            if sa is not None:
                ctx.service_account = sa
        except Exception as exc:  # pragma: no cover - defensivo
            logger.warning("falha inesperada validando service account: %s", exc)

    return ctx
