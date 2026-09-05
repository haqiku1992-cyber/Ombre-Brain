"""
========================================
web/hooks.py — breath / dream 浮现挂载点（HTTP hook）
========================================

- /breath-hook：对话开头由外部 hook 拉取，返回应浮现的记忆（pinned + 未解决采样）
- /dream-hook：dream 专用，返回最近窗口内可做梦的候选

给外部 SessionStart hook / 自动化用；默认需要 Dashboard 登录态或 hook token。
通过 sh.fire_webhook 推送事件。

对外暴露：register(mcp)。
========================================
"""

import base64
import hmac
import json
import os
import random
import zlib

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import _shared as sh

logger = sh.logger

BREATH_PROVENANCE_HEADER = "X-Ombre-Breath-Provenance"
_BREATH_PROVENANCE_MAX_HEADER_CHARS = 3900
_BREATH_PROVENANCE_MAX_SOURCES = 48
_BREATH_PROVENANCE_SECTION_CODES = {
    "pinned": "p",
    "unresolved": "u",
    "letter:user": "lu",
    "letter:ai": "la",
    "i/self": "i",
}

try:
    from utils import strip_wikilinks, count_tokens_approx, get_ai_name  # type: ignore
except ImportError:  # pragma: no cover
    from ..utils import strip_wikilinks, count_tokens_approx, get_ai_name  # type: ignore

try:
    from tools import hold as _t_hold  # type: ignore
except ImportError:  # pragma: no cover
    from ..tools import hold as _t_hold  # type: ignore


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _hook_setting(name: str, default=None):
    hooks_cfg = (getattr(sh, "config", {}) or {}).get("hooks") or {}
    return hooks_cfg.get(name, default)


def _header_value(request, name: str) -> str:
    headers = getattr(request, "headers", {}) or {}
    try:
        return str(headers.get(name, "") or "")
    except Exception:
        wanted = name.lower()
        for k, v in dict(headers).items():
            if str(k).lower() == wanted:
                return str(v or "")
    return ""


def _is_hook_request_authorized(request) -> bool:
    """Protect hook endpoints that can expose memory text.

    Public hooks can still be enabled deliberately with OMBRE_HOOK_ALLOW_PUBLIC=1
    or config hooks.allow_public=true. Otherwise a dashboard session or a hook
    token is required.
    """
    allow_public = _truthy(os.environ.get("OMBRE_HOOK_ALLOW_PUBLIC")) or _truthy(
        _hook_setting("allow_public")
    )
    if allow_public:
        return True

    token = (os.environ.get("OMBRE_HOOK_TOKEN") or str(_hook_setting("token", "") or "")).strip()
    if token:
        auth = _header_value(request, "authorization")
        supplied = [
            str((getattr(request, "query_params", {}) or {}).get("token", "") or ""),
            _header_value(request, "x-ombre-hook-token"),
            auth[7:] if auth.startswith("Bearer ") else "",
        ]
        if any(v and hmac.compare_digest(v, token) for v in supplied):
            return True

    try:
        return bool(sh._is_authenticated(request))
    except Exception:
        return False


def _utf8_prefix(value, max_bytes: int) -> str:
    return str(value or "").encode("utf-8")[:max_bytes].decode("utf-8", "ignore")


def _breath_source_row(bucket: dict, section: str) -> list:
    meta = bucket.get("metadata") if isinstance(bucket.get("metadata"), dict) else {}
    return [
        _BREATH_PROVENANCE_SECTION_CODES[section],
        _utf8_prefix(bucket.get("id") or meta.get("id"), 96),
        _utf8_prefix(meta.get("name") or meta.get("title"), 80),
        1,
    ]


def _record_breath_source(provenance: dict, bucket: dict, section: str) -> None:
    """Best-effort observability; never affect breath rendering."""

    try:
        sources = provenance["sources"]
        if len(sources) >= _BREATH_PROVENANCE_MAX_SOURCES:
            provenance["truncated"] = True
            return
        row = _breath_source_row(bucket, section)
        if row[1]:
            sources.append(row)
    except Exception as exc:
        logger.warning(f"breath provenance source failed: {exc}")


def _encode_breath_provenance(provenance: dict) -> str:
    """Return one bounded ASCII header containing only safe source metadata."""

    sources = list(provenance.get("sources") or [])[:_BREATH_PROVENANCE_MAX_SOURCES]
    if not sources:
        return ""

    def encode(rows: list, truncated: bool) -> str:
        raw = json.dumps(
            {"v": 1, "s": rows, "t": bool(truncated)},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        packed = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")
        return "z1." + packed

    truncated = bool(provenance.get("truncated"))
    value = encode(sources, truncated)
    if len(value) <= _BREATH_PROVENANCE_MAX_HEADER_CHARS:
        return value

    # Display names are optional; retain every source id/kind before dropping rows.
    sources = [[row[0], row[1], "", row[3]] for row in sources]
    value = encode(sources, truncated)
    while sources and len(value) > _BREATH_PROVENANCE_MAX_HEADER_CHARS:
        sources.pop()
        value = encode(sources, True)
    return value if sources else ""


def register(mcp) -> None:

    @mcp.custom_route("/memory-hook", methods=["POST"])
    async def memory_hook(request):
        """Token-protected write bridge for private frontends such as Chiaros."""
        if not _is_hook_request_authorized(request):
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
        content = str(body.get("content") or "").strip()[:12000]
        if not content:
            return JSONResponse({"ok": False, "error": "content required"}, status_code=400)
        try:
            result = await _t_hold.dispatch(
                content=content,
                tags=str(body.get("tags") or "chiaros,chat")[:300],
                importance=int(body.get("importance") or 5),
                source_bucket=str(body.get("source_bucket") or "chiaros")[:120],
            )
            return JSONResponse({"ok": True, "result": result})
        except Exception as exc:
            logger.exception("memory_hook write failed")
            return JSONResponse({"ok": False, "error": type(exc).__name__}, status_code=500)

    @mcp.custom_route("/breath-hook", methods=["GET"])
    async def breath_hook(request):
        from starlette.responses import PlainTextResponse
        if not _is_hook_request_authorized(request):
            return PlainTextResponse("", status_code=401)
        try:
            all_buckets = await sh.bucket_mgr.list_all(include_archive=False)
            provenance = {"sources": [], "truncated": False}
            # pinned
            pinned = [b for b in all_buckets if b["metadata"].get("pinned") or b["metadata"].get("protected")]
            # top 2 unresolved by score
            unresolved = [b for b in all_buckets
                          if not b["metadata"].get("resolved", False)
                          and b["metadata"].get("type") not in ("permanent", "feel", "plan", "letter", "self", "i")
                          and not b["metadata"].get("pinned")
                          and not b["metadata"].get("protected")
                          and not b["metadata"].get("dont_surface", False)]
            scored = sorted(unresolved, key=lambda b: sh.decay_engine.calculate_score(b["metadata"]), reverse=True)

            parts = []
            token_budget = 10000
            for b in pinned:
                summary = await sh.dehydrator.dehydrate(strip_wikilinks(b["content"]), {k: v for k, v in b["metadata"].items() if k != "tags"})
                parts.append(f"📌 [核心准则] {summary}")
                _record_breath_source(provenance, b, "pinned")
                token_budget -= count_tokens_approx(summary)

            # Diversity: top-1 fixed + shuffle rest from top-20
            candidates = list(scored)
            if len(candidates) > 1:
                top1 = [candidates[0]]
                pool = candidates[1:min(20, len(candidates))]
                random.shuffle(pool)
                candidates = top1 + pool + candidates[min(20, len(candidates)):]
            # Hard cap: max 20 surfacing buckets in hook
            candidates = candidates[:20]

            for b in candidates:
                if token_budget <= 0:
                    break
                summary = await sh.dehydrator.dehydrate(strip_wikilinks(b["content"]), {k: v for k, v in b["metadata"].items() if k != "tags"})
                summary_tokens = count_tokens_approx(summary)
                if summary_tokens > token_budget:
                    break
                parts.append(summary)
                _record_breath_source(provenance, b, "unresolved")
                token_budget -= summary_tokens

            if not parts:
                await sh.fire_webhook("breath_hook", {"surfaced": 0})
                return PlainTextResponse("")
            body_text = "[Ombre Brain - 记忆浮现]\n" + "\n---\n".join(parts)

            # --- Append latest letter from each side (iter 1.4) ---
            # --- 附带双方各最新一封 letter ---
            try:
                letters = [b for b in all_buckets if b["metadata"].get("type") == "letter"]
                if letters:
                    def _latest(*authors: str) -> dict | None:
                        wanted = set(authors)
                        pool = [letter for letter in letters if letter["metadata"].get("author") in wanted]
                        if not pool:
                            return None
                        pool.sort(key=lambda b: b["metadata"].get("letter_date") or b["metadata"].get("created", ""), reverse=True)
                        return pool[0]
                    latest_user = _latest("user")
                    # AI 侧：新署名 ai_name + 历史遗留的 "claude"
                    latest_ai = _latest(get_ai_name(), "claude")
                    letter_lines = []
                    letter_sources = []
                    for tag, section, letter in (
                        ("user→你", "letter:user", latest_user),
                        ("你→user", "letter:ai", latest_ai),
                    ):
                        if letter is None:
                            continue
                        d = letter["metadata"].get("letter_date") or letter["metadata"].get("created", "")[:10]
                        title = letter["metadata"].get("title") or letter["metadata"].get("name", "")
                        excerpt = strip_wikilinks(letter["content"])[:400]
                        letter_lines.append(
                            f"💌 [{tag}] {d}{(' · ' + title) if title else ''}\n{excerpt}"
                        )
                        letter_sources.append((letter, section))
                    if letter_lines:
                        body_text += "\n\n=== 最近的信 ===\n" + "\n\n".join(letter_lines)
                        for letter, section in letter_sources:
                            _record_breath_source(provenance, letter, section)
            except Exception as e:
                logger.warning(f"breath_hook letter section failed: {e}")

            # --- Append recent self-knowledge (I tool) ---
            try:
                self_buckets = [
                    b for b in all_buckets
                    if b["metadata"].get("type") == "i"
                    or "__i__" in (b["metadata"].get("tags") or [])
                ]
                if self_buckets:
                    self_buckets.sort(
                        key=lambda b: b["metadata"].get("created", ""), reverse=True
                    )
                    self_lines = []
                    self_sources = []
                    for b in self_buckets[:3]:
                        meta = b["metadata"]
                        ts = (meta.get("created") or "")[:10]
                        tags_list = meta.get("tags") or []
                        aspect_tag = next(
                            (t.replace("aspect:", "") for t in tags_list if t.startswith("aspect:")), ""
                        )
                        aspect_label = f" [{aspect_tag}]" if aspect_tag else ""
                        excerpt = strip_wikilinks(b["content"])[:300]
                        self_lines.append(f"🪞{ts}{aspect_label}\n{excerpt}")
                        self_sources.append(b)
                    if self_lines:
                        body_text += "\n\n=== I ===\n" + "\n\n".join(self_lines)
                        for bucket in self_sources:
                            _record_breath_source(provenance, bucket, "i/self")
            except Exception as e:
                logger.warning(f"breath_hook I section failed: {e}")

            await sh.fire_webhook("breath_hook", {"surfaced": len(parts), "chars": len(body_text)})
            headers = {}
            try:
                encoded = _encode_breath_provenance(provenance)
                if encoded:
                    headers[BREATH_PROVENANCE_HEADER] = encoded
            except Exception as e:
                logger.warning(f"breath provenance encoding failed: {e}")
            return PlainTextResponse(body_text, headers=headers)
        except Exception as e:
            logger.warning(f"Breath hook failed: {e}")
            return PlainTextResponse("")


    # =============================================================
    # /dream-hook endpoint: Dedicated hook for Dreaming
    # Dreaming 专用挂载点
    # =============================================================
    @mcp.custom_route("/dream-hook", methods=["GET"])
    async def dream_hook(request):
        from starlette.responses import PlainTextResponse
        if not _is_hook_request_authorized(request):
            return PlainTextResponse("", status_code=401)
        try:
            all_buckets = await sh.bucket_mgr.list_all(include_archive=False)
            candidates = [
                b for b in all_buckets
                if b["metadata"].get("type") not in ("permanent", "feel", "plan", "letter", "self", "i")
                and not b["metadata"].get("pinned", False)
                and not b["metadata"].get("protected", False)
                and not b["metadata"].get("dont_surface", False)
            ]
            candidates.sort(key=lambda b: b["metadata"].get("created", ""), reverse=True)
            recent = candidates[:10]

            if not recent:
                return PlainTextResponse("")

            parts = []
            for b in recent:
                meta = b["metadata"]
                resolved_tag = "[已解决]" if meta.get("resolved", False) else "[未解决]"
                parts.append(
                    f"{meta.get('name', b['id'])} {resolved_tag} "
                    f"V{float(meta.get('valence') or 0.5):.1f}/A{float(meta.get('arousal') or 0.3):.1f}\n"
                    f"{strip_wikilinks(b['content'][:200])}"
                )

            body_text = "[Ombre Brain - Dreaming]\n" + "\n---\n".join(parts)
            await sh.fire_webhook("dream_hook", {"surfaced": len(parts), "chars": len(body_text)})
            return PlainTextResponse(body_text)
        except Exception as e:
            logger.warning(f"Dream hook failed: {e}")
            return PlainTextResponse("")
