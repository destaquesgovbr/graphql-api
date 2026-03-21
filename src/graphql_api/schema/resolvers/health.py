import strawberry


@strawberry.type
class HealthQuery:
    @strawberry.field(description="Verifica se a API está funcionando")
    def ping(self) -> str:
        return "pong"
