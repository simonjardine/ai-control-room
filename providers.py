"""OpenAI-compatible provider clients: list models, chat, test connection."""

from __future__ import annotations

from pathlib import Path
from email.utils import parsedate_to_datetime
import json
import os

import re

from typing import Any

import requests
import urllib.request
import queue
import threading
import time


DEFAULT_TIMEOUT = 60
LOCAL_TIMEOUT = 90
_REQUEST_SLOTS = threading.BoundedSemaphore(8)


def provider_timeout(provider: str) -> int:
    return LOCAL_TIMEOUT if provider == "local" else DEFAULT_TIMEOUT


def _bounded_call(call, timeout=DEFAULT_TIMEOUT):
    """Bound caller wall time, including DNS and slowly trickling responses.

    Socket timeouts alone are inactivity limits. Abandoned I/O is daemonized
    and capped at eight workers so repeated retries cannot leak unlimited threads.
    """
    timeout = min(max(DEFAULT_TIMEOUT, LOCAL_TIMEOUT), max(0.01, float(timeout)))
    deadline = time.monotonic() + timeout
    if not _REQUEST_SLOTS.acquire(timeout=timeout):
        raise requests.Timeout("Provider request deadline exceeded (workers busy)")
    result = queue.Queue(maxsize=1)

    def work():
        try:
            result.put((True, call()))
        except Exception as exc:
            result.put((False, exc))
        finally:
            _REQUEST_SLOTS.release()

    threading.Thread(target=work, daemon=True).start()
    try:
        ok, value = result.get(timeout=max(0, deadline - time.monotonic()))
    except queue.Empty:
        raise requests.Timeout(f"Provider request exceeded {timeout:g}s deadline") from None
    if not ok:
        raise value
    return value


def _request(method, url, *, timeout=DEFAULT_TIMEOUT, **kwargs):
    timeout = min(max(DEFAULT_TIMEOUT, LOCAL_TIMEOUT), max(0.01, float(timeout)))
    return _bounded_call(
        lambda: requests.request(method, url, timeout=timeout, **kwargs), timeout
    )


def _headers(api_key: str | None, provider: str) -> dict[str, str]:

    h = {"Content-Type": "application/json"}

    if api_key:

        h["Authorization"] = f"Bearer {api_key.strip()}"

    if provider == "openrouter":

        h.setdefault("HTTP-Referer", "https://localhost/ai-control-room")

        h.setdefault("X-Title", "AI Control Room")

    return h

def _normalize_base(base_url: str) -> str:

    return (base_url or "").rstrip("/")

def resolve_credentials(

    provider: str,

    cfg: dict[str, Any],

    *,

    purpose: str = "chat",

) -> tuple[str | None, str, str]:

    """Return (api_key, base_url, source_label).

    Explicit provider API keys are used for both chat and model discovery.
    purpose is retained for callers that distinguish these operations.

    """

    providers = cfg.get("providers") or {}

    p = providers.get(provider) or {}

    base = _normalize_base(p.get("base_url") or "")

    if provider == "openai":

        # Explicit provider keys take precedence over unrelated desktop logins.
        api_key = (p.get("api_key") or "").strip()
        if api_key:
            return api_key, base or "https://api.openai.com/v1", "api_key"
        return None, base or "https://api.openai.com/v1", "none"


    key = (p.get("api_key") or "").strip() or None

    defaults = {

        "xai": "https://api.x.ai/v1",

        "kimi": "https://api.moonshot.ai/v1",

        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",

        "anthropic": "https://api.anthropic.com",

        "openrouter": "https://openrouter.ai/api/v1",

        "local": "http://127.0.0.1:18434/v1",

    }

    return key, base or defaults.get(provider, ""), "api_key" if key else "none"

FRIENDLY_GGUF_PATTERNS: list[tuple[str, str]] = [

    ("gemma", "Gemma"),

    ("dolphin", "Dolphin"),

    ("ornith", "Ornith"),

    ("qwen.*coder|coder.*qwen|qwen2.?5-coder|qwen3-coder", "Qwen Coder"),

    ("mistral.?nemo|nemo", "Mistral Nemo"),

    ("ternary.?bonsai|bonsai", "Ternary Bonsai"),

]

def friendly_gguf_name(stem: str) -> str:

    """Map a GGUF filename stem to a short friendly label when known."""

    low = stem.lower()

    for pat, label in FRIENDLY_GGUF_PATTERNS:

        if re.search(pat, low):

            quant = ""

            mq = re.search(r"\.(Q[\w.]+)$", stem, re.I)

            if not mq:

                mq = re.search(r"(Q[2-8][_\w.]*|IQ[\w.]+|PQ[\w.]+)$", stem, re.I)

            if mq:

                quant = f" [{mq.group(1)}]"

            return f"{label}{quant}"

    if len(stem) > 56:

        return stem[:53] + "..."

    return stem


def _split_models_paths(models_path: str | None) -> list[str]:
    """Split models_path on ; or | into nonempty roots."""
    if not models_path:
        return []
    parts: list[str] = []
    for chunk in str(models_path).replace("|", ";").split(";"):
        chunk = chunk.strip().strip('"').strip("'")
        if chunk:
            parts.append(chunk)
    return parts


def resolve_hermes_local_endpoint() -> tuple[str, str]:
    """Read this user's Hermes runtime settings, with a loopback default."""
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    path = local_app_data / "hermes" / "runtimes" / "llamacpp" / "server.json"
    default = "http://127.0.0.1:18434/v1"
    if not path.exists():
        return default, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default, ""
    base = (data.get("base_url") or default).rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    key = data.get("api_key") or data.get("apiKey") or ""
    return base, str(key or "")


def list_gguf_catalog(models_path: str | None) -> list[dict[str, str]]:
    """
    Scan models_path for *.gguf.
    models_path may be one directory or several separated by ; or |.
    Returns dicts with stem, path, size_mb, label.
    """
    roots = _split_models_paths(models_path)
    if not roots:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for root_s in roots:
        root = Path(root_s)
        if not root.exists() or not root.is_dir():
            continue
        try:
            files = sorted(root.rglob("*.gguf"), key=lambda p: p.name.lower())
        except OSError:
            continue
        for f in files:
            name_l = f.name.lower()
            if name_l.startswith("mmproj") or "mmproj" in name_l:
                continue
            try:
                size_mb = f.stat().st_size / (1024 * 1024)
            except OSError:
                size_mb = 0.0
            stem = f.stem
            key = stem.lower()
            if key in seen:
                continue
            seen.add(key)
            label = "%s (%.0f MB)" % (stem, size_mb)
            out.append(
                {
                    "stem": stem,
                    "path": str(f),
                    "size_mb": "%.1f" % size_mb,
                    "label": label,
                }
            )
    return out


def list_gguf_stems(models_path: str | None) -> list[str]:

    """Scan models_path for .gguf files (stems as model-id hints)."""

    return [d["stem"] for d in list_gguf_catalog(models_path)]

def list_models(provider: str, cfg: dict[str, Any]) -> tuple[bool, str, list[str]]:
    key, base, _src = resolve_credentials(provider, cfg, purpose="models")
    if not base:
        return False, "Missing base URL", []
    if provider != "local" and not key:
        return False, "Add this provider's API key in Settings & APIs, then refresh.", []
    if provider == "local" and not key:
        hermes_base, hermes_key = resolve_hermes_local_endpoint()
        if base == hermes_base:
            key = hermes_key.strip()
    if provider == "anthropic":
        return _anthropic_models(key, base)
    try:
        resp = _request("GET", f"{base}/models", headers=_headers(key, provider),
                        timeout=provider_timeout(provider))
    except requests.RequestException as exc:
        if provider == "local":
            hints = list_gguf_stems(cfg.get("providers", {}).get("local", {}).get("models_path"))
            if hints:
                return False, f"Server unavailable; {len(hints)} GGUF filename hints", hints
        return False, f"Model list request failed ({type(exc).__name__})", []
    if resp.status_code >= 400:
        return False, f"HTTP {resp.status_code}: {_chat_error_text(resp.text, key)}", []
    try:
        data = resp.json()
    except ValueError:
        return False, "Invalid JSON from /models", []
    if not isinstance(data, dict):
        return False, "Invalid model catalogue", []
    if data.get("error"):
        error = data["error"]
        message = error.get("message") if isinstance(error, dict) else error
        return False, _chat_error_text(message, key), []
    models = _prefer_sort(provider, _parse_model_ids(data))
    return True, f"OK: {len(models)} model(s)", models


def _parse_model_ids(data: Any) -> list[str]:

    ids: list[str] = []

    if isinstance(data, dict):

        items = data.get("data") or data.get("models") or []

        if isinstance(items, list):

            for item in items:

                if isinstance(item, str):

                    ids.append(item)

                elif isinstance(item, dict):

                    mid = item.get("id") or item.get("name")

                    if mid:

                        ids.append(str(mid).removeprefix("models/"))

    seen: set[str] = set()

    out: list[str] = []

    for m in ids:

        if m not in seen:

            seen.add(m)

            out.append(m)

    return out

def _prefer_sort(provider: str, models: list[str]) -> list[str]:

    if provider == "openrouter":

        deep = [m for m in models if "deepseek" in m.lower()]

        rest = [m for m in models if m not in deep]

        return deep + rest

    if provider == "kimi":

        kimi = [m for m in models if "kimi" in m.lower() or "moonshot" in m.lower()]

        rest = [m for m in models if m not in kimi]

        return kimi + rest

    return models

def _safe_body(resp: requests.Response, limit: int = 180) -> str:

    text = (resp.text or "").replace("\n", " ")

    if len(text) > limit:

        text = text[:limit] + "..."

    return text

def _chat_temperature(provider: str, model: str, temperature: float) -> float:

    """Some Kimi/Moonshot models only accept temperature=1."""

    m = (model or "").lower()

    if provider == "kimi" or "kimi" in m or "moonshot" in m:

        return 1.0

    return temperature


def _chat_error_text(value: Any, api_key: str) -> str:
    """Display a bounded error message, never raw metadata or credentials."""
    text = value if isinstance(value, str) else "Provider returned an error"
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", text)
    return " ".join(text.split())[:400]


def _chat_response(resp: requests.Response, api_key: str) -> tuple[bool, str, bool]:
    """Return success, final text/error, and whether a bounded retry may help."""
    transient = {408, 429, 500, 502, 503, 504}
    try:
        data = resp.json()
    except ValueError:
        data = None
    error = data.get("error") if isinstance(data, dict) else None
    # OpenRouter can send HTTP 200 before an upstream failure. Its error body
    # takes precedence over choices, including any incomplete output.
    if error is not None:
        details = error if isinstance(error, dict) else {}
        code = details.get("code", resp.status_code)
        try:
            numeric_code = int(code)
        except (TypeError, ValueError):
            numeric_code = resp.status_code
        message = _chat_error_text(details.get("message", error), api_key)
        label = f"Provider error ({code})" if isinstance(code, (int, str)) and code != 200 else "Provider error"
        # Avoid copying arbitrary code/metadata fields into the transcript.
        if not isinstance(code, int) and not (isinstance(code, str) and code.isdigit()):
            label = "Provider error"
        return False, f"{label}: {message}", numeric_code in transient
    if resp.status_code >= 400:
        return False, f"HTTP {resp.status_code}: {_chat_error_text(resp.text, api_key)}", resp.status_code in transient
    if not isinstance(data, dict):
        return False, "Malformed provider response", False
    choices = data.get("choices")
    if choices is None or choices == []:
        return False, "Provider returned no completion choices or error details. Try this model again.", True
    if not isinstance(choices, list) or not isinstance(choices[0], dict):
        return False, "Malformed provider response", False
    choice = choices[0]
    if choice.get("finish_reason") == "length":
        return False, "Response truncated at token limit", False
    if choice.get("finish_reason") == "error":
        return False, "Provider stopped before completing the response", False
    msg = choice.get("message")
    if not isinstance(msg, dict):
        return False, "Malformed provider response", False
    content = msg.get("content") or ""
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content
                          if isinstance(p, dict) and isinstance(p.get("text"), str))
    if not isinstance(content, str):
        return False, "Malformed provider response", False
    if not content.strip():
        return False, "Empty final response", False
    return True, content, False


def _chat_retry_delay(resp: requests.Response) -> float:
    """Respect server backoff without extending the caller's total deadline."""
    value = resp.headers.get("Retry-After")
    if value:
        try:
            return max(0.5, float(value))
        except (TypeError, ValueError):
            try:
                return max(0.5, parsedate_to_datetime(value).timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                pass
    return 0.5


ANTHROPIC_MAX_TOKENS = 16000
# Claude models that accept output_config.effort; older ones (e.g. Haiku 4.5) reject it.
_ANTHROPIC_EFFORT = re.compile(r"claude-(opus-(4-[5-9]|[5-9])|sonnet-(4-6|[5-9])|fable|mythos)")


def _anthropic_client(key: str, base: str, timeout: float):
    import anthropic  # Imported on use; only the Claude provider needs the SDK.
    return anthropic.Anthropic(api_key=key, base_url=base, timeout=timeout, max_retries=0)


def _anthropic_error(exc: Exception, key: str) -> tuple[str, bool]:
    """Return a bounded, credential-free message and whether a retry may help."""
    import anthropic
    if isinstance(exc, anthropic.APITimeoutError):
        return f"Provider request exceeded {DEFAULT_TIMEOUT:g}s deadline", False
    if isinstance(exc, anthropic.AuthenticationError):
        return "Anthropic rejected the API key (HTTP 401). Check it in Settings & APIs.", False
    if isinstance(exc, anthropic.APIStatusError):
        retry = isinstance(exc, (anthropic.RateLimitError, anthropic.InternalServerError,
                                 anthropic.OverloadedError))
        return f"HTTP {exc.status_code}: {_chat_error_text(exc.message, key)}", retry
    if isinstance(exc, anthropic.APIConnectionError):
        return f"Provider connection error ({type(exc).__name__})", True
    return f"Provider request failed ({type(exc).__name__})", False


def _anthropic_models(key: str, base: str) -> tuple[bool, str, list[str]]:
    try:
        client = _anthropic_client(key, base, DEFAULT_TIMEOUT)
        models = _bounded_call(lambda: [m.id for m in client.models.list()], DEFAULT_TIMEOUT)
    except requests.Timeout:
        return False, "Model list request timed out", []
    except Exception as exc:
        return False, _anthropic_error(exc, key)[0], []
    return True, f"OK: {len(models)} model(s)", models


def _anthropic_chat(key: str, base: str, model: str,
                    messages: list[dict[str, str]]) -> tuple[bool, str]:
    """Claude through the official Messages API. The room's JSON contract stays in the prompt."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [dict(role=m["role"], content=m["content"]) for m in messages
             if m["role"] in ("user", "assistant")]
    request: dict[str, Any] = dict(model=model, max_tokens=ANTHROPIC_MAX_TOKENS, messages=turns)
    if system:
        request["system"] = system
    if _ANTHROPIC_EFFORT.match(model):
        request["output_config"] = {"effort": "low"}  # Conversational turns; keeps replies quick.
    deadline = time.monotonic() + DEFAULT_TIMEOUT
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        client = _anthropic_client(key, base, remaining)
        try:
            message = _bounded_call(lambda: client.messages.create(**request), remaining)
        except requests.Timeout:
            break
        except Exception as exc:
            text, retryable = _anthropic_error(exc, key)
            if not retryable or attempt == 1 or deadline - time.monotonic() <= 1:
                return False, text
            time.sleep(0.5)
            continue
        if message.stop_reason == "max_tokens":
            return False, "Response truncated at token limit"
        if message.stop_reason == "refusal":
            return False, "Claude declined this request (safety refusal)."
        text = "".join(block.text for block in message.content if block.type == "text")
        if not text.strip():
            return False, "Empty final response"
        return True, text
    return False, f"Provider request exceeded {DEFAULT_TIMEOUT:g}s deadline"


def chat_completion(
    provider: str,
    cfg: dict[str, Any],
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.4,
    max_tokens: int = 512,
) -> tuple[bool, str]:
    key, base, _src = resolve_credentials(provider, cfg, purpose="chat")
    if not base:
        return False, "Missing base URL"
    if provider != "local" and not key:
        return False, "No provider API key configured"
    if not model:
        return False, "No model selected"
    if provider == "local" and not key:
        hermes_base, hermes_key = resolve_hermes_local_endpoint()
        if base == hermes_base:
            key = hermes_key.strip()
    if provider == "anthropic":
        return _anthropic_chat(key, base, model, messages)

    payload: dict[str, Any] = {
        "model": model, "messages": messages,
        "response_format": {"type": "json_object"},
    }
    if provider == "gemini":
        # Gemini's compatibility layer documents schema outputs, not json_object, so the
        # JSON contract stays in the prompt. Thinking shares the output budget: keep it
        # low on thinking models and leave room for the final reply.
        del payload["response_format"]
        payload["max_tokens"] = max(8192, max_tokens)
        if re.match(r"(models/)?gemini-(2\.5|[3-9])", model):
            payload["reasoning_effort"] = "low"
    elif provider == "openai":
        payload["max_completion_tokens"] = max(1024, max_tokens)
        if model.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
            payload["reasoning_effort"] = "low"
        else:
            payload["temperature"] = temperature
    else:
        payload["max_tokens"] = max_tokens
        payload["temperature"] = _chat_temperature(provider, model, temperature)
        # Preserve each model's default reasoning mode. Some endpoints require it;
        # disabling it globally rejects valid models such as Space Bunny Alpha.

    timeout = provider_timeout(provider)
    deadline = time.monotonic() + timeout
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, f"Provider request exceeded {timeout:g}s deadline"
        try:
            resp = _request("POST", f"{base}/chat/completions",
                            headers=_headers(key, provider), json=payload, timeout=remaining)
        except requests.Timeout:
            return False, f"Provider request exceeded {timeout:g}s deadline"
        except requests.RequestException as exc:
            # Header-validation exceptions can contain credentials; never log them.
            return False, f"Provider connection error ({type(exc).__name__})"
        ok, text, retryable = _chat_response(resp, key)
        if ok or not retryable or attempt == 1:
            return ok, text
        delay = _chat_retry_delay(resp)
        if delay >= deadline - time.monotonic():
            return False, text
        time.sleep(delay)
    return False, "Provider request failed"

def test_connection(provider: str, cfg: dict[str, Any]) -> tuple[bool, str, list[str]]:

    return list_models(provider, cfg)

def probe_local_server(
    base_url: str | None = None,
    timeout: float = 2.0,
    api_key: str | None = None,
) -> tuple[bool, str]:
    """Quick reachability check for Hermes Agent OpenAI-compatible llama-server."""
    hermes_base, hermes_key = resolve_hermes_local_endpoint()
    base = _normalize_base(base_url) or hermes_base or "http://127.0.0.1:18434/v1"
    key = (api_key if api_key is not None else hermes_key) or ""
    url = base.rstrip("/") + "/models"
    req = urllib.request.Request(url, method="GET")
    if key:
        req.add_header("Authorization", "Bearer %s" % key)
    timeout = min(LOCAL_TIMEOUT, max(0.01, float(timeout)))

    def fetch():
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(4000), resp.status

    try:
        raw, status = _bounded_call(fetch, timeout)
        if raw is not None:
            try:
                data = json.loads(raw.decode("utf-8", errors="replace"))
                ids = [
                    m.get("id")
                    for m in (data.get("data") or [])
                    if isinstance(m, dict) and m.get("id")
                ]
                if ids:
                    return True, "online (%d model(s)): %s" % (len(ids), ", ".join(ids[:4]))
                return True, "online (/v1/models OK)"
            except Exception:
                return True, "online (HTTP %s)" % status
    except Exception as e:
        return False, str(e)[:160]
