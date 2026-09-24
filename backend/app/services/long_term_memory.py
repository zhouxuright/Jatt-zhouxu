"""跨会话长期记忆（P1-5）。

``Conversation.fact_sheet`` 只在单个会话内有效；用户开新会话时 AI 就"失忆"
了。本服务把每次会话沉淀的事实档案**合并**进用户级画像（``user_memory``），
并在新会话首轮自动注入，使 AI 记得：当事人身份、常涉业务、既有争议焦点。

设计取舍
--------
* **不做 LLM 二次提炼**：直接复用会话内已抽取的 ``LegalFactSheet``，确定性
  合并，零额外 token 与延迟，也避免"记忆幻觉"。
* **加密落盘**：``profile_json`` / ``profile_text`` 均为 ``EncryptedText``。
* **长度上限**：注入内容截断到 ``MAX_PROMPT_CHARS``，防止长期记忆挤占
  上下文预算（三层上下文管理器的滑动窗口 + 摘要预算不受影响）。
* **失败静默**：记忆是增强能力，任何异常都不影响主问答链路。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_memory import UserMemory

logger = logging.getLogger(__name__)

#: 注入到 system prompt 的记忆文本上限（字符）
MAX_PROMPT_CHARS = 900

#: 单个列表型字段最多保留的条目数（防止画像无限膨胀）
MAX_LIST_ITEMS = 20


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

async def get_memory(db: AsyncSession, user_id: str) -> UserMemory | None:
    """取用户的长期记忆记录（不存在则返回 None）。"""
    if not user_id:
        return None
    return (
        await db.execute(select(UserMemory).where(UserMemory.user_id == user_id))
    ).scalar_one_or_none()


async def load_memory_prompt(db: AsyncSession, user_id: str) -> str:
    """构造注入用文本；无记忆时返回空串（不产生任何 prompt 噪音）。"""
    try:
        memory = await get_memory(db, user_id)
    except Exception as exc:  # noqa: BLE001 - 记忆读取失败不影响主链路
        logger.warning("读取长期记忆失败: %s", exc)
        return ""
    if memory is None or not memory.profile_text:
        return ""
    text = memory.profile_text.strip()
    if not text:
        return ""
    if len(text) > MAX_PROMPT_CHARS:
        text = text[:MAX_PROMPT_CHARS] + "…"
    return text


# ---------------------------------------------------------------------------
# 写入 / 合并
# ---------------------------------------------------------------------------

def _merge_lists(old: Any, new: Any) -> list:
    """列表合并：保序去重，保留最新的 ``MAX_LIST_ITEMS`` 条。"""
    merged: list = []
    for source in (old, new):
        if not isinstance(source, list):
            continue
        for item in source:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) \
                if isinstance(item, (dict, list)) else str(item)
            if not any(
                (json.dumps(x, ensure_ascii=False, sort_keys=True, default=str)
                 if isinstance(x, (dict, list)) else str(x)) == key
                for x in merged
            ):
                merged.append(item)
    return merged[-MAX_LIST_ITEMS:]


def merge_fact_dicts(old: dict | None, new: dict | None) -> dict:
    """确定性合并两份事实档案（新值优先，列表做并集）。"""
    result: dict = dict(old or {})
    for key, value in (new or {}).items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            result[key] = _merge_lists(result.get(key), value)
        else:
            result[key] = value
    return result


async def update_memory_from_conversation(db: AsyncSession, conversation: Any) -> bool:
    """把某个会话的事实档案合并进用户长期记忆。

    Returns:
        是否发生了更新。
    """
    try:
        user_id = getattr(conversation, "user_id", None)
        fact = getattr(conversation, "fact_sheet", None)
        if not user_id or not fact:
            return False

        memory = await get_memory(db, user_id)
        if memory is None:
            memory = UserMemory(user_id=user_id, entry_count=0)
            db.add(memory)

        old_profile: dict = {}
        if memory.profile_json:
            try:
                old_profile = json.loads(memory.profile_json) or {}
            except (json.JSONDecodeError, TypeError):
                old_profile = {}

        merged = merge_fact_dicts(old_profile, fact)

        # 生成自然语言画像（复用会话内事实档案的渲染逻辑，保证口径一致）
        from app.services.context_manager import LegalFactSheet

        memory.profile_json = json.dumps(merged, ensure_ascii=False)
        memory.profile_text = LegalFactSheet.from_dict(merged).to_text()
        memory.entry_count = int(memory.entry_count or 0) + 1
        memory.last_conversation_id = getattr(conversation, "id", None)

        await db.flush()
        logger.info(
            "长期记忆已更新: user=%s entries=%s", str(user_id)[:8], memory.entry_count
        )
        return True
    except Exception as exc:  # noqa: BLE001 - 记忆写入失败不影响主链路
        logger.warning("更新长期记忆失败: %s", exc)
        return False


async def update_memory_by_conversation_id(db: AsyncSession, conversation_id: str) -> bool:
    """按会话 ID 更新长期记忆（供流式响应在独立会话中调用）。"""
    from app.models.conversation import Conversation

    conversation = (
        await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none()
    if conversation is None:
        return False
    return await update_memory_from_conversation(db, conversation)


def build_memory_system_block(profile_text: str) -> str:
    """把记忆文本包装成 system prompt 片段。"""
    if not profile_text:
        return ""
    return (
        "## 用户长期画像（跨会话记忆）\n"
        f"{profile_text}\n\n"
        "请保持与该画像一致；如与本次对话中的新陈述冲突，以用户最新陈述为准，"
        "并在需要时向用户确认。"
    )
