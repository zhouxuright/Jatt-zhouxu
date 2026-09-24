"""进度追踪与 ETA 估算。"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class ImportProgress:
    """实时进度追踪，带 ETA 估算。"""

    def __init__(self, total: int, task_name: str, log_every: int = 1000) -> None:
        self._total = max(total, 1)
        self._task_name = task_name
        self._log_every = log_every
        self._start = time.time()
        self._processed = 0
        self._last_log_time = self._start
        self._last_log_count = 0

    def update(self, count: int = 1) -> None:
        """更新进度。"""
        self._processed += count

        # 每 log_every 条或每 1% 或每 30 秒记录一次
        elapsed_since_log = time.time() - self._last_log_time
        pct = self._processed / self._total * 100
        should_log = (
            self._processed - self._last_log_count >= self._log_every
            or pct >= (self._last_log_count / self._total * 100 + 1)
            or elapsed_since_log >= 30
            or self._processed >= self._total
        )

        if should_log:
            self._log()

    def _log(self) -> None:
        """输出进度日志。"""
        elapsed = time.time() - self._start
        rate = self._processed / max(elapsed, 0.001)
        remaining = (self._total - self._processed) / max(rate, 0.001)
        pct = self._processed / self._total * 100

        logger.info(
            "[%s] %s / %s (%.1f%%) | Rate: %.0f/sec | Elapsed: %s | ETA: %s",
            self._task_name,
            f"{self._processed:,}",
            f"{self._total:,}",
            pct,
            rate,
            self._format_duration(elapsed),
            self._format_duration(remaining),
        )

        self._last_log_time = time.time()
        self._last_log_count = self._processed

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """格式化时长为 HH:MM:SS。"""
        if seconds < 0:
            return "N/A"
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @property
    def processed(self) -> int:
        return self._processed

    @property
    def total(self) -> int:
        return self._total

    @property
    def elapsed(self) -> float:
        return time.time() - self._start

    def finish(self) -> dict[str, Any]:
        """标记完成并返回统计信息。"""
        elapsed = time.time() - self._start
        rate = self._processed / max(elapsed, 0.001)
        stats = {
            "task": self._task_name,
            "total": self._processed,
            "elapsed_seconds": round(elapsed, 1),
            "rate_per_second": round(rate, 1),
        }
        logger.info(
            "[%s] Completed: %s items in %s (%.0f/sec)",
            self._task_name,
            f"{self._processed:,}",
            self._format_duration(elapsed),
            rate,
        )
        return stats
