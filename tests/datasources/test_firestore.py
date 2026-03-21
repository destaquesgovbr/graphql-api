from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from graphql_api.datasources.firestore import (
    ClippingData,
    FirestoreDatasource,
    MaxClippingsError,
)


def _mock_doc(doc_id: str, data: dict):
    """Create a mock Firestore document snapshot."""
    doc = MagicMock()
    doc.id = doc_id
    doc.exists = True
    doc.to_dict.return_value = data
    return doc


def _sample_clipping_data(**overrides) -> dict:
    now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    base = {
        "name": "Meu Clipping",
        "description": "Clipping de teste",
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
        "schedule_time": "08:00",
        "delivery_channels": {"email": True, "telegram": False, "push": False},
        "active": True,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


def _mock_firestore_db(docs=None, count=0):
    """Create a mock Firestore db client."""
    db = MagicMock()
    collection = MagicMock()
    user_doc = MagicMock()
    clippings_col = MagicMock()

    db.collection.return_value = collection
    collection.document.return_value = user_doc
    user_doc.collection.return_value = clippings_col

    if docs is not None:
        clippings_col.stream.return_value = iter(docs)
    else:
        clippings_col.stream.return_value = iter([])

    return db, clippings_col


class TestGetClippings:
    def test_get_clippings_returns_list(self):
        data = _sample_clipping_data()
        mock_docs = [_mock_doc("clip-1", data), _mock_doc("clip-2", data)]
        db, _ = _mock_firestore_db(docs=mock_docs)
        ds = FirestoreDatasource(db)

        result = ds.get_clippings("user-123")

        assert len(result) == 2
        assert isinstance(result[0], ClippingData)
        assert result[0].id == "clip-1"
        assert result[0].name == "Meu Clipping"
        assert result[1].id == "clip-2"

    def test_get_clippings_empty(self):
        db, _ = _mock_firestore_db(docs=[])
        ds = FirestoreDatasource(db)

        result = ds.get_clippings("user-123")

        assert result == []


class TestCreateClipping:
    def test_create_clipping_returns_with_id(self):
        db, clippings_col = _mock_firestore_db(docs=[])
        doc_ref = MagicMock()
        clippings_col.document.return_value = doc_ref
        ds = FirestoreDatasource(db)

        data = {"name": "Novo Clipping", "description": "Descricao"}
        result = ds.create_clipping("user-123", data)

        assert isinstance(result, ClippingData)
        assert result.id != ""
        assert result.name == "Novo Clipping"
        assert result.description == "Descricao"
        assert result.created_at is not None
        assert result.updated_at is not None
        doc_ref.set.assert_called_once()

    def test_max_10_clippings_raises_error(self):
        data = _sample_clipping_data()
        mock_docs = [_mock_doc(f"clip-{i}", data) for i in range(10)]
        db, clippings_col = _mock_firestore_db(docs=mock_docs)
        ds = FirestoreDatasource(db)

        with pytest.raises(MaxClippingsError, match="max 10"):
            ds.create_clipping("user-123", {"name": "One too many"})


class TestUpdateClipping:
    def test_update_clipping(self):
        updated_data = _sample_clipping_data(name="Updated Name")
        db, clippings_col = _mock_firestore_db()
        doc_ref = MagicMock()
        updated_doc = _mock_doc("clip-1", updated_data)
        doc_ref.get.return_value = updated_doc
        clippings_col.document.return_value = doc_ref
        ds = FirestoreDatasource(db)

        result = ds.update_clipping("user-123", "clip-1", {"name": "Updated Name"})

        assert isinstance(result, ClippingData)
        assert result.id == "clip-1"
        assert result.name == "Updated Name"
        doc_ref.update.assert_called_once()


class TestDeleteClipping:
    def test_delete_clipping(self):
        db, clippings_col = _mock_firestore_db()
        doc_ref = MagicMock()
        clippings_col.document.return_value = doc_ref
        ds = FirestoreDatasource(db)

        result = ds.delete_clipping("user-123", "clip-1")

        assert result is True
        doc_ref.delete.assert_called_once()
