"""Інтеграційний тест retention-прибирання. Потребує справжньої Postgres.

Був скриптом tests/repro_cleanup.py, який pytest навіть не збирав (ім'я не
підходило під шаблон), друкував результат замість assert і тому не міг
провалитись. Тепер це нормальний тест, який пропускається без БД.

Запуск:
    XXL_TEST_DATABASE_URL=postgresql://... uv run pytest -m integration
"""
import os
from datetime import datetime, timedelta

import pytest

import src.tools.config as config

TEST_CHAT_ID = 999999

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("XXL_TEST_DATABASE_URL"),
        reason="XXL_TEST_DATABASE_URL не заданий — інтеграційний тест пропущено",
    ),
]


@pytest.fixture
def live_db(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", os.environ["XXL_TEST_DATABASE_URL"])
    from src.tools.db import db, init_db

    init_db()
    yield db

    with db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM messages WHERE chat_id = %s", (TEST_CHAT_ID,))
        cur.execute("DELETE FROM panbot_limits WHERE chat_id = %s", (TEST_CHAT_ID,))
        conn.commit()


def test_cleanup_removes_only_data_older_than_retention(live_db):
    from src.tools.db import cleanup_old_data

    now_kyiv = datetime.now(config.KYIV)
    old_dt = now_kyiv - timedelta(days=40)
    new_dt = now_kyiv - timedelta(days=5)

    with live_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM messages WHERE chat_id = %s", (TEST_CHAT_ID,))
        cur.execute("DELETE FROM panbot_limits WHERE chat_id = %s", (TEST_CHAT_ID,))
        for message_id, dt, text in (
            (1, old_dt, "old message"),
            (2, new_dt, "new message"),
        ):
            cur.execute(
                "INSERT INTO messages (chat_id, message_id, ts_utc, text) VALUES (%s, %s, %s, %s)",
                (TEST_CHAT_ID, message_id, int(dt.timestamp()), text),
            )
        for dt, count in ((old_dt, 5), (new_dt, 3)):
            cur.execute(
                "INSERT INTO panbot_limits (user_id, chat_id, date, count) VALUES (%s, %s, %s, %s)",
                (1, TEST_CHAT_ID, dt.strftime("%Y-%m-%d"), count),
            )
        conn.commit()

    cleanup_old_data(30)

    with live_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT text FROM messages WHERE chat_id = %s ORDER BY ts_utc", (TEST_CHAT_ID,)
        )
        remaining_messages = [r["text"] for r in cur.fetchall()]
        cur.execute(
            "SELECT count(*) AS n FROM panbot_limits WHERE chat_id = %s", (TEST_CHAT_ID,)
        )
        remaining_limits = cur.fetchone()["n"]

    assert remaining_messages == ["new message"]
    assert remaining_limits == 1


def test_reply_chain_cte_walks_up_and_ignores_age(live_db):
    """Рекурсивний CTE з ChatContextHistory проти справжньої Postgres.

    Юніт-тести мокають _fetch_reply_chain цілком, тож сам SQL перевіряється
    лише тут: коректність рекурсії, обмеження глибини й те, що вік
    повідомлень на ланцюжок не впливає.
    """
    from src.panbot.history.manager import ChatContextHistory

    ancient = int((datetime.now(config.KYIV) - timedelta(days=400)).timestamp())

    with live_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM messages WHERE chat_id = %s", (TEST_CHAT_ID,))
        # 1 <- 2 <- 3: третє відповідає на друге, друге на перше
        for message_id, reply_to, text in (
            (1, None, "корінь"),
            (2, 1, "середина"),
            (3, 2, "листок"),
        ):
            cur.execute(
                """INSERT INTO messages (chat_id, message_id, ts_utc, text, reply_to_message_id)
                   VALUES (%s, %s, %s, %s, %s)""",
                (TEST_CHAT_ID, message_id, ancient, text, reply_to),
            )
        conn.commit()

    history = ChatContextHistory(chat_id=TEST_CHAT_ID, current_message_id=4, reply_to_id=3)
    chain = history._fetch_reply_chain(3)

    # Від точки входу вгору до кореня, попри вік у 400 днів.
    assert [r["text"] for r in chain] == ["листок", "середина", "корінь"]

    assert len(history._fetch_reply_chain(3, max_depth=2)) == 2
