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


@strawberry.type
class Agency:
    code: str
    label: str


@strawberry.type
class Tag:
    label: str
    count: int
