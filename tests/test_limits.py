from sqlchat.config import Settings
from sqlchat.limits import DailyQuota, check_question

SETTINGS = Settings(max_question_chars=10, session_question_limit=2, _env_file=None)


def test_a_short_question_within_the_session_limit_passes() -> None:
    assert check_question("a" * 10, 1, SETTINGS) is None


def test_a_long_question_is_refused() -> None:
    reason = check_question("a" * 11, 0, SETTINGS)
    assert reason is not None
    assert "10 characters" in reason


def test_the_session_limit_counts_questions_already_asked() -> None:
    reason = check_question("hello", 2, SETTINGS)
    assert reason is not None
    assert "2 questions" in reason


def test_a_limit_of_zero_closes_the_demo_without_a_connection() -> None:
    assert DailyQuota("postgresql://nobody@invalid.invalid/none", limit=0).claim() is False
