"""Batch rotation logic used by the scheduled ingestion jobs."""

from __future__ import annotations

from app.scheduler import _next_batch


def test_batches_walk_the_list_and_wrap():
    tickers = ["A", "B", "C", "D", "E"]

    batch, cursor = _next_batch(tickers, 0, 2)
    assert batch == ["A", "B"] and cursor == 2

    batch, cursor = _next_batch(tickers, cursor, 2)
    assert batch == ["C", "D"] and cursor == 4

    # Wraps: takes the tail then refills from the head.
    batch, cursor = _next_batch(tickers, cursor, 2)
    assert batch == ["E", "A"] and cursor == 1


def test_batch_larger_than_universe_is_capped():
    """A batch never repeats a ticker — that would waste rate-limited calls."""
    tickers = ["A", "B"]
    batch, cursor = _next_batch(tickers, 0, 5)
    assert batch == ["A", "B"]
    assert cursor == 0


def test_zero_size_is_clamped_to_one():
    batch, cursor = _next_batch(["A", "B"], 0, 0)
    assert batch == ["A"] and cursor == 1


def test_cursor_beyond_length_is_normalised():
    batch, _ = _next_batch(["A", "B", "C"], 7, 1)
    assert batch == ["B"]
