import strawberry


@strawberry.type
class SearchSuggestion:
    unique_id: str
    title: str
