"""Testes Pydantic para `ClippingData` e `MarketplaceListingData`.

Fase A1 — Foundation: garante que os models Pydantic resolvem a fronteira
camelCase (Firestore) <-> snake_case (Python), com `populate_by_name=True`.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from graphql_api.datasources.firestore import (
    ClippingData,
    FirestoreDatasource,
    MarketplaceListingData,
)


NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _camel_clipping_doc(**overrides) -> dict:
    """Doc Firestore camelCase representativo (forma de produção)."""
    base = {
        "name": "Meu Clipping",
        "description": "Descricao",
        "recortes": [
            {
                "id": "r1",
                "title": "Economia",
                "themes": ["economia"],
                "agencies": ["agencia-brasil"],
                "keywords": ["pib"],
            }
        ],
        "prompt": "Resuma as noticias",
        "scheduleTime": "08:00",
        "deliveryChannels": {"email": True, "telegram": False, "push": False},
        "active": True,
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    base.update(overrides)
    return base


def _snake_clipping_doc(**overrides) -> dict:
    """Doc com snake_case (formato esperado nos mocks legados)."""
    base = {
        "name": "Meu Clipping",
        "description": "Descricao",
        "recortes": [],
        "prompt": "Resuma",
        "schedule_time": "08:00",
        "delivery_channels": {"email": True, "telegram": False, "push": False},
        "active": True,
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return base


def _camel_marketplace_doc(**overrides) -> dict:
    base = {
        "id": "listing-1",
        "authorUserId": "author-1",
        "authorDisplayName": "Alice",
        "sourceClippingId": "clip-src-1",
        "name": "Top Economia",
        "description": "Recortes",
        "recortes": [],
        "prompt": None,
        "likeCount": 5,
        "followerCount": 3,
        "cloneCount": 1,
        "publishedAt": NOW,
        "updatedAt": NOW,
        "active": True,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. ClippingData aceita doc camelCase (caso produção)
# ---------------------------------------------------------------------------
class TestClippingDataCamelCase:
    def test_pydantic_clipping_data_reads_camelcase_doc(self):
        doc = _camel_clipping_doc()
        data = {"id": "clip-1", **doc}

        clipping = ClippingData.model_validate(data)

        assert clipping.id == "clip-1"
        assert clipping.name == "Meu Clipping"
        assert clipping.schedule_time == "08:00"
        assert clipping.delivery_channels == {
            "email": True,
            "telegram": False,
            "push": False,
        }
        assert clipping.created_at == NOW
        assert clipping.updated_at == NOW

    def test_pydantic_clipping_data_reads_snakecase_doc(self):
        """Compat: docs snake_case (mocks legados) também devem parsear."""
        doc = _snake_clipping_doc()
        data = {"id": "clip-1", **doc}

        clipping = ClippingData.model_validate(data)

        assert clipping.id == "clip-1"
        assert clipping.schedule_time == "08:00"
        assert clipping.delivery_channels == {
            "email": True,
            "telegram": False,
            "push": False,
        }
        assert clipping.created_at == NOW

    def test_pydantic_clipping_data_writes_camelcase_dict(self):
        """`model_dump(by_alias=True)` deve produzir camelCase para escrita Firestore."""
        clipping = ClippingData(
            id="clip-1",
            name="Test",
            description="d",
            recortes=[],
            prompt=None,
            schedule_time="08:00",
            delivery_channels={"email": True, "telegram": False, "push": False},
            active=True,
            created_at=NOW,
            updated_at=NOW,
        )

        dumped = clipping.model_dump(by_alias=True)

        assert dumped["scheduleTime"] == "08:00"
        assert dumped["deliveryChannels"] == {
            "email": True,
            "telegram": False,
            "push": False,
        }
        assert dumped["createdAt"] == NOW
        assert dumped["updatedAt"] == NOW
        # snake_case não deve estar no dump
        assert "schedule_time" not in dumped
        assert "delivery_channels" not in dumped
        assert "created_at" not in dumped
        assert "updated_at" not in dumped

    def test_pydantic_validation_error_on_missing_required_id(self):
        """`id` é required."""
        doc = _camel_clipping_doc()
        # sem `id`
        with pytest.raises(ValidationError):
            ClippingData.model_validate(doc)

    def test_pydantic_extra_fields_ignored(self):
        """Campos extra no doc Firestore (futuros) não quebram parsing."""
        doc = _camel_clipping_doc(
            nextRunAt=NOW,
            schedule="0 8 * * *",
            extraEmails=["a@b.com"],
            includeHistory=False,
        )
        data = {"id": "clip-1", **doc}

        clipping = ClippingData.model_validate(data)

        # parsing bem-sucedido — campos extra ignorados sem erro
        assert clipping.id == "clip-1"
        assert clipping.name == "Meu Clipping"

    def test_pydantic_clipping_data_defaults_for_missing_fields(self):
        """Doc minimalista (só name) deve preencher defaults."""
        clipping = ClippingData.model_validate({"id": "x", "name": "X"})

        assert clipping.id == "x"
        assert clipping.name == "X"
        assert clipping.description is None
        assert clipping.recortes == []
        assert clipping.prompt is None
        assert clipping.schedule_time is None
        assert clipping.delivery_channels is None
        assert clipping.active is True  # default True
        assert clipping.created_at is None
        assert clipping.updated_at is None


# ---------------------------------------------------------------------------
# 2. MarketplaceListingData
# ---------------------------------------------------------------------------
class TestMarketplaceListingData:
    def test_pydantic_marketplace_listing_reads_camelcase(self):
        doc = _camel_marketplace_doc()

        listing = MarketplaceListingData.model_validate(doc)

        assert listing.id == "listing-1"
        assert listing.author_user_id == "author-1"
        assert listing.author_display_name == "Alice"
        assert listing.source_clipping_id == "clip-src-1"
        assert listing.like_count == 5
        assert listing.follower_count == 3
        assert listing.clone_count == 1
        assert listing.published_at == NOW
        assert listing.updated_at == NOW
        assert listing.active is True

    def test_pydantic_marketplace_listing_reads_snakecase(self):
        """Compat: docs snake_case (mocks legados) parseiam."""
        data = {
            "id": "listing-1",
            "author_user_id": "author-1",
            "author_display_name": "Alice",
            "source_clipping_id": "clip-src-1",
            "name": "Listing",
            "description": "desc",
            "recortes": [],
            "prompt": None,
            "like_count": 7,
            "follower_count": 2,
            "clone_count": 0,
            "published_at": NOW,
            "updated_at": NOW,
            "active": True,
        }

        listing = MarketplaceListingData.model_validate(data)

        assert listing.author_user_id == "author-1"
        assert listing.like_count == 7

    def test_pydantic_marketplace_listing_writes_camelcase(self):
        listing = MarketplaceListingData(
            id="listing-1",
            author_user_id="u1",
            author_display_name="Alice",
            source_clipping_id="clip-src",
            name="x",
            description="d",
            recortes=[],
            prompt=None,
            like_count=5,
            follower_count=3,
            clone_count=1,
            published_at=NOW,
            updated_at=NOW,
            active=True,
        )

        dumped = listing.model_dump(by_alias=True)

        assert dumped["authorUserId"] == "u1"
        assert dumped["authorDisplayName"] == "Alice"
        assert dumped["sourceClippingId"] == "clip-src"
        assert dumped["likeCount"] == 5
        assert dumped["followerCount"] == 3
        assert dumped["cloneCount"] == 1
        assert dumped["publishedAt"] == NOW
        assert dumped["updatedAt"] == NOW


# ---------------------------------------------------------------------------
# 3. Strawberry type ainda derivável do modelo Pydantic
# ---------------------------------------------------------------------------
class TestStrawberryUsesPydanticModel:
    def test_strawberry_clipping_type_uses_pydantic_model(self):
        """O Clipping Strawberry gerado deve expor os mesmos campos."""
        from graphql_api.schema.types.clipping import Clipping

        # tipo gerado por strawberry.experimental.pydantic.type aponta para o modelo
        # via atributo _pydantic_type. Caso a integração mude, basta inspecionar
        # os campos do schema.
        import strawberry

        fields = {f.python_name for f in strawberry.Schema(query=_StubQuery).schema_converter.type_map.get("Clipping").definition.fields}
        # campos snake_case esperados:
        for fname in [
            "id",
            "name",
            "description",
            "recortes",
            "prompt",
            "schedule_time",
            "delivery_channels",
            "active",
            "created_at",
            "updated_at",
        ]:
            assert fname in fields, f"campo '{fname}' ausente em Clipping Strawberry"


# stub query usada só para forçar inclusão do Clipping no type_map
import strawberry  # noqa: E402
from typing import Optional  # noqa: E402

from graphql_api.schema.types.clipping import Clipping  # noqa: E402


@strawberry.type
class _StubQuery:
    @strawberry.field
    def first_clipping(self) -> Optional[Clipping]:
        return None


# ---------------------------------------------------------------------------
# 4. Datasource: integração — leitura via Pydantic.model_validate
# ---------------------------------------------------------------------------
class TestFirestoreDatasourceUsesPydantic:
    """Comprova que o datasource transita por `model_validate` sem regressão."""

    def _mock_db(self, docs):
        db = MagicMock()
        col = MagicMock()
        user_doc = MagicMock()
        clippings_col = MagicMock()
        db.collection.return_value = col
        col.document.return_value = user_doc
        user_doc.collection.return_value = clippings_col
        clippings_col.stream.return_value = iter(docs)
        return db

    def test_datasource_returns_clipping_data_from_camelcase_doc(self):
        doc = MagicMock()
        doc.id = "clip-camel"
        doc.exists = True
        doc.to_dict.return_value = _camel_clipping_doc()
        db = self._mock_db([doc])

        ds = FirestoreDatasource(db)
        result = ds.get_clippings("user-123")

        assert len(result) == 1
        clipping = result[0]
        assert isinstance(clipping, ClippingData)
        assert clipping.id == "clip-camel"
        # bug histórico: schedule_time vinha como None com docs camelCase
        assert clipping.schedule_time == "08:00"
        assert clipping.delivery_channels == {
            "email": True,
            "telegram": False,
            "push": False,
        }
        assert clipping.created_at == NOW
