import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChunkFailure:
    chunk_number: int
    error: str


async def process_chunks(
    chunks: Iterable[list[dict[str, Any]]],
    normalize: Callable[[dict[str, Any]], Awaitable[None]],
    concurrency: int,
    retry_attempts: int,
) -> tuple[int, int, list[ChunkFailure]]:
    """Process independent chunks with bounded memory and per-chunk retries."""
    semaphore = asyncio.Semaphore(concurrency)
    failures: list[ChunkFailure] = []

    async def run(chunk_number: int, chunk: list[dict[str, Any]]) -> tuple[int, ChunkFailure | None]:
        async with semaphore:
            for attempt in range(retry_attempts):
                try:
                    for row in chunk:
                        await normalize(row)
                    return len(chunk), None
                except Exception as exc:
                    if attempt + 1 == retry_attempts:
                        return 0, ChunkFailure(chunk_number, str(exc))
                    await asyncio.sleep(2**attempt)
            return 0, ChunkFailure(chunk_number, "retry loop exhausted")

    pending: set[asyncio.Task[tuple[int, ChunkFailure | None]]] = set()
    chunk_count = 0
    processed_rows = 0
    iterator = iter(chunks)
    exhausted = False
    while pending or not exhausted:
        while not exhausted and len(pending) < concurrency:
            try:
                chunk = next(iterator)
            except StopIteration:
                exhausted = True
                break
            pending.add(asyncio.create_task(run(chunk_count, chunk)))
            chunk_count += 1
        if not pending:
            continue
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            count, failure = task.result()
            processed_rows += count
            if failure:
                failures.append(failure)
    return chunk_count, processed_rows, failures