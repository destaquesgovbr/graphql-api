from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from strawberry.fastapi import BaseContext

if TYPE_CHECKING:
    from graphql_api.datasources.firestore import FirestoreDatasource
    from graphql_api.datasources.postgres import PostgresDatasource
    from graphql_api.datasources.typesense import TypesenseDatasource
    from graphql_api.datasources.typesense_admin import TypesenseAdminDatasource


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
        if firestore_ds is not None:
            # Import local para evitar ciclo (dataloaders importa firestore).
            from graphql_api.dataloaders import create_subscription_loader

            self.subscription_loader = create_subscription_loader(firestore_ds)

    @property
    def is_authenticated(self) -> bool:
        return self.user is not None

    @property
    def is_internal(self) -> bool:
        return self.service_account is not None


async def get_context() -> GraphQLContext:
    return GraphQLContext()
