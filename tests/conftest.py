import os

import pytest

from sqlchat.db import QueryResult
from tests.fakes import city_revenue


@pytest.fixture
def revenue_result() -> QueryResult:
    return city_revenue()


@pytest.fixture(autouse=True)
def no_real_traces(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("LANGFUSE_") or name == "TRACE_SAMPLE_RATE":
            monkeypatch.delenv(name)
