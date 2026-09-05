import asyncio
import base64
import json
import zlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from web import hooks


class FakeMcp:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorate(handler):
            self.routes[path] = handler
            return handler
        return decorate


def bucket(bucket_id, content, **metadata):
    return {"id": bucket_id, "content": content, "metadata": metadata}


def decode_provenance(response):
    value = response.headers[hooks.BREATH_PROVENANCE_HEADER]
    assert value.startswith("z1.")
    encoded = value[3:]
    packed = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    return json.loads(zlib.decompress(packed))


def render(buckets, *, authorized=True, token_counter=None):
    mcp = FakeMcp()
    hooks.register(mcp)
    manager = SimpleNamespace(list_all=AsyncMock(return_value=buckets))
    dehydrator = SimpleNamespace(dehydrate=AsyncMock(side_effect=lambda content, _meta: content))
    decay = SimpleNamespace(calculate_score=lambda meta: meta.get("score", 0))
    with patch.object(hooks, "_is_hook_request_authorized", return_value=authorized), \
         patch.object(hooks.sh, "bucket_mgr", manager), \
         patch.object(hooks.sh, "dehydrator", dehydrator), \
         patch.object(hooks.sh, "decay_engine", decay), \
         patch.object(hooks.sh, "fire_webhook", AsyncMock()), \
         patch.object(hooks, "count_tokens_approx", token_counter or (lambda _text: 1)):
        return asyncio.run(mcp.routes["/breath-hook"](object()))


def test_pinned_body_is_unchanged_and_provenance_names_actual_bucket():
    response = render([bucket("pin-1", "CORE", name="核心名字", pinned=True)])

    assert response.body.decode() == "[Ombre Brain - 记忆浮现]\n📌 [核心准则] CORE"
    assert decode_provenance(response) == {
        "v": 1,
        "s": [["p", "pin-1", "核心名字", 1]],
        "t": False,
    }


def test_unresolved_over_token_budget_is_not_in_provenance():
    response = render(
        [
            bucket("pin-1", "PIN", name="core", pinned=True),
            bucket("unresolved-1", "TOO", name="candidate", type="memory", score=9),
        ],
        token_counter=lambda text: 9999 if text == "PIN" else 2,
    )

    assert response.body.decode() == "[Ombre Brain - 记忆浮现]\n📌 [核心准则] PIN"
    assert [row[1] for row in decode_provenance(response)["s"]] == ["pin-1"]


def test_latest_user_and_ai_letters_have_precise_sections():
    rows = [
        bucket("pin-1", "BASE", name="core", pinned=True),
        bucket("user-old", "OLD", type="letter", author="user", letter_date="2026-09-01"),
        bucket("user-new", "USER LETTER", name="用户来信", type="letter", author="user", letter_date="2026-09-02", title="User title"),
        bucket("ai-new", "AI LETTER", name="AI 回信", type="letter", author="AI", letter_date="2026-09-03", title="AI title"),
    ]
    with patch.object(hooks, "get_ai_name", return_value="AI"):
        response = render(rows)

    assert response.body.decode() == (
        "[Ombre Brain - 记忆浮现]\n📌 [核心准则] BASE"
        "\n\n=== 最近的信 ===\n"
        "💌 [user→你] 2026-09-02 · User title\nUSER LETTER\n\n"
        "💌 [你→user] 2026-09-03 · AI title\nAI LETTER"
    )
    assert decode_provenance(response)["s"] == [
        ["p", "pin-1", "core", 1],
        ["lu", "user-new", "用户来信", 1],
        ["la", "ai-new", "AI 回信", 1],
    ]


def test_i_bucket_has_i_self_section():
    response = render([
        bucket("pin-1", "BASE", name="core", pinned=True),
        bucket("i-1", "KNOW SELF", name="自我认知", type="i", created="2026-09-03T10:00:00Z", tags=["__i__", "aspect:values"]),
    ])

    assert response.body.decode() == (
        "[Ombre Brain - 记忆浮现]\n📌 [核心准则] BASE"
        "\n\n=== I ===\n🪞2026-09-03 [values]\nKNOW SELF"
    )
    assert decode_provenance(response)["s"][-1] == ["i", "i-1", "自我认知", 1]


def test_provenance_encoding_failure_keeps_breath_body():
    expected = "[Ombre Brain - 记忆浮现]\n📌 [核心准则] CORE"
    with patch.object(hooks, "_encode_breath_provenance", side_effect=RuntimeError("instrumentation")):
        response = render([bucket("pin-1", "CORE", pinned=True)])

    assert response.body.decode() == expected
    assert hooks.BREATH_PROVENANCE_HEADER not in response.headers


def test_unauthorized_contract_is_unchanged():
    response = render([], authorized=False)

    assert response.status_code == 401
    assert response.body == b""
    assert hooks.BREATH_PROVENANCE_HEADER not in response.headers


def test_provenance_header_is_ascii_and_bounded():
    provenance = {"sources": [], "truncated": False}
    for index in range(hooks._BREATH_PROVENANCE_MAX_SOURCES):
        hooks._record_breath_source(
            provenance,
            bucket(f"bucket-{index:02d}", "", name=("名字" * 40), pinned=True),
            "pinned",
        )

    value = hooks._encode_breath_provenance(provenance)
    value.encode("ascii")
    assert len(value) <= hooks._BREATH_PROVENANCE_MAX_HEADER_CHARS
    assert len(decode_provenance(SimpleNamespace(headers={hooks.BREATH_PROVENANCE_HEADER: value}))["s"]) == 48
