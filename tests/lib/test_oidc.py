"""Testes do helper `get_oidc_token` (Fase A6).

`get_oidc_token(audience)` retorna:
- `None` para audiences locais (`http://localhost*`, `http://127.0.0.1*`),
  evitando chamada ao metadata server em dev.
- O token plain text (str) retornado pelo metadata server quando
  audience aponta para um host remoto (ex.: Cloud Run).

Mock do metadata server feito via `respx` (sem chamada de rede real).
"""

import httpx
import pytest
import respx

from graphql_api.lib.oidc import _METADATA_URL, get_oidc_token


@pytest.mark.asyncio
@respx.mock
async def test_get_oidc_token_returns_token_for_remote_audience():
    audience = "https://clipping-worker-xyz.a.run.app"
    route = respx.get(_METADATA_URL).mock(
        return_value=httpx.Response(200, text="dummy-oidc-token-abc"),
    )

    token = await get_oidc_token(audience)

    assert token == "dummy-oidc-token-abc"
    assert route.called
    request = route.calls.last.request
    assert request.headers["Metadata-Flavor"] == "Google"
    # audience vai como query param (URL-encoded). Conferir via params decoded.
    assert request.url.params.get("audience") == audience
    assert request.url.params.get("format") == "full"


@pytest.mark.asyncio
@respx.mock
async def test_get_oidc_token_returns_none_for_localhost():
    # respx em modo strict — qualquer chamada de rede falhara. Se a funcao
    # nao curto-circuitar para localhost, este teste explode.
    token = await get_oidc_token("http://localhost:8080/agent/generate-recortes")
    assert token is None


@pytest.mark.asyncio
@respx.mock
async def test_get_oidc_token_returns_none_for_127_0_0_1():
    token = await get_oidc_token("http://127.0.0.1:8080/path")
    assert token is None


@pytest.mark.asyncio
@respx.mock
async def test_get_oidc_token_returns_none_on_metadata_failure(monkeypatch):
    # Metadata falha (500) E ADC indisponivel → None. Mockamos o fallback ADC
    # para None para isolar o comportamento (sem depender de ADC real da maquina).
    monkeypatch.setattr(
        "graphql_api.lib.oidc._fetch_id_token_via_adc", lambda audience: None
    )
    audience = "https://remote.example.com"
    respx.get(_METADATA_URL).mock(return_value=httpx.Response(500))

    token = await get_oidc_token(audience)
    assert token is None


@pytest.mark.asyncio
@respx.mock
async def test_get_oidc_token_returns_none_on_connect_error(monkeypatch):
    monkeypatch.setattr(
        "graphql_api.lib.oidc._fetch_id_token_via_adc", lambda audience: None
    )
    audience = "https://remote.example.com"
    respx.get(_METADATA_URL).mock(side_effect=httpx.ConnectError("no metadata server"))

    token = await get_oidc_token(audience)
    assert token is None


@pytest.mark.asyncio
@respx.mock
async def test_get_oidc_token_falls_back_to_adc_when_metadata_unavailable(
    monkeypatch,
):
    # Fora do Cloud Run (sem metadata server), gera token via ADC.
    monkeypatch.setattr(
        "graphql_api.lib.oidc._fetch_id_token_via_adc",
        lambda audience: "adc-token-xyz",
    )
    respx.get(_METADATA_URL).mock(side_effect=httpx.ConnectError("no metadata"))

    token = await get_oidc_token("https://clipping-worker-xyz.a.run.app")
    assert token == "adc-token-xyz"
