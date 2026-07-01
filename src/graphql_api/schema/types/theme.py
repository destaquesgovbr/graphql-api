from typing import Optional

import strawberry


@strawberry.type
class ThemeLevel:
    code: str
    label: str


@strawberry.type
class ThemeHierarchy:
    level1: Optional[ThemeLevel] = None
    level2: Optional[ThemeLevel] = None
    level3: Optional[ThemeLevel] = None
    most_specific: Optional[ThemeLevel] = None


@strawberry.type
class Theme:
    code: str
    label: str


_REPUBLISHERS = frozenset({"agencia_brasil", "tvbrasil", "ebc", "radioagencia_nacional"})


@strawberry.type
class Agency:
    code: str
    label: str

    @strawberry.field(
        description="True para agências republicadoras (EBC, Agência Brasil, TV Brasil)"
    )
    def is_republisher(self) -> bool:
        return self.code in _REPUBLISHERS


@strawberry.type
class Tag:
    label: str
    count: int
