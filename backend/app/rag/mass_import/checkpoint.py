"""断点续传支持。

基于 JSON 文件的检查点机制，支持从上次中断处恢复导入。
每个阶段完成后保存状态，每批次更新偏移量。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 默认检查点目录
DEFAULT_CHECKPOINT_DIR = Path("data/import_checkpoints")


class ImportCheckpoint:
    """文件级检查点，支持多阶段导入的断点续传。"""

    PHASES = ("parse", "dedup", "pg_import", "milvus")

    def __init__(self, run_id: str, checkpoint_dir: Path | str | None = None) -> None:
        self._dir = Path(checkpoint_dir or DEFAULT_CHECKPOINT_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / f"{run_id}.json"
        self._state = self._load()

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        """从磁盘加载检查点状态。"""
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                logger.info("Loaded checkpoint from %s (phase=%s, offset=%d)",
                            self._path.name, state.get("phase", "?"), state.get("offset", 0))
                return state
            except Exception as exc:
                logger.warning("Failed to load checkpoint %s: %s", self._path, exc)
        return self._default_state()

    def save(self) -> None:
        """持久化当前状态到磁盘。"""
        self._state["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error("Failed to save checkpoint: %s", exc)

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "phase": "parse",
            "offset": 0,
            "total": 0,
            "completed_phases": [],
            "stats": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # 阶段管理
    # ------------------------------------------------------------------

    @property
    def phase(self) -> str:
        return self._state.get("phase", "parse")

    @phase.setter
    def phase(self, value: str) -> None:
        self._state["phase"] = value
        self.save()

    @property
    def offset(self) -> int:
        return self._state.get("offset", 0)

    @offset.setter
    def offset(self, value: int) -> None:
        self._state["offset"] = value
        # 不在每次 offset 更新时保存，由外部调用 save()

    @property
    def total(self) -> int:
        return self._state.get("total", 0)

    @total.setter
    def total(self, value: int) -> None:
        self._state["total"] = value

    def is_phase_completed(self, phase: str) -> bool:
        """检查某阶段是否已完成。"""
        return phase in self._state.get("completed_phases", [])

    def mark_phase_completed(self, phase: str) -> None:
        """标记阶段完成。"""
        completed = self._state.setdefault("completed_phases", [])
        if phase not in completed:
            completed.append(phase)
        self.save()

    def should_skip_phase(self, phase: str) -> bool:
        """判断是否应跳过某阶段（已完成则跳过）。"""
        return self.is_phase_completed(phase)

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def update_stats(self, **kwargs: Any) -> None:
        """增量更新统计信息。"""
        stats = self._state.setdefault("stats", {})
        for key, value in kwargs.items():
            if isinstance(value, int) and isinstance(stats.get(key), int):
                stats[key] += value
            else:
                stats[key] = value
        self.save()

    @property
    def stats(self) -> dict[str, Any]:
        return dict(self._state.get("stats", {}))

    # ------------------------------------------------------------------
    # 中间文件路径
    # ------------------------------------------------------------------

    @property
    def parsed_articles_path(self) -> Path:
        """解析后的条文中间文件路径（JSONL 格式）。"""
        return self._dir / f"{self._state.get('run_id', 'import')}_parsed.jsonl"

    @property
    def deduped_articles_path(self) -> Path:
        """去重后的条文中间文件路径（JSONL 格式）。"""
        return self._dir / f"{self._state.get('run_id', 'import')}_deduped.jsonl"

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """重置检查点（重新开始）。"""
        self._state = self._default_state()
        self.save()

    def summary(self) -> str:
        """返回人类可读的状态摘要。"""
        s = self._state
        lines = [
            f"Run ID: {s.get('run_id', '?')}",
            f"Current phase: {s.get('phase', '?')}",
            f"Offset: {s.get('offset', 0):,} / {s.get('total', 0):,}",
            f"Completed phases: {', '.join(s.get('completed_phases', [])) or 'none'}",
            f"Last updated: {s.get('updated_at', '?')}",
        ]
        stats = s.get("stats", {})
        if stats:
            lines.append("Stats:")
            for k, v in stats.items():
                lines.append(f"  {k}: {v:,}" if isinstance(v, int) else f"  {k}: {v}")
        return "\n".join(lines)

    @staticmethod
    def generate_run_id() -> str:
        """生成基于时间戳的运行 ID。"""
        return datetime.now().strftime("import_%Y%m%d_%H%M%S")

    @staticmethod
    def list_checkpoints(checkpoint_dir: Path | str | None = None) -> list[dict[str, Any]]:
        """列出所有可用的检查点。"""
        d = Path(checkpoint_dir or DEFAULT_CHECKPOINT_DIR)
        if not d.exists():
            return []
        results = []
        for f in sorted(d.glob("import_*.json")):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    state = json.load(fp)
                state["_file"] = str(f)
                results.append(state)
            except Exception:
                continue
        return results
