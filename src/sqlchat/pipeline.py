"""Question in, answer out: write SQL, check it, run it, explain the result."""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import psycopg
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from sqlchat.charts import ChartSpec, suggest_chart
from sqlchat.config import Settings
from sqlchat.db import Database, QueryResult, describe_error
from sqlchat.llm import build_chat_model
from sqlchat.prompts import ANSWER_SYSTEM, ANSWER_USER, SQL_REJECTED, SQL_RETRY, SQL_SYSTEM
from sqlchat.schema import ALLOWED_TABLES
from sqlchat.tools import SqlTools
from sqlchat.validator import UnsafeQueryError

# The first try plus one retry, with the Postgres error or the validator's reason fed back.
MAX_ATTEMPTS = 2
ROWS_FOR_SUMMARY = 20

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:sql|postgresql|postgres)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

Status = Literal["ok", "blocked", "failed", "no_sql"]


@dataclass
class Attempt:
    sql: str
    error: str | None = None
    blocked: bool = False  # rejected by the validator, so it never reached Postgres


@dataclass
class Answer:
    question: str
    status: Status
    text: str
    sql: str | None = None
    result: QueryResult | None = None
    chart: ChartSpec | None = None
    attempts: list[Attempt] = field(default_factory=list)


class TextToSql:
    def __init__(self, model: BaseChatModel, tools: SqlTools) -> None:
        self._model = model
        self.tools = tools

    def ask(self, question: str) -> Answer:
        system = SQL_SYSTEM.format(schema=self.tools.schema_text, today=date.today().isoformat())
        messages: list[BaseMessage] = [SystemMessage(system), HumanMessage(question)]
        attempts: list[Attempt] = []

        for _ in range(MAX_ATTEMPTS):
            reply = _text(self._model.invoke(messages))
            if reply.startswith("NO_SQL"):
                reason = reply.removeprefix("NO_SQL").lstrip(" :")
                return Answer(question, "no_sql", reason or "That isn't in the sales data.")

            candidate = extract_sql(reply)
            try:
                safe, result = self.tools.run_sql(candidate)
            except UnsafeQueryError as exc:
                # Usually an honest mistake (a made-up table, stray prose), so the model gets
                # the reason and one more go. The retry is validated like any other query.
                attempts.append(Attempt(candidate, str(exc), blocked=True))
                messages += [AIMessage(reply), HumanMessage(SQL_REJECTED.format(reason=exc))]
                continue
            except psycopg.Error as exc:
                # No SQLSTATE means the connection itself failed; the model can't fix that.
                if exc.sqlstate is None:
                    raise
                error = describe_error(exc)
                attempts.append(Attempt(candidate, error))
                messages += [AIMessage(reply), HumanMessage(SQL_RETRY.format(error=error))]
                continue

            attempts.append(Attempt(safe.sql))
            return Answer(
                question,
                "ok",
                self._summarize(question, result),
                sql=safe.sql,
                result=result,
                chart=suggest_chart(result),
                attempts=attempts,
            )

        last = attempts[-1]
        if last.blocked:
            return Answer(
                question,
                "blocked",
                f"Blocked by the guardrails: {last.error}",
                sql=last.sql,
                attempts=attempts,
            )
        return Answer(
            question,
            "failed",
            f"The query still failed after a retry: {last.error}",
            sql=last.sql,
            attempts=attempts,
        )

    def _summarize(self, question: str, result: QueryResult) -> str:
        shown = result.rows[:ROWS_FOR_SUMMARY]
        header = " | ".join(c.name for c in result.columns)
        lines = [header, *(" | ".join(_cell(v) for v in row) for row in shown)]
        prompt = ANSWER_USER.format(
            question=question,
            row_count=len(result.rows),
            shown=f", first {len(shown)} shown" if len(shown) < len(result.rows) else "",
            table="\n".join(lines) if shown else "(no rows)",
        )
        reply = self._model.invoke([SystemMessage(ANSWER_SYSTEM), HumanMessage(prompt)])
        return " ".join(_text(reply).split())


def build_pipeline(settings: Settings) -> TextToSql:
    db = Database(settings.database_url, settings.statement_timeout_ms)
    db.ensure_read_only_role(ALLOWED_TABLES)
    return TextToSql(build_chat_model(settings), SqlTools(db, settings.max_rows))


def extract_sql(reply: str) -> str:
    """Pull the statement out of a model reply, with or without a code fence."""
    reply = _THINK.sub("", reply).strip()
    match = _FENCE.search(reply)
    return (match.group(1) if match else reply).strip()


def _text(message: BaseMessage) -> str:
    return _THINK.sub("", message.text).strip()


def _cell(value: object) -> str:
    return "NULL" if value is None else str(value)
