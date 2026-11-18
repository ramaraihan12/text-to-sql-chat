import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest

from sqlchat import tracing
from sqlchat.db import QueryResult
from sqlchat.pipeline import Answer
from tests.fakes import city_revenue


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
        "TRACE_SAMPLE_RATE",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("TRACE_SAMPLE_RATE", "1")


def _ok_answer() -> Answer:
    return Answer("q", "ok", "Jakarta led.", sql="SELECT 1", result=city_revenue())


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mail me at a.b+c@example.co.id now", "mail me at [email] now"),
        ("call 081234567890 please", "call [phone] please"),
        ("call +62 812-3456-7890", "call [phone]"),
        ("call +1 (415) 555-0132", "call [phone]"),
        ("order 1234567 was late", "order [number] was late"),
        ("sk" + "a" * 30, "[token]"),
        ("revenue in 2026-09-01 was 12345", "revenue in 2026-09-01 was 12345"),
    ],
)
def test_redact(text, expected):
    assert tracing.redact(text) == expected


def test_redact_order_email_before_number():
    assert tracing.redact("x1234567@example.com") == "[email]"


def test_clean_caps():
    assert len(tracing.clean("a b " * 1000, 500)) <= 500


def test_sampling_is_deterministic():
    q = "  Monthly Revenue  "
    h = int(hashlib.sha256(b"monthly revenue").hexdigest()[:8], 16) / 2**32
    assert tracing.is_sampled(q, h + 1e-9) is True
    assert tracing.is_sampled(q, h) is False
    assert tracing.is_sampled(q, 0) is False
    assert tracing.is_sampled(q, 1) is True


def test_payload_shape():
    now = datetime(2026, 11, 18, 10, 0, 0, tzinfo=UTC)
    payload = tracing.build_batch(
        "mail a@b.com", _ok_answer(), model="m", seconds=2.0, flags=[], rate=0.25, now=now
    )
    trace, gen = (e["body"] for e in payload["batch"])
    assert [e["type"] for e in payload["batch"]] == ["trace-create", "generation-create"]
    assert trace["name"] == "sql-chat.ask"
    assert trace["environment"] == "production"
    assert trace["release"] == "unknown"
    assert trace["input"] == {"question": "mail [email]"}
    assert trace["output"] == {
        "answer": "Jakarta led.",
        "sql": "SELECT 1",
        "row_count": len(city_revenue().rows),
    }
    assert trace["tags"] == ["app:sql-chat"]
    assert trace["metadata"] == {
        "app": "sql-chat",
        "model": "m",
        "latency_ms": 2000,
        "flags": [],
        "sample_rate": 0.25,
    }
    assert gen["traceId"] == trace["id"]
    assert gen["level"] == "DEFAULT"
    assert gen["output"] == trace["output"]
    assert gen["metadata"] == trace["metadata"]
    assert gen["endTime"] == "2026-11-18T10:00:00.000Z"
    assert len({payload["batch"][0]["id"], payload["batch"][1]["id"], trace["id"], gen["id"]}) == 4


def test_flags():
    full = city_revenue()
    empty = Answer("q", "ok", "none", sql="SELECT 1", result=QueryResult(full.columns, []))
    assert tracing.flags_for(_ok_answer(), 1) == []
    assert tracing.flags_for(empty, 1) == ["empty_result"]
    assert tracing.flags_for(Answer("q", "failed", "x"), 1) == ["sql_error"]
    assert tracing.flags_for(Answer("q", "blocked", "x"), 1) == ["refused"]
    assert tracing.flags_for(None, 20, error=True) == ["error", "slow"]


def test_levels():
    a = _ok_answer()
    warn = tracing.build_batch("q", a, model="m", seconds=1, flags=["slow"], rate=1)
    err = tracing.build_batch("q", None, model="m", seconds=1, flags=["error"], rate=1)
    assert warn["batch"][1]["body"]["level"] == "WARNING"
    assert err["batch"][1]["body"]["level"] == "ERROR"
    assert err["batch"][0]["body"]["tags"] == ["app:sql-chat", "flag:error"]


def test_off_without_keys(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not send")

    monkeypatch.setattr(httpx, "post", boom)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")  # secret missing
    monkeypatch.setenv("TRACE_SAMPLE_RATE", "1")
    assert tracing.send_trace("q", _ok_answer(), model="m", seconds=1) is False
    tracing.send_trace_in_background("q", _ok_answer(), model="m", seconds=1)


def test_sends_one_post(monkeypatch, keys):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append((a, k)))
    assert tracing.send_trace("q", _ok_answer(), model="m", seconds=1) is True
    ((args, kwargs),) = calls
    assert args == ("https://cloud.langfuse.com/api/public/ingestion",)
    assert kwargs["auth"] == ("pk-test", "sk-test")
    assert kwargs["timeout"] == 2.0
    json.dumps(kwargs["json"])


def test_not_sampled_sends_nothing(monkeypatch, keys):
    monkeypatch.setenv("TRACE_SAMPLE_RATE", "0")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("sent"))
    assert tracing.send_trace("q", _ok_answer(), model="m", seconds=1) is False


def test_exceptions_swallowed(monkeypatch, keys, caplog):
    def boom(*a, **k):
        raise httpx.ConnectTimeout("sk-test leaked?")

    monkeypatch.setattr(httpx, "post", boom)
    assert tracing.send_trace("q", _ok_answer(), model="m", seconds=1) is False
    assert "sk-test" not in caplog.text
    assert "ConnectTimeout" in caplog.text
