"""异步任务状态查询 API。

提供任务进度查询和列表接口。
合同审查和文书生成的异步 POST 端点分别在 contract.py 和 document.py 中。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user
from app.models.user import User
from app.services.task_manager import get_task_manager

router = APIRouter()


@router.get("/{task_id}")
async def get_task_status(
    task_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """查询异步任务的状态和结果。"""
    task_mgr = get_task_manager()
    task = await task_mgr.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")

    if task.get("user_id") != current_user.id:
        raise HTTPException(status_code=403, detail="无权访问此任务")

    return {
        "task_id": task["task_id"],
        "task_type": task["task_type"],
        "status": task["status"],
        "progress": task["progress"],
        "progress_message": task["progress_message"],
        "result": task.get("result"),
        "error": task.get("error"),
        "created_at": task["created_at"],
        "started_at": task.get("started_at"),
        "completed_at": task.get("completed_at"),
    }


@router.get("")
async def list_tasks(
    current_user: Annotated[User, Depends(get_current_user)],
    task_type: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """列出当前用户的异步任务。"""
    task_mgr = get_task_manager()
    tasks = await task_mgr.list_tasks(
        user_id=current_user.id,
        task_type=task_type,
        limit=limit,
    )

    summaries = []
    for t in tasks:
        summaries.append({
            "task_id": t["task_id"],
            "task_type": t["task_type"],
            "status": t["status"],
            "progress": t["progress"],
            "progress_message": t["progress_message"],
            "created_at": t["created_at"],
            "completed_at": t.get("completed_at"),
        })

    return {
        "tasks": summaries,
        "total": len(summaries),
    }
