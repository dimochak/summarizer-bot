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
        cur.execute("DELETE FROM voice_limits WHERE chat_id = %s", (TEST_CHAT_ID,))
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
            cur.execute(
                "INSERT INTO voice_limits (user_id, chat_id, date, count) VALUES (%s, %s, %s, %s)",
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
        cur.execute(
            "SELECT count(*) AS n FROM voice_limits WHERE chat_id = %s", (TEST_CHAT_ID,)
        )
        remaining_voice_limits = cur.fetchone()["n"]

    assert remaining_messages == ["new message"]
    assert remaining_limits == 1
    assert remaining_voice_limits == 1


def test_voice_slots_count_past_limit_and_release_never_goes_negative(live_db):
    from src.tools.db import claim_voice_slot, release_voice_slot

    day = "2026-01-01"
    assert [claim_voice_slot(1, TEST_CHAT_ID, day) for _ in range(3)] == [1, 2, 3]
    # Інший день і інший чат — окремі лічильники.
    assert claim_voice_slot(1, TEST_CHAT_ID, "2026-01-02") == 1
    assert claim_voice_slot(1, TEST_CHAT_ID + 1, day) == 1

    release_voice_slot(1, TEST_CHAT_ID, day)
    assert claim_voice_slot(1, TEST_CHAT_ID, day) == 3

    for _ in range(5):
        release_voice_slot(1, TEST_CHAT_ID, day)
    assert claim_voice_slot(1, TEST_CHAT_ID, day) == 1

    with live_db() as conn:
        conn.cursor().execute("DELETE FROM voice_limits WHERE chat_id = %s", (TEST_CHAT_ID + 1,))
        conn.commit()


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


def test_self_repost_is_not_a_bayan(live_db):
    """Самоповтор — не баян; повтор чужого — баян, з посиланням на перший пост."""
    from src.tools.db import get_duplicate_photo_message_id as dup, upsert_photo_message

    PETRO, OLYA, IVAN = 1, 2, 3
    pic = "integration-test-pic"

    with live_db() as conn:
        conn.cursor().execute("DELETE FROM photo_messages WHERE chat_id = %s", (TEST_CHAT_ID,))
        conn.commit()

    def post(message_id, user_id):
        upsert_photo_message(TEST_CHAT_ID, message_id, 1000 + message_id, pic, user_id)
        return dup(TEST_CHAT_ID, pic, exclude_message_id=message_id, user_id=user_id)

    try:
        assert post(1, PETRO) is None, "перший пост — не баян"
        assert post(2, PETRO) is None, "Петро повторив власний мем — не баян"
        assert post(3, OLYA) == 1, "Оля повторила Петрів мем — баян, посилання на Петрів пост"
        assert post(4, PETRO) is None, "автор повторив свій мем після чужого репосту — не баян"
        assert post(5, OLYA) is None, "Олю вже підколювали — вдруге за те саме не треба"
        assert post(6, IVAN) == 1, "новий репостер — знову баян із посиланням на оригінал"
    finally:
        with live_db() as conn:
            conn.cursor().execute("DELETE FROM photo_messages WHERE chat_id = %s", (TEST_CHAT_ID,))
            conn.commit()


def test_reaction_claim_is_atomic_and_respects_cooldown(live_db):
    """Кулдаун реакцій проти справжньої Postgres: другий захват у межах кулдауну
    має відмовити, а після нього — знову дозволити."""
    from src.tools.db import claim_reaction, is_reaction_on_cooldown

    rule, hour = "integration-test-rule", 3600

    with live_db() as conn:
        conn.cursor().execute("DELETE FROM reaction_state WHERE chat_id = %s", (TEST_CHAT_ID,))
        conn.commit()

    try:
        assert claim_reaction(TEST_CHAT_ID, rule, 10_000, hour) is True, "перший захват"
        assert is_reaction_on_cooldown(TEST_CHAT_ID, rule, 10_100, hour) is True
        assert claim_reaction(TEST_CHAT_ID, rule, 10_100, hour) is False, "у межах кулдауну"
        assert claim_reaction(TEST_CHAT_ID, rule, 10_000 + hour, hour) is True, "кулдаун минув"
        assert claim_reaction(TEST_CHAT_ID, "other-rule", 10_100, hour) is True, \
            "кулдаун окремий для кожного правила"
    finally:
        with live_db() as conn:
            conn.cursor().execute("DELETE FROM reaction_state WHERE chat_id = %s", (TEST_CHAT_ID,))
            conn.commit()


def test_air_alert_state_reports_init_same_changed(live_db):
    from src.tools.db import record_air_alert_state as record

    region = "test:integration"

    def wipe():
        with live_db() as conn:
            conn.cursor().execute("DELETE FROM air_alert_state WHERE region = %s", (region,))
            conn.commit()

    wipe()
    try:
        assert record(region, False, 1) == "init"
        assert record(region, False, 2) == "same"
        assert record(region, True, 3) == "changed"
        assert record(region, True, 4) == "same"
        assert record(region, False, 5) == "changed"
    finally:
        wipe()
