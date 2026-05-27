import typing

from strawberry.permission import BasePermission
from strawberry.types import Info


class IsAuthenticated(BasePermission):
    message = "UNAUTHENTICATED"

    def has_permission(self, source: typing.Any, info: Info, **kwargs: typing.Any) -> bool:
        context = info.context
        return getattr(context, "user", None) is not None


class IsInternal(BasePermission):
    message = "FORBIDDEN"

    def has_permission(self, source: typing.Any, info: Info, **kwargs: typing.Any) -> bool:
        context = info.context
        return getattr(context, "service_account", None) is not None
