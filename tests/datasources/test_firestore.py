"""Testes do FirestoreDatasource — Fase A2 (top-level collections).

Substitui a suite anterior que assumia subcoleção `users/{u}/clippings/{c}`.
Agora a leitura passa por `subscriptions where userId == X` + batch get em
`clippings/{id}` (D3 do PLANO-ATUALIZACAO-v2.md).

Estrutura canônica (§8.0 do plano):
- `clippings/{id}`: doc do clipping; *não* tem `deliveryChannels`.
- `subscriptions/{id}`: link user <-> clipping; carrega `role`, `deliveryChannels`,
  `extraEmails`, `webhookUrl`, `active`.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, call

import pytest

from graphql_api.datasources.firestore import (
    ClippingData,
    FirestoreDatasource,
    MyClippingResult,
    SubscriptionData,
    UnauthorizedError,
)

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers de mock
# ---------------------------------------------------------------------------
def _mock_doc(doc_id: str, data: dict, exists: bool = True):
    """Cria um snapshot de doc Firestore."""
    doc = MagicMock()
    doc.id = doc_id
    doc.exists = exists
    doc.to_dict.return_value = data
    # Para batch get(): get() em um DocumentReference retorna o snapshot.
    return doc


def _clipping_doc(**overrides) -> dict:
    base = {
        "name": "Meu Clipping",
        "description": "desc",
        "recortes": [],
        "prompt": None,
        "scheduleTime": "08:00",
        "active": True,
        "authorUserId": "user-author",
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    base.update(overrides)
    return base


def _subscription_doc(**overrides) -> dict:
    base = {
        "clippingId": "clip-1",
        "userId": "user-author",
        "role": "author",
        "deliveryChannels": {
            "email": True,
            "telegram": False,
            "push": False,
            "webhook": False,
        },
        "extraEmails": [],
        "webhookUrl": "",
        "active": True,
        "subscribedAt": NOW,
    }
    base.update(overrides)
    return base


class FakeFirestore:
    """Fake Firestore que aceita refs por nome de coleção.

    Suporta:
    - `db.collection("clippings")` e `db.collection("subscriptions")`.
    - `.document(id)` retorna um DocRef mock.
    - `.document()` (sem id) retorna um DocRef com `.id` gerado.
    - `.where(field, op, value)` retorna um Query encadeável; `.stream()` itera docs.
    - `db.batch()` retorna um WriteBatch que registra operações e comita.
    """

    def __init__(self):
        # collection_name -> {doc_id: doc_data}
        self.store: dict[str, dict[str, dict]] = {
            "clippings": {},
            "subscriptions": {},
            "releases": {},
        }
        self._id_counter = 0
        self.batches_committed = 0
        self.batch_should_fail = False

    def _next_id(self, prefix: str = "auto") -> str:
        self._id_counter += 1
        return f"{prefix}-{self._id_counter}"

    def collection(self, name: str):
        return _FakeCollection(self, name)

    def batch(self):
        return _FakeBatch(self)


class _FakeCollection:
    def __init__(self, db: FakeFirestore, name: str):
        self.db = db
        self.name = name

    def document(self, doc_id: str | None = None):
        if doc_id is None:
            doc_id = self.db._next_id(self.name)
        return _FakeDocRef(self.db, self.name, doc_id)

    def where(self, field: str, op: str, value):
        # filter() em versões novas tem assinatura diferente; aqui só replicamos `where`.
        return _FakeQuery(self.db, self.name, [(field, op, value)])

    def order_by(self, field: str, direction: str = "ASCENDING"):
        return _FakeQuery(self.db, self.name, [], order=(field, direction))

    def limit(self, n: int):
        return _FakeQuery(self.db, self.name, [], limit=n)

    def stream(self):
        for doc_id, data in list(self.db.store[self.name].items()):
            yield _mock_doc(doc_id, data)


class _FakeQuery:
    def __init__(
        self,
        db: FakeFirestore,
        collection: str,
        filters: list,
        order: tuple | None = None,
        limit_n: int | None = None,
        *,
        limit: int | None = None,
    ):
        self.db = db
        self.collection = collection
        self.filters = filters
        self.order = order
        # Aceita tanto `limit_n` (interno) quanto `limit` (kwarg externo)
        # para retrocompat com chamadas existentes.
        self.limit_n = limit if limit is not None else limit_n

    def where(self, field: str, op: str, value):
        return _FakeQuery(
            self.db,
            self.collection,
            self.filters + [(field, op, value)],
            order=self.order,
            limit_n=self.limit_n,
        )

    def order_by(self, field: str, direction: str = "ASCENDING"):
        return _FakeQuery(
            self.db,
            self.collection,
            self.filters,
            order=(field, direction),
            limit_n=self.limit_n,
        )

    def limit(self, n: int):
        return _FakeQuery(
            self.db,
            self.collection,
            self.filters,
            order=self.order,
            limit_n=n,
        )

    def stream(self):
        items = [
            (doc_id, data)
            for doc_id, data in list(self.db.store[self.collection].items())
            if all(self._match(data, f, op, v) for f, op, v in self.filters)
        ]
        if self.order is not None:
            field, direction = self.order
            reverse = direction.upper().startswith("DESC")
            # `None` ordena no final; usamos sort key tupla (none_flag, value)
            def sort_key(item):
                v = item[1].get(field)
                return (v is None, v)
            items.sort(key=sort_key, reverse=reverse)
        if self.limit_n is not None:
            items = items[: self.limit_n]
        for doc_id, data in items:
            yield _mock_doc(doc_id, data)

    @staticmethod
    def _match(data: dict, field: str, op: str, value) -> bool:
        cur = data.get(field)
        if op == "==":
            return cur == value
        if op == "!=":
            return cur != value
        if op == "in":
            return cur in value
        if op == "<":
            if cur is None or value is None:
                return False
            return cur < value
        if op == "<=":
            if cur is None or value is None:
                return False
            return cur <= value
        if op == ">":
            if cur is None or value is None:
                return False
            return cur > value
        if op == ">=":
            if cur is None or value is None:
                return False
            return cur >= value
        raise NotImplementedError(op)


class _FakeDocRef:
    def __init__(self, db: FakeFirestore, collection: str, doc_id: str):
        self.db = db
        self.collection = collection
        self.id = doc_id

    def get(self):
        data = self.db.store[self.collection].get(self.id)
        if data is None:
            return _mock_doc(self.id, {}, exists=False)
        return _mock_doc(self.id, data)

    def set(self, data: dict):
        self.db.store[self.collection][self.id] = dict(data)

    def update(self, data: dict):
        self.db.store[self.collection].setdefault(self.id, {}).update(data)

    def delete(self):
        self.db.store[self.collection].pop(self.id, None)


class _FakeBatch:
    def __init__(self, db: FakeFirestore):
        self.db = db
        self._ops: list[tuple] = []

    def set(self, ref: _FakeDocRef, data: dict):
        self._ops.append(("set", ref, dict(data)))

    def update(self, ref: _FakeDocRef, data: dict):
        self._ops.append(("update", ref, dict(data)))

    def delete(self, ref: _FakeDocRef):
        self._ops.append(("delete", ref, None))

    def commit(self):
        if self.db.batch_should_fail:
            raise RuntimeError("batch boom")
        for op, ref, data in self._ops:
            if op == "set":
                ref.set(data)
            elif op == "update":
                ref.update(data)
            elif op == "delete":
                ref.delete()
        self.db.batches_committed += 1


# ---------------------------------------------------------------------------
# 1. SubscriptionData Pydantic
# ---------------------------------------------------------------------------
class TestSubscriptionData:
    def test_subscription_data_pydantic_camelcase(self):
        sub = SubscriptionData.model_validate(
            {
                "id": "sub-1",
                "clippingId": "clip-1",
                "userId": "user-1",
                "role": "author",
                "deliveryChannels": {
                    "email": True,
                    "telegram": False,
                    "push": False,
                    "webhook": False,
                },
                "extraEmails": ["a@b.com"],
                "webhookUrl": "",
                "active": True,
                "subscribedAt": NOW,
            }
        )
        assert sub.id == "sub-1"
        assert sub.clipping_id == "clip-1"
        assert sub.user_id == "user-1"
        assert sub.role == "author"
        assert sub.delivery_channels == {
            "email": True,
            "telegram": False,
            "push": False,
            "webhook": False,
        }
        assert sub.extra_emails == ["a@b.com"]
        assert sub.webhook_url == ""
        assert sub.active is True
        assert sub.subscribed_at == NOW

    def test_subscription_data_writes_camelcase(self):
        sub = SubscriptionData(
            id="sub-1",
            clipping_id="clip-1",
            user_id="user-1",
            role="subscriber",
            delivery_channels={
                "email": True,
                "telegram": False,
                "push": False,
                "webhook": False,
            },
            extra_emails=[],
            webhook_url="",
            active=True,
            subscribed_at=NOW,
        )
        dumped = sub.model_dump(by_alias=True)
        assert dumped["clippingId"] == "clip-1"
        assert dumped["userId"] == "user-1"
        assert dumped["deliveryChannels"]["email"] is True
        assert dumped["extraEmails"] == []
        assert dumped["webhookUrl"] == ""
        assert dumped["subscribedAt"] == NOW
        assert "clipping_id" not in dumped
        assert "user_id" not in dumped


# ---------------------------------------------------------------------------
# 2. get_my_clippings via subscriptions
# ---------------------------------------------------------------------------
class TestGetMyClippings:
    def test_get_my_clippings_via_subscriptions(self):
        db = FakeFirestore()
        # 2 clippings
        db.store["clippings"]["clip-author"] = _clipping_doc(
            name="Owned", authorUserId="user-1"
        )
        db.store["clippings"]["clip-sub"] = _clipping_doc(
            name="Followed", authorUserId="user-99"
        )
        # 2 subs do user-1: 1 author + 1 subscriber
        db.store["subscriptions"]["sub-a"] = _subscription_doc(
            clippingId="clip-author", userId="user-1", role="author"
        )
        db.store["subscriptions"]["sub-b"] = _subscription_doc(
            clippingId="clip-sub", userId="user-1", role="subscriber"
        )

        ds = FirestoreDatasource(db)
        results = ds.get_my_clippings("user-1")

        assert len(results) == 2
        # Cada item é (ClippingData, SubscriptionData)
        clipping_ids = {r.clipping.id for r in results}
        assert clipping_ids == {"clip-author", "clip-sub"}
        roles = {r.subscription.role for r in results}
        assert roles == {"author", "subscriber"}

    def test_get_my_clippings_empty(self):
        db = FakeFirestore()
        ds = FirestoreDatasource(db)
        results = ds.get_my_clippings("user-x")
        assert results == []

    def test_get_my_clippings_inactive_sub_excluded(self):
        db = FakeFirestore()
        db.store["clippings"]["clip-1"] = _clipping_doc(name="Active")
        db.store["clippings"]["clip-2"] = _clipping_doc(name="DeadSub")
        db.store["subscriptions"]["sub-1"] = _subscription_doc(
            clippingId="clip-1", userId="user-1", active=True
        )
        db.store["subscriptions"]["sub-2"] = _subscription_doc(
            clippingId="clip-2", userId="user-1", active=False
        )
        ds = FirestoreDatasource(db)
        results = ds.get_my_clippings("user-1")
        assert len(results) == 1
        assert results[0].clipping.id == "clip-1"

    def test_get_my_clippings_skips_missing_clipping_doc(self):
        """Defensivo: sub aponta para clipping que não existe (race condition)."""
        db = FakeFirestore()
        db.store["subscriptions"]["sub-1"] = _subscription_doc(
            clippingId="ghost", userId="user-1"
        )
        ds = FirestoreDatasource(db)
        results = ds.get_my_clippings("user-1")
        assert results == []


# ---------------------------------------------------------------------------
# 3. get_subscription (lookup composto)
# ---------------------------------------------------------------------------
class TestGetSubscription:
    def test_get_subscription_for_clipping_returns_one(self):
        db = FakeFirestore()
        db.store["subscriptions"]["sub-1"] = _subscription_doc(
            clippingId="clip-1", userId="user-1", role="subscriber"
        )
        ds = FirestoreDatasource(db)
        sub = ds.get_subscription("user-1", "clip-1")
        assert sub is not None
        assert sub.role == "subscriber"
        assert sub.clipping_id == "clip-1"
        assert sub.user_id == "user-1"

    def test_get_subscription_for_clipping_returns_none_when_not_subscribed(self):
        db = FakeFirestore()
        ds = FirestoreDatasource(db)
        assert ds.get_subscription("user-x", "clip-x") is None


# ---------------------------------------------------------------------------
# 4. create_clipping — batch atômico
# ---------------------------------------------------------------------------
class TestCreateClipping:
    def test_create_clipping_creates_clipping_and_author_subscription_atomically(self):
        db = FakeFirestore()
        ds = FirestoreDatasource(db)
        data = {
            "name": "Novo",
            "description": "d",
            "recortes": [],
            "delivery_channels": {
                "email": True,
                "telegram": False,
                "push": False,
                "webhook": False,
            },
            "extra_emails": ["x@y.com"],
            "webhook_url": "",
        }

        result = ds.create_clipping("user-1", data)

        assert isinstance(result, ClippingData)
        assert result.name == "Novo"
        assert db.batches_committed == 1
        # 1 clipping + 1 subscription criados
        assert len(db.store["clippings"]) == 1
        assert len(db.store["subscriptions"]) == 1
        # subscription tem role=author
        (sub_doc,) = db.store["subscriptions"].values()
        assert sub_doc["role"] == "author"
        assert sub_doc["userId"] == "user-1"
        assert sub_doc["active"] is True

    def test_create_clipping_rollback_on_failure(self):
        db = FakeFirestore()
        db.batch_should_fail = True
        ds = FirestoreDatasource(db)

        with pytest.raises(RuntimeError):
            ds.create_clipping("user-1", {"name": "X"})

        # nada foi escrito
        assert db.store["clippings"] == {}
        assert db.store["subscriptions"] == {}
        assert db.batches_committed == 0

    def test_create_clipping_clipping_doc_has_no_delivery_channels(self):
        db = FakeFirestore()
        ds = FirestoreDatasource(db)
        ds.create_clipping(
            "user-1",
            {
                "name": "Novo",
                "delivery_channels": {
                    "email": True,
                    "telegram": True,
                    "push": False,
                    "webhook": False,
                },
            },
        )
        (clipping_doc,) = db.store["clippings"].values()
        assert "deliveryChannels" not in clipping_doc
        assert "delivery_channels" not in clipping_doc
        (sub_doc,) = db.store["subscriptions"].values()
        assert sub_doc["deliveryChannels"]["telegram"] is True

    def test_create_clipping_sets_author_user_id_from_caller(self):
        db = FakeFirestore()
        ds = FirestoreDatasource(db)
        ds.create_clipping("user-1", {"name": "n"})
        (clipping_doc,) = db.store["clippings"].values()
        assert clipping_doc["authorUserId"] == "user-1"
        assert clipping_doc["active"] is True
        assert clipping_doc["createdAt"] is not None
        assert clipping_doc["updatedAt"] is not None


# ---------------------------------------------------------------------------
# 5. subscribe / unsubscribe
# ---------------------------------------------------------------------------
class TestSubscribe:
    def test_subscribe_to_clipping_creates_subscriber_sub(self):
        db = FakeFirestore()
        db.store["clippings"]["clip-1"] = _clipping_doc(name="X")
        ds = FirestoreDatasource(db)

        sub = ds.subscribe_to_clipping(
            user_id="user-2",
            clipping_id="clip-1",
            channels={
                "email": True,
                "telegram": False,
                "push": False,
                "webhook": False,
            },
            extra_emails=[],
            webhook_url="",
        )

        assert isinstance(sub, SubscriptionData)
        assert sub.role == "subscriber"
        assert sub.user_id == "user-2"
        assert sub.clipping_id == "clip-1"
        assert sub.delivery_channels["email"] is True
        assert len(db.store["subscriptions"]) == 1

    def test_subscribe_to_clipping_idempotent(self):
        db = FakeFirestore()
        ds = FirestoreDatasource(db)
        ds.subscribe_to_clipping(
            user_id="user-2",
            clipping_id="clip-1",
            channels={
                "email": True,
                "telegram": False,
                "push": False,
                "webhook": False,
            },
            extra_emails=[],
            webhook_url="",
        )
        # Segunda chamada com channels diferentes — atualiza, não duplica
        ds.subscribe_to_clipping(
            user_id="user-2",
            clipping_id="clip-1",
            channels={
                "email": False,
                "telegram": True,
                "push": False,
                "webhook": False,
            },
            extra_emails=["new@x.com"],
            webhook_url="",
        )
        # Ainda só 1 sub
        assert len(db.store["subscriptions"]) == 1
        (sub_doc,) = db.store["subscriptions"].values()
        assert sub_doc["deliveryChannels"]["telegram"] is True
        assert sub_doc["deliveryChannels"]["email"] is False
        assert sub_doc["extraEmails"] == ["new@x.com"]

    def test_subscribe_with_webhook_channel_persists_webhook_url(self):
        db = FakeFirestore()
        ds = FirestoreDatasource(db)
        ds.subscribe_to_clipping(
            user_id="user-2",
            clipping_id="clip-1",
            channels={
                "email": False,
                "telegram": False,
                "push": False,
                "webhook": True,
            },
            extra_emails=[],
            webhook_url="https://example.com/hook",
        )
        (sub_doc,) = db.store["subscriptions"].values()
        assert sub_doc["webhookUrl"] == "https://example.com/hook"
        assert sub_doc["deliveryChannels"]["webhook"] is True


class TestUnsubscribe:
    def test_unsubscribe_subscriber_removes_subscription(self):
        db = FakeFirestore()
        db.store["subscriptions"]["sub-1"] = _subscription_doc(
            clippingId="clip-1", userId="user-2", role="subscriber"
        )
        ds = FirestoreDatasource(db)
        result = ds.unsubscribe_from_clipping("user-2", "clip-1")
        assert result is True
        assert db.store["subscriptions"] == {}

    def test_unsubscribe_author_raises_unauthorized(self):
        db = FakeFirestore()
        db.store["subscriptions"]["sub-1"] = _subscription_doc(
            clippingId="clip-1", userId="user-1", role="author"
        )
        ds = FirestoreDatasource(db)
        with pytest.raises(UnauthorizedError):
            ds.unsubscribe_from_clipping("user-1", "clip-1")

    def test_unsubscribe_when_not_subscribed_returns_false(self):
        db = FakeFirestore()
        ds = FirestoreDatasource(db)
        assert ds.unsubscribe_from_clipping("ghost", "ghost") is False


# ---------------------------------------------------------------------------
# 6. delete_clipping — cascade
# ---------------------------------------------------------------------------
class TestDeleteClipping:
    def test_delete_clipping_cascades_all_subscriptions(self):
        db = FakeFirestore()
        db.store["clippings"]["clip-1"] = _clipping_doc(authorUserId="user-1")
        db.store["subscriptions"]["sub-a"] = _subscription_doc(
            clippingId="clip-1", userId="user-1", role="author"
        )
        db.store["subscriptions"]["sub-b"] = _subscription_doc(
            clippingId="clip-1", userId="user-2", role="subscriber"
        )
        db.store["subscriptions"]["sub-c"] = _subscription_doc(
            clippingId="clip-other", userId="user-1", role="author"
        )
        ds = FirestoreDatasource(db)
        result = ds.delete_clipping("user-1", "clip-1")

        assert result is True
        assert "clip-1" not in db.store["clippings"]
        # apenas a sub do clip-other sobrevive
        remaining = list(db.store["subscriptions"].values())
        assert len(remaining) == 1
        assert remaining[0]["clippingId"] == "clip-other"

    def test_delete_clipping_non_author_raises_unauthorized(self):
        db = FakeFirestore()
        db.store["clippings"]["clip-1"] = _clipping_doc(authorUserId="user-1")
        ds = FirestoreDatasource(db)
        with pytest.raises(UnauthorizedError):
            ds.delete_clipping("user-2", "clip-1")

    def test_delete_clipping_returns_false_when_missing(self):
        db = FakeFirestore()
        ds = FirestoreDatasource(db)
        assert ds.delete_clipping("user-1", "ghost") is False


# ---------------------------------------------------------------------------
# 7. get_clipping (single, top-level)
# ---------------------------------------------------------------------------
class TestGetClipping:
    def test_get_clipping_returns_data(self):
        db = FakeFirestore()
        db.store["clippings"]["clip-1"] = _clipping_doc(name="X")
        ds = FirestoreDatasource(db)
        clipping = ds.get_clipping("clip-1")
        assert clipping is not None
        assert clipping.id == "clip-1"
        assert clipping.name == "X"

    def test_get_clipping_returns_none_when_missing(self):
        db = FakeFirestore()
        ds = FirestoreDatasource(db)
        assert ds.get_clipping("nope") is None


# ---------------------------------------------------------------------------
# 8. update_clipping
# ---------------------------------------------------------------------------
class TestUpdateClipping:
    def test_update_clipping_modifies_only_clipping_doc(self):
        db = FakeFirestore()
        db.store["clippings"]["clip-1"] = _clipping_doc(
            name="Old", authorUserId="user-1"
        )
        db.store["subscriptions"]["sub-1"] = _subscription_doc(
            clippingId="clip-1", userId="user-1", role="author"
        )
        original_sub = dict(db.store["subscriptions"]["sub-1"])
        ds = FirestoreDatasource(db)

        result = ds.update_clipping("user-1", "clip-1", {"name": "New"})

        assert isinstance(result, ClippingData)
        assert result.name == "New"
        assert db.store["clippings"]["clip-1"]["name"] == "New"
        # subscription intacta
        assert db.store["subscriptions"]["sub-1"] == original_sub

    def test_update_clipping_non_author_raises_unauthorized(self):
        db = FakeFirestore()
        db.store["clippings"]["clip-1"] = _clipping_doc(authorUserId="user-1")
        ds = FirestoreDatasource(db)
        with pytest.raises(UnauthorizedError):
            ds.update_clipping("user-2", "clip-1", {"name": "Hacker"})


# ---------------------------------------------------------------------------
# 9. update_my_subscription
# ---------------------------------------------------------------------------
class TestUpdateMySubscription:
    def test_update_my_subscription_changes_channels(self):
        db = FakeFirestore()
        db.store["subscriptions"]["sub-1"] = _subscription_doc(
            clippingId="clip-1", userId="user-2", role="subscriber"
        )
        ds = FirestoreDatasource(db)
        result = ds.update_my_subscription(
            user_id="user-2",
            clipping_id="clip-1",
            channels={
                "email": False,
                "telegram": True,
                "push": True,
                "webhook": False,
            },
            extra_emails=["new@x.com"],
            webhook_url="",
        )
        assert isinstance(result, SubscriptionData)
        assert result.delivery_channels["telegram"] is True
        assert result.delivery_channels["push"] is True
        assert result.extra_emails == ["new@x.com"]

    def test_update_my_subscription_when_missing_returns_none(self):
        db = FakeFirestore()
        ds = FirestoreDatasource(db)
        result = ds.update_my_subscription(
            user_id="user-x",
            clipping_id="clip-x",
            channels={"email": True, "telegram": False, "push": False, "webhook": False},
            extra_emails=[],
            webhook_url="",
        )
        assert result is None


# ---------------------------------------------------------------------------
# 10. MAX_CLIPPINGS_PER_USER removido
# ---------------------------------------------------------------------------
class TestMaxClippingsRemoved:
    def test_max_clippings_check_removed(self):
        """`MAX_CLIPPINGS_PER_USER` foi removido — portal usa 0 = desabilitado."""
        import graphql_api.datasources.firestore as fs_mod

        assert not hasattr(fs_mod, "MAX_CLIPPINGS_PER_USER")
        assert not hasattr(fs_mod, "MaxClippingsError")

    def test_can_create_many_clippings_without_block(self):
        db = FakeFirestore()
        ds = FirestoreDatasource(db)
        for i in range(20):
            ds.create_clipping("user-1", {"name": f"clip-{i}"})
        assert len(db.store["clippings"]) == 20


# ---------------------------------------------------------------------------
# 11. Compat: parsing camelCase de campos novos
# ---------------------------------------------------------------------------
class TestFirestoreCamelCaseDoc:
    def test_firestore_camelcase_doc_parses_correctly(self):
        """Doc com campos novos (scheduleTime, nextRunAt, extraEmails) parseia.

        Campos novos (schedule, nextRunAt etc.) ainda *não* fazem parte do
        modelo canônico — A4 adicionará. Aqui só comprovamos que extras não
        quebram parsing.
        """
        db = FakeFirestore()
        db.store["clippings"]["clip-1"] = _clipping_doc(
            scheduleTime="07:30",
            nextRunAt=NOW,
            extraEmails=["a@b.com"],
        )
        db.store["subscriptions"]["sub-1"] = _subscription_doc(
            clippingId="clip-1", userId="user-1", role="author"
        )
        ds = FirestoreDatasource(db)
        results = ds.get_my_clippings("user-1")
        assert len(results) == 1
        assert results[0].clipping.schedule_time == "07:30"


# ---------------------------------------------------------------------------
# Fase A5 — Releases datasource
# ---------------------------------------------------------------------------
def _release_doc(**overrides) -> dict:
    base = {
        "clippingId": "clip-1",
        "userId": "user-1",
        "clippingName": "Meu Clipping",
        "digest": '{"intro":"texto"}',
        "digestHtml": "<p>texto</p>",
        "articlesCount": 5,
        "createdAt": NOW,
        "releaseUrl": "/clipping/release/rel-1",
        "refTime": NOW,
        "sinceHours": 24,
    }
    base.update(overrides)
    return base


class TestReleasesDatasource:
    def test_release_data_parses_camelcase_doc(self):
        from graphql_api.datasources.firestore import ReleaseData

        rel = ReleaseData.model_validate({"id": "rel-1", **_release_doc()})
        assert rel.id == "rel-1"
        assert rel.clipping_id == "clip-1"
        assert rel.clipping_name == "Meu Clipping"
        assert rel.digest_html == "<p>texto</p>"
        assert rel.articles_count == 5
        assert rel.created_at == NOW
        assert rel.release_url == "/clipping/release/rel-1"
        assert rel.ref_time == NOW
        assert rel.since_hours == 24

    def test_get_releases_returns_only_matching_clipping_id(self):
        db = FakeFirestore()
        db.store["releases"]["rel-a"] = _release_doc(clippingId="clip-1")
        db.store["releases"]["rel-b"] = _release_doc(clippingId="clip-2")
        ds = FirestoreDatasource(db)
        result = ds.get_releases("clip-1", limit=20)
        assert len(result) == 1
        assert result[0].id == "rel-a"

    def test_get_releases_sorted_desc_by_created_at(self):
        from datetime import timedelta

        db = FakeFirestore()
        db.store["releases"]["older"] = _release_doc(
            clippingId="clip-1", createdAt=NOW
        )
        db.store["releases"]["newer"] = _release_doc(
            clippingId="clip-1", createdAt=NOW + timedelta(hours=1)
        )
        ds = FirestoreDatasource(db)
        result = ds.get_releases("clip-1", limit=20)
        # mais recente primeiro
        assert [r.id for r in result] == ["newer", "older"]

    def test_get_releases_respects_limit(self):
        from datetime import timedelta

        db = FakeFirestore()
        for i in range(5):
            db.store["releases"][f"rel-{i}"] = _release_doc(
                clippingId="clip-1",
                createdAt=NOW + timedelta(hours=i),
            )
        ds = FirestoreDatasource(db)
        result = ds.get_releases("clip-1", limit=3)
        assert len(result) == 3

    def test_get_releases_with_before_cursor(self):
        from datetime import timedelta

        db = FakeFirestore()
        t0 = NOW
        t1 = NOW + timedelta(hours=1)
        t2 = NOW + timedelta(hours=2)
        db.store["releases"]["rel-0"] = _release_doc(clippingId="clip-1", createdAt=t0)
        db.store["releases"]["rel-1"] = _release_doc(clippingId="clip-1", createdAt=t1)
        db.store["releases"]["rel-2"] = _release_doc(clippingId="clip-1", createdAt=t2)
        ds = FirestoreDatasource(db)
        # before=t2 → traz só rel-0 e rel-1 (estritamente < t2)
        result = ds.get_releases("clip-1", limit=20, before=t2)
        ids = {r.id for r in result}
        assert ids == {"rel-0", "rel-1"}

    def test_get_releases_empty_when_no_match(self):
        db = FakeFirestore()
        ds = FirestoreDatasource(db)
        result = ds.get_releases("clip-ghost", limit=20)
        assert result == []

    def test_get_releases_batch_returns_map_with_all_clipping_ids(self):
        db = FakeFirestore()
        db.store["releases"]["rel-a"] = _release_doc(clippingId="clip-1")
        db.store["releases"]["rel-b"] = _release_doc(clippingId="clip-2")
        ds = FirestoreDatasource(db)
        result = ds.get_releases_batch(["clip-1", "clip-2", "clip-3"], limit=10)
        assert set(result.keys()) == {"clip-1", "clip-2", "clip-3"}
        assert len(result["clip-1"]) == 1
        assert len(result["clip-2"]) == 1
        assert result["clip-3"] == []
