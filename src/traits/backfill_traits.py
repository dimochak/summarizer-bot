import asyncio
import os
from contextlib import closing
from time import time

from src.tools.db import db, init_db
from src.traits.compose_traits import refresh_user_traits_from_messages_llm  # async


def _get_unique_user_ids(limit: int | None = None) -> list[int]:
    """
    Повертає унікальні user_id з таблиці messages (без NULL).
    """
    with closing(db()) as conn, closing(conn.cursor()) as cur:
        sql = "SELECT DISTINCT user_id FROM messages WHERE user_id IS NOT NULL"
        if limit:
            sql += " LIMIT %s"
            cur.execute(sql, (limit,))
        else:
            cur.execute(sql)
        rows = cur.fetchall()
    return [r["user_id"] for r in rows if r.get("user_id") is not None]


async def _process_user_ids(user_ids: list[int], lang: str = "uk", concurrency: int = 5):
    """
    Запускає побудову traits для списку користувачів з обмеженням паралельності.
    """
    sem = asyncio.Semaphore(concurrency)

    async def worker(uid: int):
        async with sem:
            try:
                traits = await refresh_user_traits_from_messages_llm(uid, lang=lang)
                print(f"[OK] user_id={uid} sample_size={traits.get('sample_size', 0)}")
            except Exception as e:
                print(f"[ERR] user_id={uid}: {e}")

    await asyncio.gather(*(worker(uid) for uid in user_ids))


def main():
    init_db()

    limit_env = os.getenv("TRAITS_BACKFILL_USER_LIMIT")
    limit = int(limit_env) if limit_env and limit_env.isdigit() else None
    user_ids = _get_unique_user_ids(limit=limit)
    if not user_ids:
        print("No users found in messages.")
        return

    # 3) Масове оновлення traits через LLM
    lang = os.getenv("TRAITS_LANG", "uk")
    concurrency_env = os.getenv("TRAITS_CONCURRENCY")
    concurrency = int(concurrency_env) if concurrency_env and concurrency_env.isdigit() else 5

    started = time()
    asyncio.run(_process_user_ids(user_ids, lang=lang, concurrency=concurrency))
    took = time() - started
    print(f"Done. Users processed: {len(user_ids)} in {took:.1f}s")


if __name__ == "__main__":
    main()