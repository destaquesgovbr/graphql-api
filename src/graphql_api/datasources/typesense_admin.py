import logging
from typing import Any, Optional

import typesense
import typesense.exceptions

from graphql_api.datasources.typesense import _build_typesense_client, _parse_typesense_conn

logger = logging.getLogger(__name__)

COLLECTION_NAME = "news"


class TypesenseAdminDatasource:
    def __init__(self, client: typesense.Client):
        self.client = client

    @classmethod
    def from_env(cls, env_name: str = "TYPESENSE_WRITE_CONN") -> Optional["TypesenseAdminDatasource"]:
        """Cria um datasource admin a partir da env var (JSON). Retorna None se ausente/inválida."""
        conn = _parse_typesense_conn(env_name)
        if conn is None:
            return None
        try:
            client = _build_typesense_client(conn)
        except Exception as exc:  # pragma: no cover - defensivo
            logger.warning("falha ao construir typesense admin client a partir de %s: %s", env_name, exc)
            return None
        return cls(client=client)

    def update_field(self, unique_id: str, field: str, value: Any) -> bool:
        try:
            self.client.collections[COLLECTION_NAME].documents[unique_id].update(
                {field: value}
            )
            return True
        except typesense.exceptions.ObjectNotFound:
            return False
