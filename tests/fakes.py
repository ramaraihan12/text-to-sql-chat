from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import BaseMessage
from pydantic import Field

from sqlchat.db import Column, QueryResult
from sqlchat.tools import SqlTools

SCHEMA_TEXT = """\
TABLE stores -- Coffee shop outlets.
  id integer
  name text
  city text
TABLE orders -- One row per receipt.
  id integer
  store_id integer
  ordered_at timestamp with time zone
  status text"""


class RecordingModel(GenericFakeChatModel):
    """Fake chat model that replays scripted replies and remembers what it was sent."""

    calls: list[list[BaseMessage]] = Field(default_factory=list)

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        self.calls.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


class FakeDatabase:
    """Stands in for Database: returns (or raises) scripted results in order."""

    def __init__(self, *responses: QueryResult | Exception) -> None:
        self.responses = list(responses)
        self.executed: list[str] = []

    def query(self, query: Any, params: Any = None) -> QueryResult:
        self.executed.append(str(query))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def scripted_model(*replies: str) -> RecordingModel:
    return RecordingModel(messages=iter(replies))


def make_tools(db: FakeDatabase, max_rows: int = 100) -> SqlTools:
    tools = SqlTools(db, max_rows=max_rows)  # type: ignore[arg-type]
    tools.schema_text = SCHEMA_TEXT
    return tools


def city_revenue() -> QueryResult:
    return QueryResult(
        columns=[Column("city", "text"), Column("revenue", "number")],
        rows=[("Jakarta", 1_250_000), ("Bandung", 830_000), ("Salatiga", 210_000)],
    )
