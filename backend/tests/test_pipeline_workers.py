import asyncio

import pytest

from masterone.pipeline.workers import process_chunks


@pytest.mark.asyncio
async def test_chunks_are_bounded_retried_and_processed_once():
    active = 0
    peak = 0
    attempts: dict[str, int] = {}
    seen: list[str] = []

    async def normalize(row):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        attempts[row["id"]] = attempts.get(row["id"], 0) + 1
        if row["id"] == "retry" and attempts[row["id"]] == 1:
            active -= 1
            raise RuntimeError("temporary")
        seen.append(row["id"])
        await asyncio.sleep(0)
        active -= 1

    count, rows, failures = await process_chunks([[{"id": "a"}], [{"id": "retry"}], [{"id": "c"}]], normalize, 2, 2)

    assert count == 3
    assert rows == 3
    assert failures == []
    assert sorted(seen) == ["a", "c", "retry"]
    assert peak <= 2


@pytest.mark.asyncio
async def test_permanent_chunk_failure_does_not_stop_other_chunks():
    seen = []

    async def normalize(row):
        if row["id"] == "bad":
            raise ValueError("invalid row")
        seen.append(row["id"])

    count, rows, failures = await process_chunks([[{"id": "bad"}], [{"id": "good"}]], normalize, 1, 1)

    assert count == 2
    assert rows == 1
    assert seen == ["good"]
    assert failures[0].chunk_number == 0