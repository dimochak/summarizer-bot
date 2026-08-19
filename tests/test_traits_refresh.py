"""Тести щомісячного оновлення профілів користувачів.

Раніше traits оновлювались лише ручним запуском backfill_traits.py, тож
get_traits_block читав те, що колись залишив разовий прогін.
"""
import pytest

import src.tools.config as config
import src.traits.compose_traits as traits_mod

NOW = 1_700_000_000


@pytest.fixture(autouse=True)
def traits_config(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "TRAITS_REFRESH_DAYS", 30)
    monkeypatch.setattr(config, "TRAITS_REFRESH_BATCH", 50)
    monkeypatch.setattr(config, "TRAITS_REFRESH_CONCURRENCY", 3)


async def test_refreshes_only_stale_users(monkeypatch):
    seen_cutoff = []

    def mock_stale(cutoff_ts, limit):
        seen_cutoff.append((cutoff_ts, limit))
        return [1, 2, 3]

    refreshed = []

    async def mock_refresh(uid, lang="uk"):
        refreshed.append(uid)
        return {}

    monkeypatch.setattr(traits_mod, "get_user_ids_with_stale_traits", mock_stale)
    monkeypatch.setattr(traits_mod, "refresh_user_traits_from_messages_llm", mock_refresh)

    count = await traits_mod.refresh_stale_user_traits(now_ts=NOW)

    assert count == 3
    assert sorted(refreshed) == [1, 2, 3]
    # Поріг — рівно 30 діб назад від «зараз»
    assert seen_cutoff == [(NOW - 30 * 86400, 50)]


async def test_no_stale_users_is_a_noop(monkeypatch):
    monkeypatch.setattr(traits_mod, "get_user_ids_with_stale_traits", lambda c, limit: [])

    async def fail(*args, **kwargs):
        raise AssertionError("не мало викликатись")

    monkeypatch.setattr(traits_mod, "refresh_user_traits_from_messages_llm", fail)

    assert await traits_mod.refresh_stale_user_traits(now_ts=NOW) == 0


async def test_one_failure_does_not_abort_the_batch(monkeypatch):
    monkeypatch.setattr(
        traits_mod, "get_user_ids_with_stale_traits", lambda c, limit: [1, 2, 3]
    )

    async def mock_refresh(uid, lang="uk"):
        if uid == 2:
            raise RuntimeError("LLM впав")
        return {}

    monkeypatch.setattr(traits_mod, "refresh_user_traits_from_messages_llm", mock_refresh)

    # Двоє інших мають оновитись попри збій на одному
    assert await traits_mod.refresh_stale_user_traits(now_ts=NOW) == 2


async def test_skipped_without_openai_key(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")

    def fail(*args, **kwargs):
        raise AssertionError("не мало ходити в БД")

    monkeypatch.setattr(traits_mod, "get_user_ids_with_stale_traits", fail)

    assert await traits_mod.refresh_stale_user_traits(now_ts=NOW) == 0


async def test_batch_size_is_respected(monkeypatch):
    monkeypatch.setattr(config, "TRAITS_REFRESH_BATCH", 7)
    captured = []

    def mock_stale(cutoff_ts, limit):
        captured.append(limit)
        return []

    monkeypatch.setattr(traits_mod, "get_user_ids_with_stale_traits", mock_stale)
    await traits_mod.refresh_stale_user_traits(now_ts=NOW)

    assert captured == [7]
