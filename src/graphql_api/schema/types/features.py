"""Tipos GraphQL das features computadas de uma notícia (Fase 1).

As features vivem no JSONB `news_features.features` (preenchido por
feature-worker / enrichment-worker / DAGs). Aqui expomos de forma tipada e
sob demanda apenas o subconjunto voltado ao público da tela de notícia:
entidades (NER), popularidade/trending e leitura/legibilidade.

`article_features_from_json` é tolerante: o pipeline é parcial (cobertura
~78% de entities, ~0.3% de view_count), então campos ausentes/malformados
viram None / lista vazia em vez de erro.
"""

from typing import Any, Optional

import strawberry


@strawberry.type
class EntityType:
    """Entidade nomeada extraída do conteúdo (NER via LLM)."""

    text: str
    # ORG (instituições), PER (pessoas), LOC (locais), MISC, EVENT, PROGRAM…
    type: str
    count: int


@strawberry.type
class ArticleFeatures:
    """Features computadas, expostas no campo lazy `Article.features`."""

    entities: list[EntityType] = strawberry.field(default_factory=list)
    view_count: Optional[int] = None
    unique_sessions: Optional[int] = None
    trending_score: Optional[float] = None
    word_count: Optional[int] = None
    readability_flesch: Optional[float] = None


def _as_int(value: Any) -> Optional[int]:
    # bool é subclasse de int — descartado explicitamente.
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _parse_entities(raw: Any) -> list[EntityType]:
    if not isinstance(raw, list):
        return []
    entities: list[EntityType] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        etype = item.get("type")
        if not text or not etype:
            continue
        count = _as_int(item.get("count"))
        entities.append(
            EntityType(text=str(text), type=str(etype), count=count if count else 1)
        )
    return entities


def article_features_from_json(
    features: Optional[dict[str, Any]],
) -> Optional[ArticleFeatures]:
    """Mapeia o JSONB `news_features.features` para `ArticleFeatures`.

    Retorna None quando não há features (dict vazio/None) — o portal esconde a
    seção. Campos ausentes viram None; entidades malformadas são ignoradas.
    """
    if not features:
        return None
    return ArticleFeatures(
        entities=_parse_entities(features.get("entities")),
        view_count=_as_int(features.get("view_count")),
        unique_sessions=_as_int(features.get("unique_sessions")),
        trending_score=_as_float(features.get("trending_score")),
        word_count=_as_int(features.get("word_count")),
        readability_flesch=_as_float(features.get("readability_flesch")),
    )
