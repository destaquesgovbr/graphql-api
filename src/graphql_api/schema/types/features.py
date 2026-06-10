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
    # ORG (instituições), PER (pessoas), LOC (locais), MISC, EVENT, POLICY…
    type: str
    count: int
    # Fase 4 (canonicalização): id canônico da entidade no `entity_registry`
    # (QID Wikidata "Q216330" ou "dgb_<ulid>"); None até a menção ser
    # canonicalizada. `salience` [0,1] é emitido pelo LLM (opcional).
    canonical_id: Optional[str] = None
    salience: Optional[float] = None


@strawberry.type
class ContentAnnotation:
    """Anotação inline (offset) de uma entidade no `news.content` (Fase 5).

    Span de caracteres `[start, end)` derivado deterministicamente pelo
    feature-worker (re-match do texto da entidade no conteúdo). Renderizável
    como camada semântica ligável/colorida por tipo no portal.
    """

    start: int
    end: int
    type: str
    text: str
    canonical_id: Optional[str] = None


@strawberry.type
class ArticleFeatures:
    """Features computadas, expostas no campo lazy `Article.features`."""

    entities: list[EntityType] = strawberry.field(default_factory=list)
    content_annotations: list[ContentAnnotation] = strawberry.field(default_factory=list)
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


def _as_str_or_none(value: Any) -> Optional[str]:
    """Coage para str não-vazia; None/'' viram None. bool é descartado para
    evitar 'True'/'False' acidentais vindos de JSONB malformado."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


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
            EntityType(
                text=str(text),
                type=str(etype),
                count=count if count else 1,
                # canonical_id pode ser null pré-canonicalização; salience é
                # opcional. Leitura tolerante (forma_canonica não é exposta).
                canonical_id=_as_str_or_none(item.get("canonical_id")),
                salience=_as_float(item.get("salience")),
            )
        )
    return entities


def _parse_annotations(raw: Any) -> list[ContentAnnotation]:
    """Parser tolerante das anotações inline (Fase 5), espelhando
    `_parse_entities`: itens não-dict ou sem os campos obrigatórios
    (`start`/`end` inteiros, `text` e `type` não-vazios) são ignorados —
    nunca levanta. `start`/`end` são coagidos para int; `end <= start` ou
    índices negativos são descartados (span inválido)."""
    if not isinstance(raw, list):
        return []
    annotations: list[ContentAnnotation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        start = _as_int(item.get("start"))
        end = _as_int(item.get("end"))
        if start is None or end is None:
            continue
        if start < 0 or end <= start:
            continue
        text = item.get("text")
        atype = item.get("type")
        if not text or not atype:
            continue
        annotations.append(
            ContentAnnotation(
                start=start,
                end=end,
                type=str(atype),
                text=str(text),
                canonical_id=_as_str_or_none(item.get("canonical_id")),
            )
        )
    return annotations


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
        content_annotations=_parse_annotations(features.get("content_annotations")),
        view_count=_as_int(features.get("view_count")),
        unique_sessions=_as_int(features.get("unique_sessions")),
        trending_score=_as_float(features.get("trending_score")),
        word_count=_as_int(features.get("word_count")),
        readability_flesch=_as_float(features.get("readability_flesch")),
    )
