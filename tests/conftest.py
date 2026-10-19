import pytest

from sqlchat.db import QueryResult
from tests.fakes import city_revenue


@pytest.fixture
def revenue_result() -> QueryResult:
    return city_revenue()
