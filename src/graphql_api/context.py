from dataclasses import dataclass, field
from typing import Optional

from strawberry.fastapi import BaseContext


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
    def __init__(self):
        super().__init__()
        self.user: Optional[User] = None
        self.service_account: Optional[ServiceAccount] = None

    @property
    def is_authenticated(self) -> bool:
        return self.user is not None

    @property
    def is_internal(self) -> bool:
        return self.service_account is not None


async def get_context() -> GraphQLContext:
    return GraphQLContext()
