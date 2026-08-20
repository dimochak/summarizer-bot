"""Перевірка, що робота з БД не блокує event loop.

psycopg тут синхронний. Пул зʼєднань прибрав вартість конекту, але прямий
виклик із async-хендлера все одно зупиняв би обробку всього іншого — у всіх
чатах одразу — на час запиту.
"""
import asyncio
import time

from src.tools.db import db_call


def _slow_query(marker: list, delay: float = 0.2):
    """Імітує повільний синхронний запит до БД."""
    time.sleep(delay)
    marker.append("db")
    return "result"


async def test_db_call_does_not_block_the_loop():
    order: list = []

    async def ticker():
        # Якби db_call блокував loop, ці тіки не встигли б відпрацювати
        # ДО завершення «запиту».
        for _ in range(3):
            await asyncio.sleep(0.01)
            order.append("tick")

    result, _ = await asyncio.gather(db_call(_slow_query, order), ticker())

    assert result == "result"
    assert order.index("db") > order.index("tick"), (
        f"event loop був заблокований: {order}"
    )
    assert order.count("tick") == 3


async def test_db_calls_run_concurrently():
    """Кілька запитів мають виконуватись паралельно, а не один за одним."""
    started = time.perf_counter()
    await asyncio.gather(*(db_call(_slow_query, [], 0.2) for _ in range(4)))
    elapsed = time.perf_counter() - started

    # Послідовно це зайняло б ~0.8 с; беремо запас на повільний CI.
    assert elapsed < 0.6, f"запити виконались послідовно: {elapsed:.2f}s"


async def test_db_call_propagates_exceptions():
    def boom():
        raise ValueError("запит впав")

    try:
        await db_call(boom)
    except ValueError as e:
        assert str(e) == "запит впав"
    else:
        raise AssertionError("виняток мав прокинутись назовні")


async def test_db_call_passes_kwargs():
    def query(a, *, b):
        return a + b

    assert await db_call(query, 1, b=2) == 3
