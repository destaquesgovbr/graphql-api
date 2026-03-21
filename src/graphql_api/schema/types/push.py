import strawberry


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
