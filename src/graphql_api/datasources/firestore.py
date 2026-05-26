"""Datasource Firestore para clippings, subscriptions e marketplace listings.

Fase A2 — Top-level collections + camelCase boundary
----------------------------------------------------
A leitura de "meus clippings" agora segue D3 do PLANO-ATUALIZACAO-v2.md:
    1) query em `subscriptions where userId == X AND active == True`
    2) batch get em `clippings/{id}` para cada `clippingId` retornado.

Estruturas canônicas (§8.0 do plano):

```
clippings/{id}
  {name, description, recortes[], prompt, active, authorUserId,
   schedule, scheduleTime, nextRunAt, startDate, endDate,
   extraEmails, includeHistory, publishedToMarketplace,
   marketplaceListingId, clonedFrom, createdAt, updatedAt}

subscriptions/{id}
  {clippingId, userId, role: "author"|"subscriber",
   deliveryChannels: {email, telegram, push, webhook},
   extraEmails, webhookUrl, active, subscribedAt}
```

`deliveryChannels` *não* vive mais no clipping — fica na subscription. Isso
permite que múltiplos usuários tenham canais distintos para o mesmo clipping
(autor e subscribers).

Boundary camelCase/snake_case continua via Pydantic `Field(alias=...)`. Para
gravar use `model.model_dump(by_alias=True)` (camelCase canônico).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class DeliveryChannelsData(BaseModel):
    """Sub-modelo dos canais de entrega.

    Fase A5: `webhook` adicionado. Quando `webhook=True`, a subscription
    correspondente deve ter `webhook_url` não vazio (validação cross-field
    fica no resolver, não aqui — o modelo aceita o estado inconsistente
    para permitir parsing defensivo de docs antigos).
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    email: bool = False
    telegram: bool = False
    push: bool = False
    webhook: bool = False


class RecorteData(BaseModel):
    """Sub-modelo de um recorte (tema/agencia/keyword)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = ""
    title: str = ""
    themes: list[str] = Field(default_factory=list)
    agencies: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class ClippingData(BaseModel):
    """Modelo canonico de um clipping (top-level `clippings/{id}`).

    Aceita tanto camelCase (Firestore producao) quanto snake_case
    (mocks legados). Sempre escreve camelCase via `model_dump(by_alias=True)`.

    Fase A4 adicionou os campos cron: `schedule`, `next_run_at`, `start_date`,
    `end_date`, `extra_emails`, `include_history`. `schedule_time` é mantido
    como legacy readable (clippings antigos com slot 30min — `"08:00"`).

    Note: `delivery_channels` permanece no model para retro-compat dos 122
    testes anteriores e dos mocks que ainda passam `deliveryChannels` no doc
    do clipping. Em produção, o campo só vive na subscription — `populate_by_name`
    + `extra="ignore"` deixa o parsing tolerante.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    name: str = ""
    description: Optional[str] = None
    recortes: list[dict] = Field(default_factory=list)
    prompt: Optional[str] = None
    # A4: cron + janela
    schedule: str = ""
    schedule_time: Optional[str] = Field(default=None, alias="scheduleTime")  # legacy
    next_run_at: Optional[datetime] = Field(default=None, alias="nextRunAt")
    start_date: Optional[datetime] = Field(default=None, alias="startDate")
    end_date: Optional[datetime] = Field(default=None, alias="endDate")
    extra_emails: list[str] = Field(default_factory=list, alias="extraEmails")
    include_history: bool = Field(default=False, alias="includeHistory")
    delivery_channels: Optional[dict] = Field(default=None, alias="deliveryChannels")
    active: bool = True
    author_user_id: Optional[str] = Field(default=None, alias="authorUserId")
    created_at: Optional[datetime] = Field(default=None, alias="createdAt")
    updated_at: Optional[datetime] = Field(default=None, alias="updatedAt")


class SubscriptionData(BaseModel):
    """Modelo canonico de uma subscription (top-level `subscriptions/{id}`).

    Liga um usuário a um clipping; carrega os canais de entrega *daquele*
    usuário. `role` distingue autor (criado automaticamente em
    `create_clipping`) de subscriber (criado via `subscribe_to_clipping`).

    Para A2 (sem schema novo), `delivery_channels` é mantido como dict livre
    — A3 introduzirá o tipo Strawberry.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    clipping_id: str = Field(alias="clippingId")
    user_id: str = Field(alias="userId")
    role: Literal["author", "subscriber"]
    delivery_channels: dict = Field(default_factory=dict, alias="deliveryChannels")
    extra_emails: list[str] = Field(default_factory=list, alias="extraEmails")
    webhook_url: str = Field(default="", alias="webhookUrl")
    active: bool = True
    subscribed_at: Optional[datetime] = Field(default=None, alias="subscribedAt")


@dataclass(frozen=True)
class MyClippingResult:
    """Tupla retornada por `get_my_clippings`: clipping + subscription do user.

    A subscription informa o role (autor vs subscriber) e os canais de entrega
    *deste usuário* para o clipping. Resolvers de A3 vão expor isso como
    `Clipping.isAuthor` + `Clipping.mySubscription`.
    """

    clipping: ClippingData
    subscription: SubscriptionData


class ReleaseData(BaseModel):
    """Modelo canonico de um release (top-level `releases/{id}`).

    Releases sao entregas historicas de um clipping: um digest gerado
    periodicamente conforme `schedule`. O portal grava esses docs como
    parte do worker de envio. graphql-api so le.

    Estrutura observada no Firestore producao (camelCase):
      `releases/{id}` -> { clippingId, userId, clippingName, digest,
        digestHtml, articlesCount, createdAt, releaseUrl, refTime,
        sinceHours }

    `createdAt` e o cursor canonico para paginacao (ordenacao desc).
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    clipping_id: str = Field(alias="clippingId")
    user_id: str = Field(default="", alias="userId")
    clipping_name: str = Field(default="", alias="clippingName")
    digest: str = ""
    digest_html: str = Field(default="", alias="digestHtml")
    articles_count: int = Field(default=0, alias="articlesCount")
    created_at: Optional[datetime] = Field(default=None, alias="createdAt")
    release_url: Optional[str] = Field(default=None, alias="releaseUrl")
    ref_time: Optional[datetime] = Field(default=None, alias="refTime")
    since_hours: Optional[int] = Field(default=None, alias="sinceHours")


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


class UnauthorizedError(Exception):
    """Operação negada: o usuário não tem o role necessário."""


class FirestoreDatasource:
    """Acessa Firestore via coleções top-level `clippings/` e `subscriptions/`.

    Não usa mais subcoleções `users/{u}/clippings/{c}` — toda a leitura de
    "meus clippings" passa por `subscriptions`. Ver D3 do plano A2.
    """

    def __init__(self, db: Any):
        self._db = db

    # -- refs auxiliares ----------------------------------------------------
    def _clippings_ref(self):
        return self._db.collection("clippings")

    def _subscriptions_ref(self):
        return self._db.collection("subscriptions")

    # -- parsing helpers ----------------------------------------------------
    @staticmethod
    def _doc_to_clipping(doc_id: str, data: dict) -> ClippingData:
        return ClippingData.model_validate({"id": doc_id, **(data or {})})

    @staticmethod
    def _doc_to_subscription(doc_id: str, data: dict) -> SubscriptionData:
        return SubscriptionData.model_validate({"id": doc_id, **(data or {})})

    # -- read ---------------------------------------------------------------
    def get_my_clippings(self, user_id: str) -> list[MyClippingResult]:
        """Retorna todos os clippings (autorados + inscritos) do usuário.

        D3 (subscriptions-first):
          1) query 1: `subscriptions where userId == user_id AND active == True`
          2) batch get: `clippings/{id}` para cada sub.

        Subs com `active == False` são filtradas (decisão do brief A2).
        Subs órfãs (apontam para clipping inexistente) são silenciosamente
        ignoradas — defensivo contra race condition de delete.
        """
        subs_query = (
            self._subscriptions_ref()
            .where("userId", "==", user_id)
            .where("active", "==", True)
        )
        subs = [self._doc_to_subscription(d.id, d.to_dict()) for d in subs_query.stream()]
        if not subs:
            return []

        results: list[MyClippingResult] = []
        clippings_ref = self._clippings_ref()
        for sub in subs:
            doc = clippings_ref.document(sub.clipping_id).get()
            if not doc.exists:
                # sub órfã — ignora
                continue
            clipping = self._doc_to_clipping(doc.id, doc.to_dict())
            results.append(MyClippingResult(clipping=clipping, subscription=sub))
        return results

    def get_clipping(self, clipping_id: str) -> Optional[ClippingData]:
        """Busca clipping por ID (top-level)."""
        doc = self._clippings_ref().document(clipping_id).get()
        if not doc.exists:
            return None
        return self._doc_to_clipping(doc.id, doc.to_dict())

    def get_subscription(
        self, user_id: str, clipping_id: str
    ) -> Optional[SubscriptionData]:
        """Retorna a subscription do user para o clipping, ou None.

        Query composta `userId == X AND clippingId == Y` — assume índice
        composto criado em produção.
        """
        query = (
            self._subscriptions_ref()
            .where("userId", "==", user_id)
            .where("clippingId", "==", clipping_id)
        )
        for d in query.stream():
            return self._doc_to_subscription(d.id, d.to_dict())
        return None

    def get_subscriptions_for_user_and_clippings(
        self, user_id: str, clipping_ids: list[str]
    ) -> dict[str, SubscriptionData]:
        """Batch lookup das subscriptions de um user para vários clippings.

        Usado pelo dataloader em A3 para resolver `Clipping.mySubscription`
        sem N+1: 1 query Firestore `subscriptions where userId == user_id`,
        filtrando local para os `clipping_ids` solicitados.

        Decisão: query por `userId` (não composta) + filtro client-side é
        defensiva — evita depender do operador `in` com listas grandes (limite
        de 30 IDs no Firestore) e do índice composto `userId+clippingId`.
        Em troca, lê todas as subs do usuário, o que costuma ser O(dezenas).
        """
        if not clipping_ids:
            return {}
        clipping_id_set = set(clipping_ids)
        query = self._subscriptions_ref().where("userId", "==", user_id)
        result: dict[str, SubscriptionData] = {}
        for d in query.stream():
            data = d.to_dict() or {}
            cid = data.get("clippingId") or data.get("clipping_id")
            if cid in clipping_id_set:
                result[cid] = self._doc_to_subscription(d.id, data)
        return result

    # -- write: clippings ---------------------------------------------------
    def create_clipping(self, user_id: str, data: dict) -> ClippingData:
        """Cria clipping + author subscription em batch atômico.

        - `data` pode conter `delivery_channels`, `extra_emails`, `webhook_url`
          — esses migram para a *subscription* (não vão para o doc do clipping).
        - O doc do clipping carrega `authorUserId`, `active`, `createdAt`,
          `updatedAt` e os campos de conteúdo (name, description, recortes,
          prompt, etc.).

        A consistência é garantida por `db.batch()` — se o commit falhar,
        nenhum dos dois docs é persistido.
        """
        now = datetime.now(tz=timezone.utc)

        # Separar campos da subscription dos do clipping.
        # A4: `extra_emails` agora *também* vive no clipping (§8.0). O campo
        # sub-only é apenas `sub_extra_emails` (opt-in para preencher a sub
        # do autor com emails específicos).
        sub_only_keys = {"delivery_channels", "webhook_url", "sub_extra_emails"}
        clipping_payload: dict = {}
        for key, value in data.items():
            if key in sub_only_keys:
                continue
            clipping_payload[key] = value

        clipping_payload.setdefault("active", True)
        clipping_payload["author_user_id"] = user_id
        clipping_payload["created_at"] = now
        clipping_payload["updated_at"] = now

        # Cálculo do nextRunAt (A4). Se schedule inválido ou ausente, fica None.
        schedule_expr = clipping_payload.get("schedule") or ""
        if schedule_expr:
            from graphql_api.lib.cron import calculate_next_run

            clipping_payload["next_run_at"] = calculate_next_run(
                schedule_expr,
                now,
                start_date=clipping_payload.get("start_date"),
                end_date=clipping_payload.get("end_date"),
            )

        # Validar via Pydantic e dump em camelCase (canônico no Firestore)
        # `id` é dummy aqui — gerado pelo Firestore logo abaixo.
        # `delivery_channels` é dumpado vazio se restou no model — excluir
        # explicitamente, pois esse campo vive *só* na subscription em A2+.
        clipping_model = ClippingData.model_validate({"id": "_tmp_", **clipping_payload})
        clipping_doc_data = clipping_model.model_dump(
            by_alias=True, exclude={"id", "delivery_channels"}
        )

        # IDs pré-alocados
        clipping_ref = self._clippings_ref().document()
        clipping_id = clipping_ref.id
        sub_ref = self._subscriptions_ref().document()
        sub_id = sub_ref.id

        # Sub do autor
        delivery_channels = data.get("delivery_channels") or {}
        # `sub_extra_emails` (opcional) tem prioridade sobre o `extra_emails`
        # do clipping — sub_extra_emails é explicitamente sub-only.
        sub_extra_emails = data.get("sub_extra_emails")
        if sub_extra_emails is None:
            sub_extra_emails = []
        webhook_url = data.get("webhook_url") or ""
        sub_model = SubscriptionData(
            id=sub_id,
            clipping_id=clipping_id,
            user_id=user_id,
            role="author",
            delivery_channels=delivery_channels,
            extra_emails=list(sub_extra_emails),
            webhook_url=webhook_url,
            active=True,
            subscribed_at=now,
        )
        sub_doc_data = sub_model.model_dump(by_alias=True, exclude={"id"})

        batch = self._db.batch()
        batch.set(clipping_ref, clipping_doc_data)
        batch.set(sub_ref, sub_doc_data)
        batch.commit()

        # Retorna o ClippingData hidratado com o ID real
        return self._doc_to_clipping(clipping_id, clipping_doc_data)

    def update_clipping(
        self, user_id: str, clipping_id: str, data: dict
    ) -> ClippingData:
        """Atualiza conteúdo do clipping (não toca subscription).

        Autoriza apenas o `authorUserId`. Subscriber não pode editar conteúdo.
        """
        clipping_ref = self._clippings_ref().document(clipping_id)
        doc = clipping_ref.get()
        if not doc.exists:
            raise UnauthorizedError(f"clipping {clipping_id} not found")
        current = doc.to_dict() or {}
        if current.get("authorUserId") != user_id and current.get("author_user_id") != user_id:
            raise UnauthorizedError(
                f"user {user_id} is not the author of clipping {clipping_id}"
            )

        now = datetime.now(tz=timezone.utc)
        # Remover campos que pertencem à subscription (defensivo).
        sub_only_keys = {"delivery_channels", "webhook_url", "sub_extra_emails"}
        update_payload: dict = {k: v for k, v in data.items() if k not in sub_only_keys}

        # A4: recalcular nextRunAt sempre que `schedule`/`start_date`/`end_date`
        # entrarem no payload (ou mudarem).
        recalc_keys = {"schedule", "start_date", "end_date"}
        if recalc_keys & update_payload.keys():
            from graphql_api.lib.cron import calculate_next_run

            # Estado pós-update: payload tem prioridade sobre o doc atual.
            schedule_expr = update_payload.get("schedule") or current.get("schedule") or ""
            start_date = update_payload.get(
                "start_date", current.get("startDate") or current.get("start_date")
            )
            end_date = update_payload.get(
                "end_date", current.get("endDate") or current.get("end_date")
            )
            if schedule_expr:
                update_payload["next_run_at"] = calculate_next_run(
                    schedule_expr, now, start_date=start_date, end_date=end_date
                )

        # Converter snake_case dos updates para camelCase: para cada campo do
        # payload, consultar `model_fields` e usar o alias correspondente.
        update_dict: dict = {"updatedAt": now}
        for key, value in update_payload.items():
            field_info = ClippingData.model_fields.get(key)
            alias = field_info.alias if (field_info and field_info.alias) else key
            update_dict[alias] = value

        clipping_ref.update(update_dict)

        doc = clipping_ref.get()
        return self._doc_to_clipping(doc.id, doc.to_dict())

    def delete_clipping(self, user_id: str, clipping_id: str) -> bool:
        """Deleta o clipping + cascade em todas as subscriptions.

        Só o autor pode. Retorna False se o clipping não existir.
        """
        clipping_ref = self._clippings_ref().document(clipping_id)
        doc = clipping_ref.get()
        if not doc.exists:
            return False
        current = doc.to_dict() or {}
        author = current.get("authorUserId") or current.get("author_user_id")
        if author != user_id:
            raise UnauthorizedError(
                f"user {user_id} is not the author of clipping {clipping_id}"
            )

        # Encontrar todas subs do clipping
        subs_query = self._subscriptions_ref().where("clippingId", "==", clipping_id)
        sub_refs = [self._subscriptions_ref().document(d.id) for d in subs_query.stream()]

        batch = self._db.batch()
        batch.delete(clipping_ref)
        for ref in sub_refs:
            batch.delete(ref)
        batch.commit()
        return True

    # -- write: subscriptions ----------------------------------------------
    def subscribe_to_clipping(
        self,
        user_id: str,
        clipping_id: str,
        channels: dict,
        extra_emails: Optional[list[str]] = None,
        webhook_url: str = "",
    ) -> SubscriptionData:
        """Cria sub `role=subscriber` (ou atualiza se já existir).

        Idempotente: chamadas repetidas atualizam a sub existente com os
        novos channels/extra_emails/webhook_url, sem duplicar.
        """
        now = datetime.now(tz=timezone.utc)
        extra_emails = list(extra_emails or [])

        existing = self.get_subscription(user_id, clipping_id)
        if existing is not None:
            # Atualiza in-place — preserva role (não rebaixa author para subscriber)
            sub_ref = self._subscriptions_ref().document(existing.id)
            sub_ref.update(
                {
                    "deliveryChannels": dict(channels),
                    "extraEmails": extra_emails,
                    "webhookUrl": webhook_url,
                    "active": True,
                    "subscribedAt": existing.subscribed_at or now,
                }
            )
            doc = sub_ref.get()
            return self._doc_to_subscription(doc.id, doc.to_dict())

        sub_ref = self._subscriptions_ref().document()
        sub = SubscriptionData(
            id=sub_ref.id,
            clipping_id=clipping_id,
            user_id=user_id,
            role="subscriber",
            delivery_channels=dict(channels),
            extra_emails=extra_emails,
            webhook_url=webhook_url,
            active=True,
            subscribed_at=now,
        )
        sub_ref.set(sub.model_dump(by_alias=True, exclude={"id"}))
        return sub

    def unsubscribe_from_clipping(self, user_id: str, clipping_id: str) -> bool:
        """Remove a sub do user para o clipping.

        - Sub não existente: retorna False.
        - Sub com `role=author`: levanta `UnauthorizedError` (autor não pode
          self-unsubscribe; tem que usar `delete_clipping`).
        - Sub com `role=subscriber`: deleta e retorna True.
        """
        sub = self.get_subscription(user_id, clipping_id)
        if sub is None:
            return False
        if sub.role == "author":
            raise UnauthorizedError(
                "author cannot unsubscribe from own clipping; use delete_clipping"
            )
        self._subscriptions_ref().document(sub.id).delete()
        return True

    # -- refs: releases -----------------------------------------------------
    def _releases_ref(self):
        return self._db.collection("releases")

    @staticmethod
    def _doc_to_release(doc_id: str, data: dict) -> ReleaseData:
        return ReleaseData.model_validate({"id": doc_id, **(data or {})})

    def get_releases(
        self,
        clipping_id: str,
        limit: int = 20,
        before: Optional[datetime] = None,
    ) -> list[ReleaseData]:
        """Lista releases de um clipping, ordenados por `createdAt` desc.

        - `limit`: maximo de docs retornados.
        - `before`: cursor opcional; retorna apenas releases com
          `createdAt < before` (paginacao forward).

        Nao filtra por autorizacao — quem chama deve validar.
        """
        query = (
            self._releases_ref()
            .where("clippingId", "==", clipping_id)
            .order_by("createdAt", direction="DESCENDING")
        )
        if before is not None:
            query = query.where("createdAt", "<", before)
        query = query.limit(limit)
        return [self._doc_to_release(d.id, d.to_dict()) for d in query.stream()]

    def get_releases_batch(
        self,
        clipping_ids: list[str],
        limit: int = 20,
        before: Optional[datetime] = None,
    ) -> dict[str, list[ReleaseData]]:
        """Batch loader para releases de varios clippings.

        Implementacao defensiva: faz N queries (uma por clipping_id) em vez
        de uma unica com `where in [...]`, pois o operador `in` do Firestore
        limita a 30 elementos e nao se combina bem com `order_by`+`limit`
        per-group. Cada query retorna no maximo `limit` docs, ordenados
        por `createdAt` desc.

        Retorna `{clipping_id: [ReleaseData...]}` — chave sempre presente,
        mesmo quando lista vazia.
        """
        if not clipping_ids:
            return {}
        result: dict[str, list[ReleaseData]] = {}
        for cid in clipping_ids:
            result[cid] = self.get_releases(cid, limit=limit, before=before)
        return result

    def update_my_subscription(
        self,
        user_id: str,
        clipping_id: str,
        channels: dict,
        extra_emails: Optional[list[str]] = None,
        webhook_url: str = "",
    ) -> Optional[SubscriptionData]:
        """Atualiza canais/emails/webhook da sub do user.

        Funciona tanto para `role=author` quanto `role=subscriber` — o autor
        também tem direito de mudar seus canais. Retorna None se a sub não
        existir.
        """
        sub = self.get_subscription(user_id, clipping_id)
        if sub is None:
            return None
        extra_emails = list(extra_emails or [])
        sub_ref = self._subscriptions_ref().document(sub.id)
        sub_ref.update(
            {
                "deliveryChannels": dict(channels),
                "extraEmails": extra_emails,
                "webhookUrl": webhook_url,
            }
        )
        doc = sub_ref.get()
        return self._doc_to_subscription(doc.id, doc.to_dict())
