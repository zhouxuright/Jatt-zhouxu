"""异步任务系统 — 基于 FastAPI BackgroundTasks + Redis 状态追踪。

把耗时的 LLM 任务（合同审查、文书生成）改为异步执行：
1. 用户提交请求 → 立即返回 task_id（202 Accepted）
2. 后台执行 LLM 任务，实时更新进度到 Redis
3. 用户轮询 GET /tasks/{task_id} 查看进度和结果
4. 完成后返回最终结果

不引入 Celery（避免额外依赖），使用 FastAPI 原生 BackgroundTasks。
Redis 用于存储任务状态和结果（TTL 24 小时自动过期）。

任务状态流转：
    pending → running → completed
                     → failed
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# TTL for task results in Redis (24 hours)
TASK_TTL_SECONDS = 86400


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AsyncTaskManager:
    """管理异步任务的生命周期。

    使用 Redis 存储任务状态和结果。
    如果 Redis 不可用，回退到内存存储（单进程模式下可用）。
    """

    def __init__(self) -> None:
        self._redis = None
        self._memory_store: dict[str, dict[str, Any]] = {}
        self._use_redis = True

    async def _get_redis(self):
        """获取 Redis 连接（懒加载）。"""
        if self._redis is not None:
            return self._redis

        if not self._use_redis:
            return None

        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                max_connections=5,
            )
            # 测试连接
            await self._redis.ping()
            return self._redis
        except Exception as exc:
            logger.warning("Redis unavailable for task manager, using memory store: %s", exc)
            self._use_redis = False
            return None

    def _task_key(self, task_id: str) -> str:
        return f"task:{task_id}"

    async def create_task(
        self,
        task_type: str,
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """创建新任务，返回 task_id。"""
        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        task_data = {
            "task_id": task_id,
            "task_type": task_type,
            "user_id": user_id,
            "status": TaskStatus.PENDING,
            "progress": 0,
            "progress_message": "任务已创建，等待处理...",
            "result": None,
            "error": None,
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
        }

        redis = await self._get_redis()
        if redis:
            await redis.setex(
                self._task_key(task_id),
                TASK_TTL_SECONDS,
                json.dumps(task_data, ensure_ascii=False),
            )
        else:
            self._memory_store[task_id] = task_data

        logger.info("Task created: %s (type=%s, user=%s)", task_id, task_type, user_id[:8])
        return task_id

    async def update_task(
        self,
        task_id: str,
        status: TaskStatus | None = None,
        progress: int | None = None,
        progress_message: str | None = None,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        """更新任务状态。"""
        task_data = await self.get_task_raw(task_id)
        if not task_data:
            logger.warning("Task not found for update: %s", task_id)
            return

        now = datetime.now(timezone.utc).isoformat()
        task_data["updated_at"] = now

        if status is not None:
            task_data["status"] = status
            if status == TaskStatus.RUNNING:
                task_data["started_at"] = now
            elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                task_data["completed_at"] = now

        if progress is not None:
            task_data["progress"] = min(100, max(0, progress))

        if progress_message is not None:
            task_data["progress_message"] = progress_message

        if result is not None:
            task_data["result"] = result

        if error is not None:
            task_data["error"] = error

        redis = await self._get_redis()
        if redis:
            await redis.setex(
                self._task_key(task_id),
                TASK_TTL_SECONDS,
                json.dumps(task_data, ensure_ascii=False),
            )
        else:
            self._memory_store[task_id] = task_data

    async def get_task_raw(self, task_id: str) -> dict[str, Any] | None:
        """获取任务原始数据。"""
        redis = await self._get_redis()
        if redis:
            data = await redis.get(self._task_key(task_id))
            if data:
                return json.loads(data)
            return None
        else:
            return self._memory_store.get(task_id)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """获取任务状态（公开接口）。"""
        return await self.get_task_raw(task_id)

    async def list_tasks(
        self,
        user_id: str,
        task_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """列出用户的任务。

        注意：Redis 不支持高效的按前缀列表查询，
        这里用 SCAN 命令遍历。生产环境下如果任务量大，
        应该用 PostgreSQL 存储任务记录。
        """
        tasks: list[dict[str, Any]] = []

        redis = await self._get_redis()
        if redis:
            cursor = 0
            while True:
                cursor, keys = await redis.scan(
                    cursor, match="task:*", count=100,
                )
                for key in keys:
                    data = await redis.get(key)
                    if data:
                        task = json.loads(data)
                        if task.get("user_id") == user_id:
                            if task_type is None or task.get("task_type") == task_type:
                                tasks.append(task)
                if cursor == 0 or len(tasks) >= limit * 2:
                    break
        else:
            for task in self._memory_store.values():
                if task.get("user_id") == user_id:
                    if task_type is None or task.get("task_type") == task_type:
                        tasks.append(task)

        # 按创建时间倒序
        tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        return tasks[:limit]


# ---------------------------------------------------------------------------
# Background task executors
# ---------------------------------------------------------------------------

async def _persist_contract_review(
    task_id: str,
    review_result: dict[str, Any],
    risk_items: list[dict[str, Any]],
    report: str,
) -> str | None:
    """把异步审查结果写入 contract_reviews，返回 review_id。

    任务数据（user_id / document_id / original_filename）从 Redis 任务记录里取，
    无需再次查询 users 表。落库失败只记日志、不影响任务本身的完成状态。
    """
    from app.core.database import async_session_factory
    from app.models.document import ContractReview

    task_manager = get_task_manager()
    task = await task_manager.get_task_raw(task_id) or {}
    user_id = task.get("user_id")
    if not user_id:
        return None
    meta = task.get("metadata") or {}

    review_id = str(uuid.uuid4())
    try:
        async with async_session_factory() as session:
            session.add(
                ContractReview(
                    id=review_id,
                    user_id=user_id,
                    document_id=meta.get("document_id"),
                    original_filename=meta.get("original_filename") or "",
                    risk_score=float(review_result.get("risk_score") or 0),
                    risk_items=json.dumps(risk_items, ensure_ascii=False),
                    summary=review_result.get("summary"),
                    full_analysis=report or None,
                )
            )
            await session.commit()
        return review_id
    except Exception:
        logger.exception("Failed to persist contract review for task %s", task_id)
        return None


async def execute_contract_review(task_id: str, contract_text: str, review_type: str) -> None:
    """后台执行合同审查任务。"""
    from app.agents.contract_review_agent import create_contract_review_agent

    task_manager = get_task_manager()

    try:
        await task_manager.update_task(
            task_id,
            status=TaskStatus.RUNNING,
            progress=10,
            progress_message="正在解析合同文本...",
        )

        agent = create_contract_review_agent()

        await task_manager.update_task(
            task_id,
            progress=30,
            progress_message="正在调用 AI 进行合同审查...",
        )

        result = await agent.run({
            "contract_text": contract_text,
            "review_focus": review_type,
        })

        await task_manager.update_task(
            task_id,
            progress=90,
            progress_message="正在生成审查报告...",
        )

        # Build structured result
        risk_score = result.get("risk_score", 0)
        risk_level = result.get("risk_level", "low")
        risk_items = result.get("risk_items", [])
        report = result.get("report", "")
        missing_clauses = result.get("missing_clauses", [])

        review_result = {
            "risk_score": risk_score,
            "risk_level": risk_level,
            "risk_items": risk_items,
            "report": report,
            "missing_clauses": missing_clauses,
            "summary": (
                report[:1000] if report else
                f"合同审查完成。综合风险等级：{risk_level}，"
                f"风险评分：{risk_score}/100，"
                f"识别风险项：{len(risk_items)}条，"
                f"缺失条款：{len(missing_clauses)}项。"
            ),
        }

        # 落库：异步接口此前只把结果写到 Redis，不写 contract_reviews，
        # 导致走异步的审查在「审查历史」里查不到（同步接口才会落库）。
        # 这里补齐，保持两条路径的历史一致。
        persisted_id = await _persist_contract_review(task_id, review_result, risk_items, report)
        if persisted_id:
            review_result["review_id"] = persisted_id

        await task_manager.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            progress=100,
            progress_message="审查完成",
            result=review_result,
        )

        logger.info("Contract review task completed: %s", task_id)

    except Exception as exc:
        logger.exception("Contract review task failed: %s", task_id)
        await task_manager.update_task(
            task_id,
            status=TaskStatus.FAILED,
            error=str(exc),
            progress_message=f"审查失败: {str(exc)[:100]}",
        )


async def execute_document_generation(
    task_id: str,
    template_name: str,
    description: str,
) -> None:
    """后台执行文书生成任务。"""
    from app.agents.document_gen_agent import create_document_gen_agent

    task_manager = get_task_manager()

    try:
        await task_manager.update_task(
            task_id,
            status=TaskStatus.RUNNING,
            progress=10,
            progress_message="正在准备文书模板...",
        )

        agent = create_document_gen_agent()

        await task_manager.update_task(
            task_id,
            progress=30,
            progress_message="正在提取法律要素...",
        )

        result = await agent.run({
            "document_type": template_name,
            "description": description,
        })

        await task_manager.update_task(
            task_id,
            progress=80,
            progress_message="正在格式化文书...",
        )

        generated_content = result.get("generated_document", "")
        final_output = result.get("final_output", "")
        missing_fields = result.get("missing_fields", [])

        if not generated_content:
            generated_content = final_output or f"[文档生成失败] 未能生成 {template_name}。"

        doc_result = {
            "content": generated_content,
            "template_name": template_name,
            "missing_fields": missing_fields,
        }

        await task_manager.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            progress=100,
            progress_message="文书生成完成",
            result=doc_result,
        )

        logger.info("Document generation task completed: %s", task_id)

    except Exception as exc:
        logger.exception("Document generation task failed: %s", task_id)
        await task_manager.update_task(
            task_id,
            status=TaskStatus.FAILED,
            error=str(exc),
            progress_message=f"生成失败: {str(exc)[:100]}",
        )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_task_manager: AsyncTaskManager | None = None


def get_task_manager() -> AsyncTaskManager:
    global _task_manager
    if _task_manager is None:
        _task_manager = AsyncTaskManager()
    return _task_manager
