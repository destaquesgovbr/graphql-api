from typing import Any

import typesense
import typesense.exceptions

COLLECTION_NAME = "news"


class TypesenseAdminDatasource:
    def __init__(self, client: typesense.Client):
        self.client = client

    def update_field(self, unique_id: str, field: str, value: Any) -> bool:
        try:
            self.client.collections[COLLECTION_NAME].documents[unique_id].update(
                {field: value}
            )
            return True
        except typesense.exceptions.ObjectNotFound:
            return False
