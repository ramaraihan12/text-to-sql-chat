"""Sampled, redacted tracing of questions to Langfuse Cloud.

Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are both set. One OTLP/HTTP POST (the
legacy ingestion API is gone on Langfuse Cloud), with a short timeout, on a background thread so
the answer on screen never waits for it. Nothing here may raise.
"""

import hashlib
import json
import logging
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from sqlchat.pipeline import Answer

log = logging.getLogger(__name__)

APP = "sql-chat"
DEFAULT_HOST = "https://cloud.langfuse.com"
DEFAULT_SAMPLE_RATE = 0.25
TIMEOUT_SECONDS = 2.0
QUESTION_CAP = 500
ANSWER_CAP = 2000
SLOW_SECONDS = 15.0

NOTICE = (
    "A sample of questions is logged to improve answers, with emails and numbers removed. "
    "Logs are kept 30 days and never published."
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ID_PHONE = re.compile(r"(?<![\d+])(?:\+62|62|0)8\d[\d\s().-]{6,}\d")
_INTL_PHONE = re.compile(r"(?<![\d+])\+?\d[\d\s().-]{7,}\d")
_DIGITS = re.compile(r"\d{6,}")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{24,}")


def redact(text: str) -> str:
    # Tokens go before phones and numbers: a hex key with a run of digits would otherwise be
    # split by the number rule and leave pieces the token rule can no longer match.
    text = _EMAIL.sub("[email]", text)
    text = _TOKEN.sub("[token]", text)
    text = _ID_PHONE.sub("[phone]", text)
    text = _INTL_PHONE.sub(_phone_or_keep, text)
    return _DIGITS.sub("[number]", text)


def _phone_or_keep(match: re.Match[str]) -> str:
    value = match.group(0)
    return "[phone]" if sum(c.isdigit() for c in value) >= 9 else value


def clean(text: str, cap: int) -> str:
    # Cap first so a huge input isn't scanned, then again in case a redaction grew it.
    return redact(text[:cap])[:cap]


def sample_rate() -> float:
    try:
        return float(os.environ.get("TRACE_SAMPLE_RATE", DEFAULT_SAMPLE_RATE))
    except ValueError:
        return DEFAULT_SAMPLE_RATE


def is_sampled(question: str, rate: float) -> bool:
    digest = hashlib.sha256(question.strip().lower().encode()).hexdigest()
    return int(digest[:8], 16) / 2**32 < rate


def enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def flags_for(answer: Answer | None, seconds: float, *, error: bool = False) -> list[str]:
    flags: list[str] = []
    if answer is not None:
        if answer.status == "failed":
            flags.append("sql_error")
        elif answer.status == "blocked":
            flags.append("refused")
        elif answer.status == "ok" and answer.result is not None and not answer.result.rows:
            flags.append("empty_result")
    if error:
        flags.append("error")
    if seconds > SLOW_SECONDS:
        flags.append("slow")
    return flags


def build_batch(
    question: str,
    answer: Answer | None,
    *,
    model: str,
    seconds: float,
    flags: list[str],
    rate: float,
    release: str = "unknown",
    now: datetime | None = None,
) -> dict[str, Any]:
    end = now or datetime.now(UTC)
    start = datetime.fromtimestamp(end.timestamp() - seconds, UTC)
    output = {
        "answer": clean(answer.text, ANSWER_CAP) if answer else "",
        "sql": clean(answer.sql, ANSWER_CAP) if answer and answer.sql else None,
        "row_count": len(answer.result.rows) if answer and answer.result else 0,
    }
    question_in = {"question": clean(question, QUESTION_CAP)}
    metadata = {
        "app": APP,
        "model": model,
        "latency_ms": round(seconds * 1000),
        "flags": flags,
        "sample_rate": rate,
    }
    level = "ERROR" if "error" in flags else "WARNING" if flags else "DEFAULT"
    attributes = [
        _text("langfuse.observation.type", "generation"),
        _text("langfuse.trace.name", f"{APP}.ask"),
        _text("langfuse.environment", "production"),
        _text("langfuse.release", release),
        {
            "key": "langfuse.trace.tags",
            "value": {
                "arrayValue": {
                    "values": [
                        {"stringValue": t} for t in [f"app:{APP}", *(f"flag:{f}" for f in flags)]
                    ]
                }
            },
        },
        _text("langfuse.observation.level", level),
        _text("langfuse.observation.model.name", model),
        _text("langfuse.observation.input", json.dumps(question_in)),
        _text("langfuse.observation.output", json.dumps(output)),
        _text("langfuse.observation.metadata.app", APP),
        _text("langfuse.observation.metadata.model", model),
        _text("langfuse.observation.metadata.latency_ms", str(metadata["latency_ms"])),
        _text("langfuse.observation.metadata.flags", json.dumps(flags)),
        _text("langfuse.observation.metadata.sample_rate", str(rate)),
    ]
    span = {
        "traceId": uuid.uuid4().hex,
        "spanId": uuid.uuid4().hex[:16],
        "name": "answer",
        "kind": 1,
        "startTimeUnixNano": _nanos(start),
        "endTimeUnixNano": _nanos(end),
        "attributes": attributes,
        "status": {"code": 2 if "error" in flags else 1},
    }
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [_text("service.name", APP)]},
                "scopeSpans": [{"scope": {"name": APP}, "spans": [span]}],
            }
        ]
    }


def send_trace(
    question: str,
    answer: Answer | None,
    *,
    model: str,
    seconds: float,
    error: bool = False,
) -> bool:
    """Send one sampled trace. Returns whether a request went out. Never raises."""
    try:
        if not enabled():
            return False
        rate = sample_rate()
        if not is_sampled(question, rate):
            return False
        flags = flags_for(answer, seconds, error=error)
        release = os.environ.get("GIT_SHA") or os.environ.get("GITHUB_SHA") or "unknown"
        payload = build_batch(
            question, answer, model=model, seconds=seconds, flags=flags, rate=rate, release=release
        )
        host = (os.environ.get("LANGFUSE_HOST") or DEFAULT_HOST).rstrip("/")
        resp = httpx.post(
            f"{host}/api/public/otel/v1/traces",
            json=payload,
            auth=(os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]),
            headers={"x-langfuse-ingestion-version": "4"},
            timeout=TIMEOUT_SECONDS,
        )
        if not 200 <= resp.status_code < 300:
            # Status only: the body could echo the payload.
            log.warning("trace rejected: status %s", resp.status_code)
        return True
    except Exception as exc:
        # Type only: the message could carry the payload or a key.
        log.warning("trace not sent (%s)", type(exc).__name__)
        return False


def send_trace_in_background(
    question: str,
    answer: Answer | None,
    *,
    model: str,
    seconds: float,
    error: bool = False,
) -> None:
    if not enabled():
        return
    threading.Thread(
        target=send_trace,
        args=(question, answer),
        kwargs={"model": model, "seconds": seconds, "error": error},
        daemon=True,
    ).start()


def _nanos(moment: datetime) -> str:
    return str(int(moment.timestamp() * 1000) * 1_000_000)


def _text(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}
