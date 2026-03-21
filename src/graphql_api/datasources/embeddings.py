import os

import httpx

EMBEDDINGS_API_URL = os.getenv("EMBEDDINGS_API_URL", "http://localhost:8080/embeddings")


class EmbeddingsDatasource:
    def __init__(self, api_url: str | None = None):
        self.api_url = api_url or EMBEDDINGS_API_URL

    async def generate_embedding(self, text: str) -> list[float] | None:
        """Generate an embedding vector for the given text.

        Returns None if the API is unavailable (graceful fallback).
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self.api_url,
                    json={"texts": [text]},
                )
                response.raise_for_status()
                data = response.json()
                return data["embeddings"][0]
        except (httpx.HTTPError, KeyError, IndexError):
            return None
