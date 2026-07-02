import json

import pytest

import web.system as system


class FakeMCP:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(fn):
            for method in methods:
                self.routes[(method, path)] = fn
            return fn

        return decorator


class FakeBucketManager:
    async def get_stats(self):
        return {
            "permanent_count": 1,
            "dynamic_count": 2,
            "archive_count": 3,
        }


class FakeDecayEngine:
    is_running = True


class StandbyEmbeddingEngine:
    enabled = True
    _backend = None
    model = ""
    db_path = ""


class FakeGithubSync:
    def status(self):
        return {
            "enabled": True,
            "repo": "owner/repo",
            "branch": "main",
            "path_prefix": "ombre",
            "last_sync": None,
            "last_status": "idle",
            "last_error": "",
            "last_count": 0,
            "is_validated": False,
        }


@pytest.mark.asyncio
async def test_system_diagnostics_reports_missing_ai_configuration(monkeypatch, tmp_path):
    buckets_dir = tmp_path / "buckets"
    buckets_dir.mkdir()

    monkeypatch.setattr(system.sh, "config", {
        "buckets_dir": str(buckets_dir),
        "dehydration": {
            "api_key": "",
            "base_url": "https://api.example.test/v1",
            "model": "deepseek-chat",
            "timeout_seconds": 120,
        },
        "embedding": {
            "enabled": True,
            "api_key": "",
            "model": "bge-m3",
            "timeout_seconds": 150,
        },
        "mcp_require_auth": False,
        "github_sync": {"repo": "owner/repo", "branch": "main", "path_prefix": "ombre"},
    })
    monkeypatch.setattr(system.sh, "bucket_mgr", FakeBucketManager())
    monkeypatch.setattr(system.sh, "decay_engine", FakeDecayEngine())
    monkeypatch.setattr(system.sh, "embedding_engine", StandbyEmbeddingEngine())
    monkeypatch.setattr(system.sh, "github_sync_instance", FakeGithubSync())
    monkeypatch.setattr(system.sh, "version", "2.4.8")
    monkeypatch.setattr(system.sh, "repo_root", str(tmp_path))
    monkeypatch.setattr(system.sh, "_is_setup_needed", lambda: False)

    payload = await system.build_system_diagnostics()
    by_id = {check["id"]: check for check in payload["checks"]}

    assert payload["ok"] is False
    assert payload["summary"]["error"] >= 2
    assert by_id["storage"]["status"] == "ok"
    assert by_id["llm"]["status"] == "error"
    assert "API Key" in by_id["llm"]["message"]
    assert by_id["embedding"]["status"] == "error"
    assert "待机" in by_id["embedding"]["message"]
    assert by_id["github"]["status"] == "warning"
    assert by_id["auth"]["details"]["mcp_oauth_required"] is False


@pytest.mark.asyncio
async def test_system_diagnostics_route_requires_auth_and_returns_payload(monkeypatch):
    expected = {
        "ok": True,
        "summary": {"ok": 1, "warning": 0, "error": 0},
        "checks": [{"id": "runtime", "label": "运行时", "status": "ok", "message": "ready", "details": {}}],
    }

    async def fake_build():
        return expected

    monkeypatch.setattr(system.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(system, "build_system_diagnostics", fake_build, raising=False)

    mcp = FakeMCP()
    system.register(mcp)

    response = await mcp.routes[("GET", "/api/system/diagnostics")](object())
    payload = json.loads(response.body)

    assert payload == expected

