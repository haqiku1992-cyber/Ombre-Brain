import json
import time

import pytest

import web.oauth as oauth_mod


class FakeMCP:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(fn):
            for method in methods:
                self.routes[(method, path)] = fn
            return fn

        return decorator


class FakeUrl:
    scheme = "https"
    netloc = "ombre.example"


class JsonRequest:
    def __init__(self, body=None, *, headers=None, path_params=None):
        self._body = body or {}
        self.headers = headers or {"content-type": "application/json", "host": "ombre.example"}
        self.url = FakeUrl()
        self.path_params = path_params or {}

    async def json(self):
        return self._body

    async def form(self):
        return self._body


def _payload(response):
    return json.loads(response.body)


@pytest.fixture
def oauth_routes(monkeypatch, tmp_path):
    oauth_mod._oauth_clients.clear()
    oauth_mod._oauth_codes.clear()
    oauth_mod._mcp_tokens.clear()
    if hasattr(oauth_mod, "_mcp_refresh_tokens"):
        oauth_mod._mcp_refresh_tokens.clear()
    monkeypatch.setattr(oauth_mod.sh, "config", {"buckets_dir": str(tmp_path / "buckets")})

    mcp = FakeMCP()
    oauth_mod.register(mcp)
    return mcp.routes


@pytest.mark.asyncio
async def test_oauth_metadata_and_registration_advertise_refresh_token(oauth_routes):
    metadata_response = await oauth_routes[("GET", "/.well-known/oauth-authorization-server")](
        JsonRequest()
    )
    metadata = _payload(metadata_response)

    register_response = await oauth_routes[("POST", "/oauth/register")](
        JsonRequest({"redirect_uris": ["https://client.example/callback"]})
    )
    registration = _payload(register_response)

    assert "refresh_token" in metadata["grant_types_supported"]
    assert "refresh_token" in registration["grant_types"]


@pytest.mark.asyncio
async def test_refresh_token_grant_renews_access_without_browser_authorization(oauth_routes):
    oauth_mod._oauth_clients["client-1"] = {
        "redirect_uris": ["https://client.example/callback"],
        "client_name": "Headless Client",
    }
    oauth_mod._oauth_codes["code-1"] = {
        "client_id": "client-1",
        "redirect_uri": "https://client.example/callback",
        "code_challenge": "",
        "expires": time.time() + 60,
    }

    token_response = await oauth_routes[("POST", "/oauth/token")](
        JsonRequest({
            "grant_type": "authorization_code",
            "code": "code-1",
            "client_id": "client-1",
        })
    )
    initial = _payload(token_response)
    first_access_token = initial["access_token"]
    refresh_token = initial["refresh_token"]

    oauth_mod._mcp_tokens[first_access_token] = time.time() - 1
    assert oauth_mod._is_valid_mcp_token(first_access_token) is False

    refresh_response = await oauth_routes[("POST", "/oauth/token")](
        JsonRequest({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": "client-1",
        })
    )
    refreshed = _payload(refresh_response)

    assert refreshed["access_token"] != first_access_token
    assert refreshed["token_type"] == "Bearer"
    assert refreshed["scope"] == "mcp"
    assert oauth_mod._is_valid_mcp_token(refreshed["access_token"]) is True


@pytest.mark.asyncio
async def test_refresh_token_survives_process_restart(oauth_routes):
    oauth_mod._oauth_clients["client-1"] = {
        "redirect_uris": ["https://client.example/callback"],
        "client_name": "Headless Client",
    }
    oauth_mod._oauth_codes["code-1"] = {
        "client_id": "client-1",
        "redirect_uri": "https://client.example/callback",
        "code_challenge": "",
        "expires": time.time() + 60,
    }

    token_response = await oauth_routes[("POST", "/oauth/token")](
        JsonRequest({
            "grant_type": "authorization_code",
            "code": "code-1",
            "client_id": "client-1",
        })
    )
    refresh_token = _payload(token_response)["refresh_token"]

    oauth_mod._mcp_tokens.clear()
    oauth_mod._mcp_refresh_tokens.clear()
    oauth_mod._load_mcp_tokens()

    refresh_response = await oauth_routes[("POST", "/oauth/token")](
        JsonRequest({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": "client-1",
        })
    )
    refreshed = _payload(refresh_response)

    assert refresh_response.status_code == 200
    assert oauth_mod._is_valid_mcp_token(refreshed["access_token"]) is True


@pytest.mark.asyncio
async def test_refresh_token_grant_rejects_unknown_refresh_token(oauth_routes):
    response = await oauth_routes[("POST", "/oauth/token")](
        JsonRequest({
            "grant_type": "refresh_token",
            "refresh_token": "not-issued",
            "client_id": "client-1",
        })
    )
    payload = _payload(response)

    assert response.status_code == 400
    assert payload["error"] == "invalid_grant"
