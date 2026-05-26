"""Datasource Firestore para clippings e marketplace listings.

Fase A1 — Foundation
--------------------
Os models de dados aqui sao Pydantic `BaseModel` com `Field(alias=...)` para
fechar a fronteira camelCase (Firestore) <-> snake_case (Python). Isso resolve
o bug historico em que campos como `scheduleTime`, `deliveryChannels` e
`createdAt` no Firestore nao eram parseados (o dataclass esperava
`schedule_time`/`delivery_channels`/`created_at`).

A configuracao `populate_by_name=True` permite que tanto a forma camelCase
(producao) quanto a forma snake_case (mocks legados) sejam aceitas, sem quebrar
os testes existentes.

Para gravar no Firestore, use `model.model_dump(by_alias=True)` — isso produz
um dict camelCase, que e o formato canonico das colecoes (veja
`PLANO-ATUALIZACAO-v2.md` §8.0).
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class DeliveryChannelsData(BaseModel):
    """Sub-modelo dos canais de entrega de um clipping/subscription.

    Note: deliberadamente *sem* `webhook` nesta fase — sera adicionado em A5
    (ver PLANO-ATUALIZACAO-v2.md §5 Fase A5).
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    email: bool = False
    telegram: bool = False
    push: bool = False


class RecorteData(BaseModel):
    """Sub-modelo de um recorte (tema/agencia/keyword)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = ""
    title: str = ""
    themes: list[str] = Field(default_factory=list)
    agencies: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class ClippingData(BaseModel):
    """Modelo canonico de um clipping.

    Aceita tanto camelCase (Firestore producao) quanto snake_case
    (mocks legados). Sempre escreve camelCase via `model_dump(by_alias=True)`.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    name: str = ""
    description: Optional[str] = None
    # `recortes` e `delivery_channels` permanecem como dict por enquanto: os
    # 110 testes existentes pre-A1 montam fixtures com `list[dict]` e os
    # resolvers acessam `r.get("id")` etc. Migrar para `list[RecorteData]`
    # e `DeliveryChannelsData` aqui exigiria reescrever ~30 testes alheios
    # ao escopo de A1. A escolha do brief (PLANO-ATUALIZACAO-v2.md §5 Fase
    # A1, exemplo de modelo) e manter `list[dict]` / `Optional[dict]` neste
    # momento e fazer a migracao em A2/A3.
    recortes: list[dict] = Field(default_factory=list)
    prompt: Optional[str] = None
    schedule_time: Optional[str] = Field(default=None, alias="scheduleTime")
    delivery_channels: Optional[dict] = Field(default=None, alias="deliveryChannels")
    active: bool = True
    created_at: Optional[datetime] = Field(default=None, alias="createdAt")
    updated_at: Optional[datetime] = Field(default=None, alias="updatedAt")


class MarketplaceListingData(BaseModel):
    """Modelo canonico de um listing de marketplace.

    `marketplace/{id}` no Firestore — ver §8.0 do PLANO-ATUALIZACAO-v2.md.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    author_user_id: str = Field(default="", alias="authorUserId")
    author_display_name: str = Field(default="", alias="authorDisplayName")
    source_clipping_id: str = Field(default="", alias="sourceClippingId")
    name: str = ""
    description: Optional[str] = None
    recortes: list[dict] = Field(default_factory=list)
    prompt: Optional[str] = None
    like_count: int = Field(default=0, alias="likeCount")
    follower_count: int = Field(default=0, alias="followerCount")
    clone_count: int = Field(default=0, alias="cloneCount")
    published_at: Optional[datetime] = Field(default=None, alias="publishedAt")
    updated_at: Optional[datetime] = Field(default=None, alias="updatedAt")
    active: bool = True


MAX_CLIPPINGS_PER_USER = 10


class MaxClippingsError(Exception):
    pass


class FirestoreDatasource:
    def __init__(self, db: Any):
        self._db = db

    def _user_clippings_ref(self, user_id: str):
        return self._db.collection("users").document(user_id).collection("clippings")

    def _doc_to_clipping(self, doc_id: str, data: dict) -> ClippingData:
        # `model_validate` aceita tanto camelCase (alias) quanto snake_case
        # (populate_by_name=True). Campo `id` injetado a partir do doc.id.
        return ClippingData.model_validate({"id": doc_id, **(data or {})})

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
