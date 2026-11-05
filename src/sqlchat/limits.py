"""What one public demo may spend. Every question costs model calls on a shared free quota.

Session counts are gone after a page reload, so the daily cap in Postgres is the real wall. It
has its own table and role because the app's main role is read-only, and no model-written SQL
ever runs on this connection.
"""

import psycopg

from sqlchat.config import Settings

# One statement, so two sessions racing for the last slot can't both get it. The day rolls over
# at midnight Pacific because that's when Gemini's daily quota resets.
_CLAIM = """
    INSERT INTO daily_usage AS u (day, questions)
    VALUES ((now() AT TIME ZONE 'America/Los_Angeles')::date, 1)
    ON CONFLICT (day) DO UPDATE SET questions = u.questions + 1
    WHERE u.questions < %s
    RETURNING u.questions
"""


def check_question(question: str, asked: int, settings: Settings) -> str | None:
    """The reason this question can't be sent, or None. Doesn't touch the database."""
    if len(question) > settings.max_question_chars:
        return f"Please keep questions to {settings.max_question_chars} characters."
    if asked >= settings.session_question_limit:
        return (
            f"That's the {settings.session_question_limit} questions this demo allows per visit. "
            "Thanks for trying it!"
        )
    return None


class DailyQuota:
    def __init__(self, dsn: str, limit: int) -> None:
        self._dsn = dsn
        self._limit = limit

    def claim(self) -> bool:
        """Take one question from today's cap. False once the cap is reached."""
        if self._limit < 1:
            return False
        with psycopg.connect(self._dsn, connect_timeout=5) as conn:
            return conn.execute(_CLAIM, (self._limit,)).fetchone() is not None
