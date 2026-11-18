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
        ("nomor0812 3456 7890", "nomor[phone]"),
        ("wa0812-3456-7890", "wa[phone]"),
        ("key 3f9a8b7c1234567d9e0f1a2b3c4d5e6f end", "key [token] end"),
        ("key " + "a1b2c3d4e5" * 4 + " end", "key [token] end"),
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


def _attrs(payload) -> dict:
    (span,) = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    return {a["key"]: a["value"] for a in span["attributes"]}


def _str(attrs, key):
    return attrs[key]["stringValue"]


def test_payload_shape():
    now = datetime(2026, 11, 18, 10, 0, 0, tzinfo=UTC)
    payload = tracing.build_batch(
        "mail a@b.com", _ok_answer(), model="m", seconds=2.0, flags=[], rate=0.25, now=now
    )
    (span,) = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(span["traceId"]) == 32 and len(span["spanId"]) == 16
    assert int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"]) == 2_000_000_000
    assert span["status"] == {"code": 1}
    a = _attrs(payload)
    assert _str(a, "langfuse.trace.name") == "sql-chat.ask"
    assert _str(a, "langfuse.environment") == "production"
    assert _str(a, "langfuse.release") == "unknown"
    assert _str(a, "langfuse.observation.type") == "generation"
    assert _str(a, "langfuse.observation.level") == "DEFAULT"
    assert _str(a, "langfuse.observation.model.name") == "m"
    assert json.loads(_str(a, "langfuse.observation.input")) == {"question": "mail [email]"}
    assert json.loads(_str(a, "langfuse.observation.output")) == {
        "answer": "Jakarta led.",
        "sql": "SELECT 1",
        "row_count": len(city_revenue().rows),
    }
    tags = [t["stringValue"] for t in a["langfuse.trace.tags"]["arrayValue"]["values"]]
    assert tags == ["app:sql-chat"]
    assert _str(a, "langfuse.observation.metadata.app") == "sql-chat"
    assert _str(a, "langfuse.observation.metadata.latency_ms") == "2000"
    assert json.loads(_str(a, "langfuse.observation.metadata.flags")) == []
    assert _str(a, "langfuse.observation.metadata.sample_rate") == "0.25"
    json.dumps(payload)


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
    assert _str(_attrs(warn), "langfuse.observation.level") == "WARNING"
    assert _str(_attrs(err), "langfuse.observation.level") == "ERROR"
    tags = _attrs(err)["langfuse.trace.tags"]["arrayValue"]["values"]
    assert [t["stringValue"] for t in tags] == ["app:sql-chat", "flag:error"]
    assert err["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["status"] == {"code": 2}


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
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append((a, k)) or httpx.Response(200))
    assert tracing.send_trace("q", _ok_answer(), model="m", seconds=1) is True
    ((args, kwargs),) = calls
    assert args == ("https://cloud.langfuse.com/api/public/otel/v1/traces",)
    assert kwargs["headers"] == {"x-langfuse-ingestion-version": "4"}
    assert kwargs["auth"] == ("pk-test", "sk-test")
    assert kwargs["timeout"] == 2.0
    json.dumps(kwargs["json"])


def test_rejected_response_logs_status_only(monkeypatch, keys, caplog):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(400, text="secret answer"))
    with caplog.at_level("WARNING"):
        tracing.send_trace("q", _ok_answer(), model="m", seconds=1)
    assert "400" in caplog.text and "secret" not in caplog.text


def test_ok_response_logs_nothing(monkeypatch, keys, caplog):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200))
    with caplog.at_level("WARNING"):
        tracing.send_trace("q", _ok_answer(), model="m", seconds=1)
    assert caplog.text == ""


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
