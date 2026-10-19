"""Streamlit chat UI. Start it with `streamlit run streamlit_app.py`."""

import logging
import time
from dataclasses import dataclass

import pandas as pd
import psycopg
import streamlit as st

from sqlchat.charts import ChartSpec
from sqlchat.config import Settings, get_settings
from sqlchat.db import QueryResult
from sqlchat.pipeline import Answer, TextToSql, build_pipeline

log = logging.getLogger(__name__)

USER_AVATAR = ":material/person:"
BOT_AVATAR = ":material/local_cafe:"

EXAMPLES = {
    "English": [
        "Which city had the highest revenue in September 2026?",
        "Monthly revenue in 2026",
        "How many orders came through delivery each quarter?",
    ],
    "Bahasa Indonesia": [
        "Produk apa yang paling laris di Bandung tahun 2026?",
        "Rata-rata nilai transaksi per toko bulan Agustus 2026",
        "Jam berapa toko paling ramai?",
    ],
}


@dataclass
class Turn:
    answer: Answer
    seconds: float


@st.cache_resource(show_spinner="Connecting to the database...")
def get_pipeline() -> TextToSql:
    pipeline = build_pipeline(get_settings())
    _ = pipeline.tools.schema_text  # load the schema once, not on the first question
    return pipeline


def main() -> None:
    st.set_page_config(page_title="Kopi Lereng sales chat", page_icon=":material/local_cafe:")
    settings = get_settings()
    _sidebar(settings)

    st.title("Ask the sales data")
    st.caption(
        "Kopi Lereng, a made-up chain of 14 coffee shops across Indonesia. "
        "Orders from January 2025 to September 2026. English or Bahasa Indonesia both work."
    )

    try:
        pipeline = get_pipeline()
    except (psycopg.Error, PermissionError) as exc:
        st.error(f"Can't use the database: {exc}")
        st.info("Check DATABASE_URL in .env and run `uv run sqlchat setup` once.")
        st.stop()

    history: list[Turn] = st.session_state.setdefault("history", [])
    for turn in history:
        _render_turn(turn, settings)

    question = st.chat_input("Ask about revenue, products, stores or customers")
    question = question or st.session_state.pop("queued", None)
    if not question:
        return

    with st.chat_message("user", avatar=USER_AVATAR):
        st.markdown(_escape(question))
    with st.chat_message("assistant", avatar=BOT_AVATAR):
        started = time.perf_counter()
        try:
            with st.spinner("Writing the query and checking it..."):
                answer = pipeline.ask(question)
        except Exception as exc:
            # Model server down, bad API key, database gone: show it instead of a traceback.
            log.exception("question failed")
            st.error(f"Something went wrong before an answer came back: {exc}")
            return
        turn = Turn(answer, time.perf_counter() - started)
        _render_answer(turn, settings)
    history.append(turn)


def _sidebar(settings: Settings) -> None:
    with st.sidebar:
        st.subheader("Try a question")
        for language, questions in EXAMPLES.items():
            st.caption(language)
            for q in questions:
                st.button(q, key=q, on_click=_queue, args=(q,), type="tertiary")

        st.subheader("Guardrails")
        timeout = settings.statement_timeout_ms / 1000
        st.caption(
            "- Connects as a read-only Postgres role\n"
            "- One SELECT at a time, on the five sales tables only\n"
            f"- At most {settings.max_rows} rows and {timeout:g}s per query\n"
            "- A failed query gets one retry with the error"
        )
        st.caption(f"Model: `{settings.llm_provider}` / `{settings.llm_model}`")
        if st.button("Clear chat", icon=":material/delete_sweep:"):
            st.session_state.history = []
            st.rerun()


def _queue(question: str) -> None:
    st.session_state.queued = question


def _render_turn(turn: Turn, settings: Settings) -> None:
    with st.chat_message("user", avatar=USER_AVATAR):
        st.markdown(_escape(turn.answer.question))
    with st.chat_message("assistant", avatar=BOT_AVATAR):
        _render_answer(turn, settings)


def _render_answer(turn: Turn, settings: Settings) -> None:
    answer = turn.answer
    if answer.status == "no_sql":
        st.info(_escape(answer.text))
        return
    if answer.status == "blocked":
        st.warning(_escape(answer.text))
        st.code(answer.sql or "", language="sql")
        return
    if answer.status == "failed":
        st.error(_escape(answer.text))
        _attempts(answer)
        return

    assert answer.result is not None
    st.markdown(f"**{_escape(answer.text)}**")
    st.caption(_meta(turn, settings.max_rows))
    frame = _frame(answer.result)
    if answer.chart:
        _chart(frame, answer.chart)
    st.dataframe(frame, hide_index=True)
    with st.expander("SQL"):
        st.code(answer.sql or "", language="sql")
        if len(answer.attempts) > 1:
            _attempts(answer)


def _meta(turn: Turn, max_rows: int) -> str:
    answer = turn.answer
    rows = len(answer.result.rows) if answer.result else 0
    parts = [f"`{rows} rows`" + (" (limit reached)" if rows == max_rows else "")]
    parts.append(f"`{turn.seconds:.1f}s`")
    if len(answer.attempts) > 1:
        parts.append("fixed on the second try")
    return " · ".join(parts)


def _attempts(answer: Answer) -> None:
    for number, attempt in enumerate(answer.attempts, start=1):
        if attempt.error:
            st.caption(f"Try {number} failed: {_escape(attempt.error)}")
            st.code(attempt.sql, language="sql")


def _frame(result: QueryResult) -> pd.DataFrame:
    names = _unique([c.name for c in result.columns])
    frame = pd.DataFrame(result.rows, columns=names)
    for name, column in zip(names, result.columns, strict=True):
        if column.kind == "number":
            values = pd.to_numeric(frame[name])
            if values.notna().all() and (values % 1 == 0).all():
                values = values.astype("int64")
            frame[name] = values
        elif column.kind == "time":
            values = pd.to_datetime(frame[name])
            if values.dt.tz is not None:
                values = values.dt.tz_localize(None)
            frame[name] = values
    return frame


def _chart(frame: pd.DataFrame, chart: ChartSpec) -> None:
    if chart.kind == "line":
        st.line_chart(frame, x=chart.x, y=chart.y, height=280)
        return
    labels_are_text = not pd.api.types.is_numeric_dtype(frame[chart.x])
    height = max(220, 30 * len(frame)) if labels_are_text else 280
    st.bar_chart(frame, x=chart.x, y=chart.y, horizontal=labels_are_text, sort=False, height=height)


def _unique(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        result.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return result


def _escape(text: str) -> str:
    # Streamlit treats $...$ as LaTeX; model answers sometimes contain a dollar sign.
    return text.replace("$", r"\$")
