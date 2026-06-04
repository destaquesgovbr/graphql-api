"""Helper para obter tokens OIDC via metadata server do GCE/Cloud Run.

A subscription `generateRecortes` (Fase A6) faz passthrough do SSE para o
`clipping/` worker, que esta protegido por IAM. Em producao precisamos
obter um token OIDC com `audience` = URL do worker e enviar como
`Authorization: Bearer <token>`. Em dev (`localhost`/`127.0.0.1`) nao ha
metadata server, portanto retornamos `None` e o caller pula o header.

Documentacao:
- https://cloud.google.com/run/docs/securing/service-identity
- https://cloud.google.com/compute/docs/instances/verifying-instance-identity
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_METADATA_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/identity"
)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _is_local_audience(audience: str) -> bool:
    try:
        host = urlparse(audience).hostname or ""
    except ValueError:
        return False
    return host in _LOCAL_HOSTS


async def get_oidc_token(audience: str, *, timeout: float = 5.0) -> str | None:
    """Retorna um ID token OIDC para chamar um servico Cloud Run.

    Args:
        audience: URL do servico alvo (ex: `https://worker-xyz.a.run.app`).
        timeout: timeout em segundos para a chamada ao metadata server.

    Returns:
        Token plain text (str) quando o metadata server responde 200.
        `None` quando:
            - `audience` aponta para localhost/127.0.0.1 (dev local).
            - metadata server retorna erro ou conexao falha.
    """
    if _is_local_audience(audience):
        return None

    params = {"audience": audience, "format": "full"}
    headers = {"Metadata-Flavor": "Google"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(_METADATA_URL, params=params, headers=headers)
            if resp.status_code == 200:
                return resp.text.strip()
            logger.warning(
                "metadata server returned %s for audience %s; tentando ADC",
                resp.status_code,
                audience,
            )
    except httpx.HTTPError as e:
        logger.warning(
            "metadata server indisponivel para %s (%s); tentando ADC", audience, e
        )

    # Fallback dev local: sem metadata server (fora do Cloud Run), tenta gerar
    # um ID token via Application Default Credentials. Requer que a identidade
    # do ADC tenha `roles/run.invoker` no servico alvo — senao o worker rejeita
    # com 401 mesmo com token valido (limitacao documentada no plano R1, Fase 6).
    return _fetch_id_token_via_adc(audience)


def _fetch_id_token_via_adc(audience: str) -> str | None:
    """Gera um ID token OIDC para `audience` via ADC (google-auth).

    Sincrono (a lib google-auth nao e async); chamado so no fallback dev, onde
    a latencia extra e irrelevante. Retorna None em qualquer falha.
    """
    try:
        import google.auth.transport.requests
        import google.oauth2.id_token

        request = google.auth.transport.requests.Request()
        token = google.oauth2.id_token.fetch_id_token(request, audience)
        return token or None
    except Exception as e:  # noqa: BLE001 — fallback best-effort
        logger.warning("ADC id_token fallback falhou para %s: %s", audience, e)
        return None
