from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid


@dataclass
class ClippingData:
    id: str
    name: str
    description: Optional[str] = None
    recortes: list[dict] = field(default_factory=list)
    prompt: Optional[str] = None
    schedule_time: Optional[str] = None
    delivery_channels: Optional[dict] = None
    active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


MAX_CLIPPINGS_PER_USER = 10


class MaxClippingsError(Exception):
    pass


class FirestoreDatasource:
    def __init__(self, db: Any):
        self._db = db

    def _user_clippings_ref(self, user_id: str):
        return self._db.collection("users").document(user_id).collection("clippings")

    def _doc_to_clipping(self, doc_id: str, data: dict) -> ClippingData:
        return ClippingData(
            id=doc_id,
            name=data.get("name", ""),
            description=data.get("description"),
            recortes=data.get("recortes", []),
            prompt=data.get("prompt"),
            schedule_time=data.get("schedule_time"),
            delivery_channels=data.get("delivery_channels"),
            active=data.get("active", True),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def get_clippings(self, user_id: str) -> list[ClippingData]:
        ref = self._user_clippings_ref(user_id)
        docs = ref.stream()
        return [self._doc_to_clipping(doc.id, doc.to_dict()) for doc in docs]

    def get_clipping(self, user_id: str, clipping_id: str) -> Optional[ClippingData]:
        ref = self._user_clippings_ref(user_id).document(clipping_id)
        doc = ref.get()
        if not doc.exists:
            return None
        return self._doc_to_clipping(doc.id, doc.to_dict())

    def count_clippings(self, user_id: str) -> int:
        ref = self._user_clippings_ref(user_id)
        docs = ref.stream()
        return sum(1 for _ in docs)

    def create_clipping(self, user_id: str, data: dict) -> ClippingData:
        count = self.count_clippings(user_id)
        if count >= MAX_CLIPPINGS_PER_USER:
            raise MaxClippingsError(
                f"User {user_id} already has {count} clippings (max {MAX_CLIPPINGS_PER_USER})"
            )

        now = datetime.now(tz=timezone.utc)
        clipping_id = str(uuid.uuid4())
        doc_data = {
            **data,
            "active": data.get("active", True),
            "created_at": now,
            "updated_at": now,
        }

        ref = self._user_clippings_ref(user_id).document(clipping_id)
        ref.set(doc_data)

        return self._doc_to_clipping(clipping_id, doc_data)

    def update_clipping(self, user_id: str, clipping_id: str, data: dict) -> ClippingData:
        now = datetime.now(tz=timezone.utc)
        update_data = {**data, "updated_at": now}

        ref = self._user_clippings_ref(user_id).document(clipping_id)
        ref.update(update_data)

        doc = ref.get()
        return self._doc_to_clipping(doc.id, doc.to_dict())

    def delete_clipping(self, user_id: str, clipping_id: str) -> bool:
        ref = self._user_clippings_ref(user_id).document(clipping_id)
        ref.delete()
        return True
