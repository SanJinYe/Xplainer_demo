"""In-memory explanation request telemetry."""

from collections import deque
from statistics import mean
from typing import Optional


METRIC_SAMPLE_LIMIT = 200
PERCENTILE_MIN_SAMPLE_COUNT = 50


class ExplanationMetricsTracker:
    """Track rolling latency, cache-hit, and output-size metrics."""

    def __init__(self, sample_limit: int = METRIC_SAMPLE_LIMIT):
        self._summary = _MetricBucket(sample_limit)
        self._detailed_stream = _MetricBucket(sample_limit)

    def record_summary(
        self,
        *,
        total_ms: float,
        output_chars: int,
        cache_hit: bool,
        error: bool = False,
    ) -> None:
        self._summary.record(
            total_ms=total_ms,
            first_token_ms=total_ms,
            output_chars=output_chars,
            cache_hit=cache_hit,
            error=error,
        )

    def record_detailed_stream(
        self,
        *,
        total_ms: float,
        first_token_ms: float,
        output_chars: int,
        cache_hit: bool,
        error: bool = False,
    ) -> None:
        self._detailed_stream.record(
            total_ms=total_ms,
            first_token_ms=first_token_ms,
            output_chars=output_chars,
            cache_hit=cache_hit,
            error=error,
        )

    def snapshot(self) -> dict[str, dict[str, float | int | None]]:
        return {
            "summary": self._summary.snapshot(),
            "detailed_stream": self._detailed_stream.snapshot(),
        }

    def reset(self) -> None:
        self._summary.reset()
        self._detailed_stream.reset()


class _MetricBucket:
    def __init__(self, sample_limit: int):
        self._sample_limit = sample_limit
        self._total_ms: deque[float] = deque(maxlen=sample_limit)
        self._first_token_ms: deque[float] = deque(maxlen=sample_limit)
        self._output_chars: deque[int] = deque(maxlen=sample_limit)
        self._requests = 0
        self._errors = 0
        self._cache_hits = 0

    def record(
        self,
        *,
        total_ms: float,
        first_token_ms: float,
        output_chars: int,
        cache_hit: bool,
        error: bool,
    ) -> None:
        self._requests += 1
        if error:
            self._errors += 1
        if cache_hit:
            self._cache_hits += 1
        self._total_ms.append(total_ms)
        self._first_token_ms.append(first_token_ms)
        self._output_chars.append(output_chars)

    def snapshot(self) -> dict[str, float | int | None]:
        return {
            "requests": self._requests,
            "errors": self._errors,
            "cache_hit_rate": round(self._cache_hits / self._requests, 4)
            if self._requests
            else 0.0,
            "p50_ms": _percentile(self._total_ms, 0.50, min_samples=1),
            "p95_ms": _percentile(
                self._total_ms,
                0.95,
                min_samples=PERCENTILE_MIN_SAMPLE_COUNT,
            ),
            "first_token_p50_ms": _percentile(
                self._first_token_ms,
                0.50,
                min_samples=1,
            ),
            "first_token_p95_ms": _percentile(
                self._first_token_ms,
                0.95,
                min_samples=PERCENTILE_MIN_SAMPLE_COUNT,
            ),
            "avg_chars": round(mean(self._output_chars), 2)
            if self._output_chars
            else 0.0,
            "p95_chars": _percentile(
                self._output_chars,
                0.95,
                min_samples=PERCENTILE_MIN_SAMPLE_COUNT,
            ),
        }

    def reset(self) -> None:
        self._total_ms.clear()
        self._first_token_ms.clear()
        self._output_chars.clear()
        self._requests = 0
        self._errors = 0
        self._cache_hits = 0


def _percentile(
    values: deque[float] | deque[int],
    ratio: float,
    *,
    min_samples: int,
) -> Optional[float]:
    if len(values) < min_samples:
        return None
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * ratio))
    return round(float(ordered[index]), 2)


__all__ = [
    "ExplanationMetricsTracker",
    "METRIC_SAMPLE_LIMIT",
    "PERCENTILE_MIN_SAMPLE_COUNT",
]
