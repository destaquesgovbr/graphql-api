"""Testes do FirestoreDatasource — bloco marketplace (graphql-api#3).

Cobertura dos 8 metodos novos:
- get_marketplace_listings / get_marketplace_listing
- publish_to_marketplace / unpublish_from_marketplace / clone_marketplace_listing
- has_liked_listing / has_followed_listing / toggle_like_marketplace

Estrategia: MagicMock no `db` Firestore + asserts sobre as chamadas
(coleções, filtros, batch ops). Mesma abordagem dos resolvers — aqui o
foco e o contrato de IO do datasource, nao a logica de armazenamento real
(coberta por smoke contra Firestore em dev local).
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from graphql_api.datasources.firestore import (
    ClippingData,
    FirestoreDatasource,
    UnauthorizedError,
)

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _mock_doc(doc_id: str, data: dict, exists: bool = True):
    doc = MagicMock()
    doc.id = doc_id
    doc.exists = exists
    doc.to_dict.return_value = data
    return doc


def _listing_doc(**overrides) -> dict:
    base = {
        "authorUserId": "author-1",
        "authorDisplayName": "Autor Um",
        "sourceClippingId": "clip-src-1",
        "name": "Listing Teste",
        "description": "desc",
        "recortes": [
            {
                "id": "r1",
                "title": "Economia",
                "themes": ["economia"],
                "agencies": ["agencia-brasil"],
                "keywords": ["pib"],
            }
        ],
        "prompt": "Resuma os 3 mais importantes",
        "likeCount": 0,
        "followerCount": 0,
        "cloneCount": 0,
        "publishedAt": NOW,
        "updatedAt": NOW,
        "active": True,
    }
    base.update(overrides)
    return base


def _clipping_doc(**overrides) -> dict:
    base = {
        "name": "Clipping Origem",
        "description": "desc clipping",
        "recortes": [{"id": "r1", "title": "Economia"}],
        "prompt": "p",
        "active": True,
        "authorUserId": "author-1",
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# get_marketplace_listings
# ---------------------------------------------------------------------------
class TestGetMarketplaceListings:
    def test_returns_listings_and_total(self):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)

        # Cadeia: collection -> where -> order_by -> offset -> limit -> stream
        col_ref = db.collection.return_value
        base_q = col_ref.where.return_value
        ordered_q = base_q.order_by.return_value
        offset_q = ordered_q.offset.return_value
        limit_q = offset_q.limit.return_value

        limit_q.stream.return_value = [
            _mock_doc("L1", _listing_doc(name="A")),
            _mock_doc("L2", _listing_doc(name="B")),
        ]
        # count().get() retorna [[AggregationResult]] -> .value
        count_value = MagicMock()
        count_value.value = 2
        base_q.count.return_value.get.return_value = [[count_value]]

        result = ds.get_marketplace_listings(offset=0, limit=20)

        assert result["total"] == 2
        assert [li["id"] for li in result["listings"]] == ["L1", "L2"]
        assert result["listings"][0]["name"] == "A"

        db.collection.assert_called_with("marketplace")
        col_ref.where.assert_called_once_with("active", "==", True)
        base_q.order_by.assert_called_once_with(
            "publishedAt", direction="DESCENDING"
        )
        ordered_q.offset.assert_called_once_with(0)
        offset_q.limit.assert_called_once_with(20)

    def test_total_falls_back_to_zero_when_count_breaks(self):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        base_q = db.collection.return_value.where.return_value
        base_q.count.side_effect = RuntimeError("count not supported")
        # stream ainda retorna lista
        base_q.order_by.return_value.offset.return_value.limit.return_value.stream.return_value = []
        result = ds.get_marketplace_listings(offset=10, limit=5)
        assert result == {"listings": [], "total": 0}


# ---------------------------------------------------------------------------
# get_marketplace_listing
# ---------------------------------------------------------------------------
class TestGetMarketplaceListing:
    def test_returns_dict_when_active(self):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        doc_ref = db.collection.return_value.document.return_value
        doc_ref.get.return_value = _mock_doc(
            "L1", _listing_doc(name="Achei")
        )
        out = ds.get_marketplace_listing("L1")
        assert out is not None
        assert out["id"] == "L1"
        assert out["name"] == "Achei"
        db.collection.assert_called_with("marketplace")

    def test_returns_none_when_missing(self):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        db.collection.return_value.document.return_value.get.return_value = (
            _mock_doc("L1", {}, exists=False)
        )
        assert ds.get_marketplace_listing("L1") is None

    def test_returns_none_when_inactive(self):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        db.collection.return_value.document.return_value.get.return_value = (
            _mock_doc("L1", _listing_doc(active=False))
        )
        assert ds.get_marketplace_listing("L1") is None


# ---------------------------------------------------------------------------
# publish_to_marketplace
# ---------------------------------------------------------------------------
class TestPublishToMarketplace:
    def _setup_db(self, *, clipping_data: dict | None = None):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        # collection("clippings") e collection("marketplace") devem retornar
        # refs distintos para isolar asserts.
        clip_col = MagicMock(name="clip_col")
        mp_col = MagicMock(name="mp_col")
        db.collection.side_effect = lambda name: {
            "clippings": clip_col,
            "marketplace": mp_col,
        }[name]
        clip_doc_ref = clip_col.document.return_value
        clip_doc_ref.get.return_value = _mock_doc(
            "clip-1", clipping_data if clipping_data is not None else _clipping_doc()
        )
        # marketplace.document() (sem id) gera novo ref com .id
        new_listing_ref = MagicMock(name="new_listing_ref", id="new-listing-id")
        mp_col.document.return_value = new_listing_ref
        batch = db.batch.return_value
        return ds, db, clip_doc_ref, new_listing_ref, batch

    def test_happy_path_creates_listing_and_updates_clipping(self):
        ds, db, clip_ref, listing_ref, batch = self._setup_db()
        result = ds.publish_to_marketplace(
            user_id="author-1",
            clipping_id="clip-1",
            name="Meu Listing",
            description="desc nova",
        )
        assert result["id"] == "new-listing-id"
        assert result["name"] == "Meu Listing"
        assert result["sourceClippingId"] == "clip-1"
        assert result["likeCount"] == 0
        assert result["active"] is True

        # batch.set no listing + batch.update no clipping + commit
        batch.set.assert_called_once()
        set_args = batch.set.call_args
        assert set_args.args[0] is listing_ref
        assert set_args.args[1]["authorUserId"] == "author-1"

        batch.update.assert_called_once()
        upd_args = batch.update.call_args
        assert upd_args.args[0] is clip_ref
        assert upd_args.args[1] == {
            "publishedToMarketplace": True,
            "marketplaceListingId": "new-listing-id",
            "description": "desc nova",
        }
        batch.commit.assert_called_once()

    def test_rejects_when_clipping_missing(self):
        ds, db, clip_ref, *_ = self._setup_db()
        clip_ref.get.return_value = _mock_doc("clip-1", {}, exists=False)
        with pytest.raises(UnauthorizedError, match="CLIPPING_NOT_FOUND"):
            ds.publish_to_marketplace(
                user_id="author-1",
                clipping_id="clip-1",
                name="N",
                description=None,
            )

    def test_rejects_when_user_is_not_author(self):
        ds, db, *_ = self._setup_db(
            clipping_data=_clipping_doc(authorUserId="someone-else")
        )
        with pytest.raises(UnauthorizedError, match="FORBIDDEN"):
            ds.publish_to_marketplace(
                user_id="author-1",
                clipping_id="clip-1",
                name="N",
                description=None,
            )

    def test_rejects_when_already_published(self):
        ds, db, *_ = self._setup_db(
            clipping_data=_clipping_doc(publishedToMarketplace=True)
        )
        with pytest.raises(ValueError, match="ALREADY_PUBLISHED"):
            ds.publish_to_marketplace(
                user_id="author-1",
                clipping_id="clip-1",
                name="N",
                description=None,
            )

    def test_rejects_when_no_recortes(self):
        ds, db, *_ = self._setup_db(
            clipping_data=_clipping_doc(recortes=[])
        )
        with pytest.raises(ValueError, match="EMPTY_RECORTES"):
            ds.publish_to_marketplace(
                user_id="author-1",
                clipping_id="clip-1",
                name="N",
                description=None,
            )

    def test_rejects_when_recorte_missing_title(self):
        ds, db, *_ = self._setup_db(
            clipping_data=_clipping_doc(
                recortes=[{"id": "r1", "title": "   "}]
            )
        )
        with pytest.raises(ValueError, match="RECORTE_MISSING_TITLE"):
            ds.publish_to_marketplace(
                user_id="author-1",
                clipping_id="clip-1",
                name="N",
                description=None,
            )


# ---------------------------------------------------------------------------
# unpublish_from_marketplace
# ---------------------------------------------------------------------------
class TestUnpublishFromMarketplace:
    def test_happy_path_deactivates_listing_and_resets_clipping(self):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        clip_col = MagicMock(name="clip_col")
        mp_col = MagicMock(name="mp_col")
        db.collection.side_effect = lambda name: {
            "clippings": clip_col,
            "marketplace": mp_col,
        }[name]
        listing_ref = mp_col.document.return_value
        listing_ref.get.return_value = _mock_doc(
            "L1", _listing_doc(sourceClippingId="clip-src")
        )
        clip_ref = clip_col.document.return_value
        batch = db.batch.return_value

        ok = ds.unpublish_from_marketplace("L1")
        assert ok is True

        # listing desativado + clipping reset
        update_calls = batch.update.call_args_list
        assert len(update_calls) == 2
        assert update_calls[0].args[0] is listing_ref
        assert update_calls[0].args[1] == {"active": False}
        assert update_calls[1].args[0] is clip_ref
        assert update_calls[1].args[1] == {
            "publishedToMarketplace": False,
            "marketplaceListingId": None,
        }
        batch.commit.assert_called_once()

    def test_returns_false_when_listing_missing(self):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        db.collection.return_value.document.return_value.get.return_value = (
            _mock_doc("L1", {}, exists=False)
        )
        ok = ds.unpublish_from_marketplace("L1")
        assert ok is False
        db.batch.assert_not_called()

    def test_succeeds_when_source_clipping_missing(self):
        # Regressao: clipping fonte excluido apos a publicacao. O batch.update
        # num doc inexistente falhava o batch INTEIRO ("No document to update")
        # e quebrava o unpublish. Agora o reset do clipping e pulado e o
        # soft-delete do listing commita normalmente.
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        clip_col = MagicMock(name="clip_col")
        mp_col = MagicMock(name="mp_col")
        db.collection.side_effect = lambda name: {
            "clippings": clip_col,
            "marketplace": mp_col,
        }[name]
        listing_ref = mp_col.document.return_value
        listing_ref.get.return_value = _mock_doc(
            "L1", _listing_doc(sourceClippingId="clip-deleted")
        )
        clip_ref = clip_col.document.return_value
        clip_ref.get.return_value = _mock_doc("clip-deleted", {}, exists=False)
        batch = db.batch.return_value

        ok = ds.unpublish_from_marketplace("L1")
        assert ok is True

        # So o listing e desativado; o clipping inexistente NAO entra no batch.
        update_calls = batch.update.call_args_list
        assert len(update_calls) == 1
        assert update_calls[0].args[0] is listing_ref
        assert update_calls[0].args[1] == {"active": False}
        batch.commit.assert_called_once()


# ---------------------------------------------------------------------------
# clone_marketplace_listing
# ---------------------------------------------------------------------------
class TestCloneMarketplaceListing:
    def test_happy_path_creates_clipping_and_subscription(self):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        mp_col = MagicMock(name="mp_col")
        clip_col = MagicMock(name="clip_col")
        sub_col = MagicMock(name="sub_col")
        db.collection.side_effect = lambda name: {
            "marketplace": mp_col,
            "clippings": clip_col,
            "subscriptions": sub_col,
        }[name]

        listing_ref = mp_col.document.return_value
        listing_ref.get.return_value = _mock_doc(
            "L1",
            _listing_doc(
                name="Original",
                description="d",
                recortes=[{"id": "r1", "title": "Tema"}],
                prompt="p",
            ),
        )
        new_clip_ref = MagicMock(id="new-clip-id")
        new_sub_ref = MagicMock(id="new-sub-id")
        clip_col.document.return_value = new_clip_ref
        sub_col.document.return_value = new_sub_ref
        batch = db.batch.return_value

        result = ds.clone_marketplace_listing("user-2", "L1")

        assert isinstance(result, ClippingData)
        assert result.id == "new-clip-id"
        assert result.name == "Original"
        assert result.author_user_id == "user-2"

        # 2 sets (clipping + subscription) + 1 update (cloneCount) + commit
        assert batch.set.call_count == 2
        assert batch.update.call_count == 1
        # cloneCount update aponta para listing_ref
        upd_args = batch.update.call_args
        assert upd_args.args[0] is listing_ref
        assert "cloneCount" in upd_args.args[1]
        batch.commit.assert_called_once()

    def test_rejects_when_listing_missing(self):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        db.collection.return_value.document.return_value.get.return_value = (
            _mock_doc("L1", {}, exists=False)
        )
        with pytest.raises(UnauthorizedError, match="LISTING_NOT_FOUND"):
            ds.clone_marketplace_listing("user-2", "L1")

    def test_rejects_when_listing_inactive(self):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        db.collection.return_value.document.return_value.get.return_value = (
            _mock_doc("L1", _listing_doc(active=False))
        )
        with pytest.raises(UnauthorizedError, match="LISTING_INACTIVE"):
            ds.clone_marketplace_listing("user-2", "L1")


# ---------------------------------------------------------------------------
# has_liked_listing / has_followed_listing
# ---------------------------------------------------------------------------
class TestHasLikedListing:
    def test_true_when_like_doc_exists(self):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        like_ref = (
            db.collection.return_value.document.return_value.collection.return_value.document.return_value
        )
        like_ref.get.return_value = _mock_doc("user-1", {}, exists=True)
        assert ds.has_liked_listing("user-1", "L1") is True

    def test_false_when_like_doc_missing(self):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        like_ref = (
            db.collection.return_value.document.return_value.collection.return_value.document.return_value
        )
        like_ref.get.return_value = _mock_doc("user-1", {}, exists=False)
        assert ds.has_liked_listing("user-1", "L1") is False


class TestHasFollowedListing:
    def test_true_when_subscription_exists(self):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        mp_col = MagicMock(name="mp_col")
        sub_col = MagicMock(name="sub_col")
        db.collection.side_effect = lambda name: {
            "marketplace": mp_col,
            "subscriptions": sub_col,
        }[name]
        mp_col.document.return_value.get.return_value = _mock_doc(
            "L1", _listing_doc(sourceClippingId="clip-src")
        )
        # subscriptions: where chain -> limit -> stream retorna 1
        q = sub_col.where.return_value.where.return_value.where.return_value
        q.limit.return_value.stream.return_value = iter(
            [_mock_doc("sub-1", {})]
        )
        assert ds.has_followed_listing("user-1", "L1") is True

    def test_false_when_listing_missing(self):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        db.collection.return_value.document.return_value.get.return_value = (
            _mock_doc("L1", {}, exists=False)
        )
        assert ds.has_followed_listing("user-1", "L1") is False

    def test_false_when_no_subscription(self):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        mp_col = MagicMock(name="mp_col")
        sub_col = MagicMock(name="sub_col")
        db.collection.side_effect = lambda name: {
            "marketplace": mp_col,
            "subscriptions": sub_col,
        }[name]
        mp_col.document.return_value.get.return_value = _mock_doc(
            "L1", _listing_doc(sourceClippingId="clip-src")
        )
        q = sub_col.where.return_value.where.return_value.where.return_value
        q.limit.return_value.stream.return_value = iter([])
        assert ds.has_followed_listing("user-1", "L1") is False


# ---------------------------------------------------------------------------
# toggle_like_marketplace
# ---------------------------------------------------------------------------
class TestToggleLikeMarketplace:
    def _setup(self, *, listing_doc: dict | None = None):
        db = MagicMock()
        ds = FirestoreDatasource(db=db)
        listing_ref = db.collection.return_value.document.return_value
        listing_ref.get.return_value = _mock_doc(
            "L1", listing_doc if listing_doc is not None else _listing_doc()
        )
        like_ref = listing_ref.collection.return_value.document.return_value
        return ds, db, listing_ref, like_ref

    def test_creates_like_when_absent(self):
        ds, db, listing_ref, like_ref = self._setup()
        like_ref.get.return_value = _mock_doc("user-1", {}, exists=False)
        result = ds.toggle_like_marketplace("user-1", "L1")
        assert result is True
        like_ref.set.assert_called_once()
        listing_ref.update.assert_called_once()
        assert "likeCount" in listing_ref.update.call_args.args[0]

    def test_deletes_like_when_present(self):
        ds, db, listing_ref, like_ref = self._setup(
            listing_doc=_listing_doc(likeCount=3)
        )
        like_ref.get.return_value = _mock_doc("user-1", {}, exists=True)
        result = ds.toggle_like_marketplace("user-1", "L1")
        assert result is False
        like_ref.delete.assert_called_once()
        listing_ref.update.assert_called_once()

    def test_no_decrement_when_count_already_zero(self):
        ds, db, listing_ref, like_ref = self._setup(
            listing_doc=_listing_doc(likeCount=0)
        )
        like_ref.get.return_value = _mock_doc("user-1", {}, exists=True)
        result = ds.toggle_like_marketplace("user-1", "L1")
        assert result is False
        like_ref.delete.assert_called_once()
        listing_ref.update.assert_not_called()

    def test_rejects_when_listing_missing(self):
        ds, db, listing_ref, _ = self._setup()
        listing_ref.get.return_value = _mock_doc("L1", {}, exists=False)
        with pytest.raises(UnauthorizedError, match="LISTING_NOT_FOUND"):
            ds.toggle_like_marketplace("user-1", "L1")

    def test_rejects_when_listing_inactive(self):
        ds, db, listing_ref, _ = self._setup(
            listing_doc=_listing_doc(active=False)
        )
        with pytest.raises(UnauthorizedError, match="LISTING_INACTIVE"):
            ds.toggle_like_marketplace("user-1", "L1")
