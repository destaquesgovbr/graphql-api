from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from strawberry.fastapi import BaseContext

if TYPE_CHECKING:
    from graphql_api.datasources.firestore import FirestoreDatasource
    from graphql_api.datasources.typesense import TypesenseDatasource


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
    ):
        super().__init__()
        self.user: Optional[User] = None
        self.service_account: Optional[ServiceAccount] = None
        self.typesense_ds = typesense_ds
        self.firestore_ds = firestore_ds

    @property
    def is_authenticated(self) -> bool:
        return self.user is not None

    @property
    def is_internal(self) -> bool:
        return self.service_account is not None


async def get_context() -> GraphQLContext:
    return GraphQLContext()
