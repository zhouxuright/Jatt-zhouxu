"""
Phase 2: Three-layer Context Manager for Multi-turn Conversations

Implements intelligent context window management:
1. Short-term memory: Recent N turns (sliding window)
2. Working memory: RAG-retrieved relevant context
3. Long-term memory: Conversation summary compression

Production enhancements for legal conversations:
- Incremental summarization with legal fact preservation
- Structured fact sheet extraction and maintenance
- Topic shift detection via embedding similarity
- Smart context budget management

This solves the context window problem for multi-turn legal conversations.
"""

import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.message import Message, MessageRole
from app.models.conversation import Conversation

logger = logging.getLogger(__name__)


# ============================================================================
# Legal Fact Sheet - Structured Fact Extraction
# ============================================================================

@dataclass
class LegalFactSheet:
    """
    Structured fact sheet maintained per conversation.

    Tracks key legal entities and relationships throughout the conversation,
    enabling precise context reconstruction and fact-based reasoning.
    """
    parties: list[str] = field(default_factory=list)           # 当事人
    legal_relationship: str = field(default="")                 # 法律关系
    dispute_focus: list[str] = field(default_factory=list)     # 争议焦点
    cited_laws: list[str] = field(default_factory=list)        # 已引用法条
    agreed_facts: list[str] = field(default_factory=list)      # 已确认事实
    pending_questions: list[str] = field(default_factory=list) # 未解决问题
    timeline: list[dict] = field(default_factory=list)         # 时间线事件

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "parties": self.parties,
            "legal_relationship": self.legal_relationship,
            "dispute_focus": self.dispute_focus,
            "cited_laws": self.cited_laws,
            "agreed_facts": self.agreed_facts,
            "pending_questions": self.pending_questions,
            "timeline": self.timeline,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LegalFactSheet":
        """Reconstruct from dictionary."""
        if not data:
            return cls()
        return cls(
            parties=data.get("parties", []),
            legal_relationship=data.get("legal_relationship", ""),
            dispute_focus=data.get("dispute_focus", []),
            cited_laws=data.get("cited_laws", []),
            agreed_facts=data.get("agreed_facts", []),
            pending_questions=data.get("pending_questions", []),
            timeline=data.get("timeline", []),
        )

    def to_text(self) -> str:
        """Render as compact text for context injection."""
        sections = []

        if self.parties:
            sections.append(f"当事人: {', '.join(self.parties)}")

        if self.legal_relationship:
            sections.append(f"法律关系: {self.legal_relationship}")

        if self.dispute_focus:
            sections.append(f"争议焦点: {'; '.join(self.dispute_focus)}")

        if self.cited_laws:
            sections.append(f"已引用法条: {', '.join(self.cited_laws)}")

        if self.agreed_facts:
            sections.append(f"已确认事实: {'; '.join(self.agreed_facts)}")

        if self.pending_questions:
            sections.append(f"未解决问题: {'; '.join(self.pending_questions)}")

        if self.timeline:
            timeline_strs = [
                f"{e.get('date', '')}: {e.get('event', '')}"
                for e in self.timeline[:10]  # Limit to 10 events
            ]
            sections.append(f"时间线: {'; '.join(timeline_strs)}")

        return "\n".join(sections) if sections else ""


class ContextManager:
    """
    Three-layer context manager for multi-turn conversations.

    Architecture:
    - Short-term: Recent 10 turns (sliding window)
    - Working: RAG-retrieved legal context (3000 tokens max)
    - Long-term: Compressed conversation summary (1000 tokens max)

    Total context budget: ~32K tokens (DeepSeek-chat supports 128K)
    """

    # Configuration
    MAX_RECENT_TURNS = 10          # Short-term: keep last 10 turns (20 messages)
    MAX_RAG_TOKENS = 3000          # Working: RAG context max tokens
    MAX_SUMMARY_TOKENS = 1000      # Long-term: summary max tokens
    MAX_FACT_SHEET_TOKENS = 500    # Structured fact sheet max tokens
    MAX_MESSAGE_TOKENS = 2000      # Per-message token limit
    COMPRESSION_THRESHOLD = 15     # Compress after 15 turns (30 messages)

    # Context budget management (in tokens)
    SYSTEM_PROMPT_BUDGET = 2000
    RAG_CONTEXT_BUDGET = 3000
    FACT_SHEET_BUDGET = 1000
    SUMMARY_BUDGET = 1000
    RECENT_TURNS_BUDGET = 20000
    OUTPUT_RESERVE = 6000
    TOTAL_BUDGET = SYSTEM_PROMPT_BUDGET + RAG_CONTEXT_BUDGET + FACT_SHEET_BUDGET + SUMMARY_BUDGET + RECENT_TURNS_BUDGET + OUTPUT_RESERVE  # 33K

    # Topic shift detection
    TOPIC_SHIFT_THRESHOLD = 0.35   # Cosine similarity below this = new topic
    MAX_TOPIC_SEGMENTS = 10        # Maximum topic segments to retain

    def __init__(self, db: AsyncSession):
        """
        Initialize context manager.

        Args:
            db: Async SQLAlchemy session
        """
        self.db = db

    async def build_context(
        self,
        conversation_id: str,
        current_message: str,
        rag_context: str = "",
    ) -> dict[str, Any]:
        """
        Build three-layer context for LLM with production enhancements.

        Architecture:
        - Short-term: Recent turns within token budget (variable, up to 20K tokens)
        - Working: RAG-retrieved legal context (3K tokens max) + Fact sheet (1K tokens max)
        - Long-term: Incremental summary (1K tokens) + Topic segments

        New features:
        1. Incremental summarization after COMPRESSION_THRESHOLD
        2. Structured fact sheet extraction and maintenance
        3. Topic shift detection via embedding similarity
        4. Smart context budget management

        Args:
            conversation_id: Conversation UUID
            current_message: Current user message
            rag_context: RAG-retrieved legal context

        Returns:
            Dict with keys: system, history, current, fact_sheet, metadata
        """
        # 1. Load conversation
        conversation = await self._get_conversation(conversation_id)
        if not conversation:
            raise ValueError(f"Conversation {conversation_id} not found")

        # 2. Get message count
        message_count = await self._get_message_count(conversation_id)

        # 3. Load and update fact sheet
        fact_sheet = await self._get_or_update_fact_sheet(
            conversation, current_message
        )

        # 4. Detect topic shift
        topic_shifted = await self._detect_topic_shift(
            conversation, current_message
        )

        # 5. Build short-term memory (recent turns) with budget awareness
        recent_messages = await self._get_recent_messages_with_budget(
            conversation_id,
            budget_tokens=self.RECENT_TURNS_BUDGET,
        )

        # 6. Build long-term memory (incremental summary if needed)
        summary = ""
        if message_count > self.COMPRESSION_THRESHOLD * 2:
            summary = await self._get_or_create_incremental_summary(
                conversation, recent_messages
            )

        # 7. Context budget management - allocate tokens smartly
        budget = self._calculate_budget(
            rag_context=rag_context,
            fact_sheet=fact_sheet,
            summary=summary,
            recent_messages=recent_messages,
        )

        # 8. Truncate RAG context if over budget
        if budget["rag_tokens"] > self.RAG_CONTEXT_BUDGET:
            rag_context = self._truncate_to_budget(
                rag_context, self.RAG_CONTEXT_BUDGET
            )

        # 9. Truncate recent messages if over budget
        recent_tokens = sum(
            self._estimate_tokens(msg["content"])
            for msg in recent_messages
        )
        if recent_tokens > budget["recent_budget"]:
            recent_messages = self._trim_messages_to_budget(
                recent_messages, budget["recent_budget"]
            )

        # 10. Assemble final context
        history_text = ""

        # Add fact sheet if non-empty
        fact_text = fact_sheet.to_text()
        if fact_text:
            history_text += f"[案件事实摘要]\n{fact_text}\n\n"

        # Add summary if available
        if summary:
            history_text += f"[历史摘要] {summary}\n\n"

        # Add topic segments if topic shifted
        if topic_shifted and conversation.topic_segments:
            segments = conversation.topic_segments
            if segments:
                recent_topics = segments[-3:]  # Last 3 topics
                topic_text = " | ".join(
                    f"[{s.get('topic', '')}]" for s in recent_topics
                )
                history_text += f"[历史话题] {topic_text}\n\n"

        # Add recent messages
        for msg in recent_messages:
            role_label = "用户" if msg["role"] == "user" else "助手"
            history_text += f"{role_label}: {msg['content']}\n"

        # 11. Build metadata
        final_recent_tokens = sum(
            self._estimate_tokens(msg["content"])
            for msg in recent_messages
        )
        metadata = {
            "message_count": message_count,
            "recent_turns": len(recent_messages) // 2,
            "summary_used": bool(summary),
            "fact_sheet_used": bool(fact_text),
            "topic_shift_detected": topic_shifted,
            "rag_tokens": self._estimate_tokens(rag_context),
            "fact_sheet_tokens": self._estimate_tokens(fact_text),
            "summary_tokens": self._estimate_tokens(summary),
            "recent_tokens": final_recent_tokens,
            "total_tokens": (
                self._estimate_tokens(rag_context)
                + self._estimate_tokens(fact_text)
                + self._estimate_tokens(summary)
                + final_recent_tokens
            ),
            "budget_total": self.TOTAL_BUDGET,
        }

        logger.info(
            f"Context built: conv={conversation_id[:8]}, "
            f"messages={message_count}, turns={metadata['recent_turns']}, "
            f"tokens={metadata['total_tokens']}/{self.TOTAL_BUDGET}, "
            f"topic_shift={topic_shifted}"
        )

        return {
            "system": "",  # System prompt assembled by caller
            "history": history_text,
            "current": current_message,
            "fact_sheet": fact_sheet,
            "metadata": metadata,
        }

    async def _get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Load conversation from database."""
        result = await self.db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def _get_message_count(self, conversation_id: str) -> int:
        """Get total message count in conversation."""
        from sqlalchemy import func
        result = await self.db.execute(
            select(func.count(Message.id)).where(
                Message.conversation_id == conversation_id
            )
        )
        return result.scalar() or 0

    async def _get_recent_messages(
        self,
        conversation_id: str,
        max_turns: int = 10,
    ) -> list[dict[str, str]]:
        """
        Load recent messages (sliding window).

        Args:
            conversation_id: Conversation UUID
            max_turns: Maximum number of turns (each turn = 2 messages)

        Returns:
            List of message dicts with 'role' and 'content'
        """
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(max_turns * 2)
        )
        messages = list(reversed(result.scalars().all()))

        history = []
        for msg in messages:
            # Truncate long messages
            content = msg.content
            if len(content) > self.MAX_MESSAGE_TOKENS * 2:
                content = content[:self.MAX_MESSAGE_TOKENS * 2] + "...[已截断]"

            history.append({
                "role": msg.role,
                "content": content,
            })

        return history

    async def _get_or_create_summary(self, conversation: Conversation) -> str:
        """
        Get existing summary or create new one (legacy method, kept for compatibility).

        Uses LLM to compress old messages into a summary.
        """
        # Check if summary exists
        if conversation.summary:
            return conversation.summary

        # Load all messages except recent ones
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .offset(self.MAX_RECENT_TURNS * 2)
            .limit(100)  # Limit to avoid memory issues
        )
        old_messages = list(reversed(result.scalars().all()))

        if not old_messages:
            return ""

        # Generate summary using LLM
        summary = await self._generate_summary(old_messages)

        # Stage the summary on the ORM object WITHOUT committing.
        #
        # Do NOT commit here. build_context() runs inside the caller's request
        # transaction, before the SSE generator has saved the assistant message.
        # Committing mid-request ends that transaction early and the assistant
        # message is then silently lost (observed: a turn's reply streamed to the
        # client but never persisted). The caller owns the transaction boundary
        # and its own commit will flush this attribute along with the messages.
        conversation.summary = summary

        logger.info(
            f"Generated summary for conv={conversation.id[:8]}, "
            f"compressed {len(old_messages)} messages (staged, not committed)"
        )

        return summary

    # ========================================================================
    # Feature 1: Incremental Summarization
    # ========================================================================

    async def _get_or_create_incremental_summary(
        self,
        conversation: Conversation,
        recent_messages: list[dict[str, str]],
    ) -> str:
        """
        Get existing summary or create an incremental one.

        After COMPRESSION_THRESHOLD, each new batch of messages is summarized
        incrementally -- the LLM receives the previous summary plus new messages
        and produces an updated summary that preserves all key legal facts.

        This avoids re-summarizing the entire conversation from scratch.
        """
        if conversation.summary:
            # Check if we need an incremental update (new messages since last summary)
            # We always do an incremental update when called, since build_context
            # only calls us when message_count > COMPRESSION_THRESHOLD * 2
            return await self._incremental_update(conversation, recent_messages)

        # First time: generate full summary from old messages
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .offset(self.MAX_RECENT_TURNS * 2)
            .limit(100)
        )
        old_messages = list(reversed(result.scalars().all()))

        if not old_messages:
            return ""

        summary = await self._generate_summary(old_messages)

        # Stage without commit (see note in _get_or_create_summary)
        conversation.summary = summary

        logger.info(
            f"Generated initial summary for conv={conversation.id[:8]}, "
            f"compressed {len(old_messages)} messages (staged, not committed)"
        )

        return summary

    async def _incremental_update(
        self,
        conversation: Conversation,
        recent_messages: list[dict[str, str]],
    ) -> str:
        """
        Perform incremental summarization: merge existing summary with recent messages.

        Preserves key legal facts, parties, dispute focus, and cited laws
        while integrating new conversation developments.
        """
        existing_summary = conversation.summary or ""

        # Take the last few recent messages for incremental context
        new_turns = recent_messages[-6:]  # Last 3 turns (6 messages)
        if not new_turns:
            return existing_summary

        new_text = "\n".join(
            f"{'用户' if m['role'] == 'user' else '助手'}: {m['content'][:300]}"
            for m in new_turns
        )

        prompt = f"""你是一个法律对话摘要助手。请对以下已有摘要和新增对话内容进行增量合并。

## 已有摘要
{existing_summary}

## 新增对话内容
{new_text}

## 合并要求
1. 在已有摘要基础上整合新增内容，消除重复
2. 必须保留以下关键信息：
   - 当事人信息及其身份
   - 法律关系的性质（合同、侵权、劳动等）
   - 争议焦点和核心问题
   - 已引用的具体法律条文编号
   - 双方已确认的事实
   - 尚未解决的问题
3. 摘要不超过500字
4. 保留关键法律术语和条文编号
5. 如新增内容引入了新的法律问题，应追加到摘要中

请输出合并后的摘要："""

        try:
            from app.services.llm_service import get_raw_llm_service
            llm_service = get_raw_llm_service()
            result = await llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=800,
            )
            new_summary = result.get("content", "").strip()

            if new_summary and len(new_summary) > 50:
                # Stage without commit
                conversation.summary = new_summary
                logger.info(
                    f"Incremental summary updated for conv={conversation.id[:8]} "
                    f"(staged, not committed)"
                )
                return new_summary
            else:
                return existing_summary

        except Exception as e:
            logger.error(f"Incremental summary failed: {e}")
            return existing_summary

    # ========================================================================
    # Feature 2: Structured Fact Extraction
    # ========================================================================

    async def _get_or_update_fact_sheet(
        self,
        conversation: Conversation,
        current_message: str,
    ) -> LegalFactSheet:
        """
        Load existing fact sheet or update it with new information.

        Uses LLM to extract structured legal facts from the latest messages
        and merges them into the running fact sheet.
        """
        # Load existing fact sheet from conversation
        fact_sheet = LegalFactSheet.from_dict(
            getattr(conversation, "fact_sheet", None)
        )

        # Only update fact sheet if conversation has enough content
        message_count = await self._get_message_count(conversation.id)
        if message_count < 2:
            return fact_sheet

        # Get the last few messages for fact extraction
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .limit(6)
        )
        latest_messages = list(reversed(result.scalars().all()))

        if not latest_messages:
            return fact_sheet

        # Extract facts using LLM
        updated_sheet = await self._extract_facts(
            fact_sheet, latest_messages, current_message
        )

        # Stage without commit
        conversation.fact_sheet = updated_sheet.to_dict()

        return updated_sheet

    async def _extract_facts(
        self,
        current_sheet: LegalFactSheet,
        messages: list[Message],
        current_message: str,
    ) -> LegalFactSheet:
        """
        Use LLM to extract structured facts from recent messages.

        Merges newly extracted facts with existing fact sheet, deduplicating
        and maintaining consistency.
        """
        conversation_text = "\n".join(
            f"{'用户' if m.role == 'user' else '助手'}: {m.content[:400]}"
            for m in messages
        )

        existing_text = current_sheet.to_text() or "(无已有事实)"

        prompt = f"""你是一个法律事实提取助手。请从对话中提取结构化的法律事实信息。

## 当前用户消息
{current_message}

## 最近对话
{conversation_text}

## 已有事实
{existing_text}

## 请提取以下信息（JSON格式输出）
请输出一个JSON对象，包含以下字段（如无法提取某项，保留空值）：
```json
{{
    "parties": ["当事人1", "当事人2"],
    "legal_relationship": "法律关系描述",
    "dispute_focus": ["争议焦点1", "争议焦点2"],
    "cited_laws": ["《法律名称》第X条"],
    "agreed_facts": ["已确认事实1"],
    "pending_questions": ["未解决问题1"],
    "timeline": [
        {{"date": "时间", "event": "事件描述"}}
    ]
}}
```

## 要求
1. parties: 提取所有提到的当事人（原告、被告、第三方等）
2. legal_relationship: 判断法律关系类型（合同纠纷、劳动争议、侵权责任等）
3. dispute_focus: 提取核心争议焦点
4. cited_laws: 提取对话中引用的具体法律条文
5. agreed_facts: 提取双方已确认的事实
6. pending_questions: 提取尚未解决的问题
7. timeline: 按时间顺序提取事件，格式为 {{"date": "...", "event": "..."}}
8. 与已有事实合并，去重并保持一致性
9. 仅输出JSON，不要其他内容

请输出JSON："""

        try:
            from app.services.llm_service import get_raw_llm_service
            import json as json_module

            llm_service = get_raw_llm_service()
            result = await llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=1000,
            )
            response_text = result.get("content", "").strip()

            # Parse JSON from response (handle markdown code blocks)
            json_str = response_text
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0].strip()

            extracted = json_module.loads(json_str)

            # Merge with existing fact sheet
            return self._merge_fact_sheet(current_sheet, extracted)

        except Exception as e:
            logger.warning(f"Fact extraction failed: {e}")
            return current_sheet

    def _merge_fact_sheet(
        self,
        existing: LegalFactSheet,
        new_data: dict[str, Any],
    ) -> LegalFactSheet:
        """
        Merge newly extracted facts with existing fact sheet.

        Deduplicates list fields and preserves existing values when
        new data is empty.
        """
        # Merge parties (deduplicated)
        parties = list(existing.parties)
        for p in new_data.get("parties", []):
            if p and p not in parties:
                parties.append(p)

        # Legal relationship: update if new is more specific
        legal_rel = existing.legal_relationship
        new_rel = new_data.get("legal_relationship", "")
        if new_rel and (not legal_rel or len(new_rel) > len(legal_rel)):
            legal_rel = new_rel

        # Dispute focus (deduplicated)
        dispute_focus = list(existing.dispute_focus)
        for d in new_data.get("dispute_focus", []):
            if d and d not in dispute_focus:
                dispute_focus.append(d)

        # Cited laws (deduplicated)
        cited_laws = list(existing.cited_laws)
        for law in new_data.get("cited_laws", []):
            if law and law not in cited_laws:
                cited_laws.append(law)

        # Agreed facts (deduplicated)
        agreed_facts = list(existing.agreed_facts)
        for f in new_data.get("agreed_facts", []):
            if f and f not in agreed_facts:
                agreed_facts.append(f)

        # Pending questions (deduplicated)
        pending = list(existing.pending_questions)
        for q in new_data.get("pending_questions", []):
            if q and q not in pending:
                pending.append(q)

        # Timeline (append new events, avoid duplicates by date+event)
        timeline = list(existing.timeline)
        existing_events = {
            (e.get("date", ""), e.get("event", ""))
            for e in timeline
        }
        for event in new_data.get("timeline", []):
            if isinstance(event, dict):
                key = (event.get("date", ""), event.get("event", ""))
                if key not in existing_events and key != ("", ""):
                    timeline.append(event)
                    existing_events.add(key)

        # Sort timeline by date
        timeline.sort(key=lambda e: e.get("date", ""))

        return LegalFactSheet(
            parties=parties,
            legal_relationship=legal_rel,
            dispute_focus=dispute_focus,
            cited_laws=cited_laws,
            agreed_facts=agreed_facts,
            pending_questions=pending,
            timeline=timeline,
        )

    # ========================================================================
    # Feature 3: Topic Shift Detection
    # ========================================================================

    async def _detect_topic_shift(
        self,
        conversation: Conversation,
        current_message: str,
    ) -> bool:
        """
        Detect if the current message represents a topic shift.

        Uses embedding similarity between the current message and a running
        summary of the conversation. If similarity < TOPIC_SHIFT_THRESHOLD,
        a new topic segment is started.

        Returns:
            True if a topic shift was detected
        """
        if not conversation.summary and not conversation.topic_segments:
            # First message or very early conversation -- no shift
            return False

        try:
            from app.services.model_registry import ModelRegistry

            # Get embedding for current message
            current_embedding = await ModelRegistry.embed_query(current_message)

            # Compute reference embedding from summary or last topic segment
            reference_text = conversation.summary or ""
            if conversation.topic_segments:
                # Use last topic segment summary as reference
                last_segment = conversation.topic_segments[-1]
                reference_text = last_segment.get("summary", reference_text)

            if not reference_text:
                return False

            reference_embedding = await ModelRegistry.embed_query(reference_text)

            # Calculate cosine similarity
            similarity = self._cosine_similarity(
                current_embedding, reference_embedding
            )

            logger.debug(
                f"Topic similarity: {similarity:.3f} "
                f"(threshold: {self.TOPIC_SHIFT_THRESHOLD})"
            )

            if similarity < self.TOPIC_SHIFT_THRESHOLD:
                # Topic shift detected -- record new segment
                await self._record_topic_segment(
                    conversation, current_message, similarity
                )
                return True

            return False

        except Exception as e:
            logger.warning(f"Topic shift detection failed: {e}")
            return False

    async def _record_topic_segment(
        self,
        conversation: Conversation,
        current_message: str,
        similarity: float,
    ) -> None:
        """
        Record a new topic segment when a topic shift is detected.

        Creates a short summary of the previous topic and stores it as a
        segment. Limits total segments to MAX_TOPIC_SEGMENTS.
        """
        segments = list(conversation.topic_segments or [])

        # Generate a brief topic label for the segment ending now
        topic_label = await self._generate_topic_label(conversation)

        new_segment = {
            "topic": topic_label,
            "summary": conversation.summary or "",
            "similarity_at_shift": round(similarity, 3),
            "message_count": await self._get_message_count(conversation.id),
        }

        segments.append(new_segment)

        # Keep only the most recent segments
        if len(segments) > self.MAX_TOPIC_SEGMENTS:
            segments = segments[-self.MAX_TOPIC_SEGMENTS:]

        # Stage without commit
        conversation.topic_segments = segments

        logger.info(
            f"Topic shift recorded for conv={conversation.id[:8]}: "
            f"'{topic_label}' (similarity={similarity:.3f}), "
            f"total segments: {len(segments)}"
        )

    async def _generate_topic_label(
        self, conversation: Conversation
    ) -> str:
        """Generate a short topic label for the current conversation state."""
        if not conversation.summary:
            return "一般法律咨询"

        # Use LLM to generate a short topic label
        prompt = f"""请为以下法律对话摘要生成一个简短的主题标签（不超过10个字）：

{conversation.summary[:300]}

请仅输出主题标签，不要其他内容。例如：合同纠纷、劳动争议、侵权责任、知识产权等。"""

        try:
            from app.services.llm_service import get_raw_llm_service
            llm_service = get_raw_llm_service()
            result = await llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=30,
            )
            label = result.get("content", "").strip()
            return label[:20] if label else "一般法律咨询"
        except Exception:
            return "一般法律咨询"

    def _cosine_similarity(
        self, vec_a: list[float], vec_b: list[float]
    ) -> float:
        """Calculate cosine similarity between two vectors."""
        a = np.array(vec_a, dtype=np.float32)
        b = np.array(vec_b, dtype=np.float32)

        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return float(np.dot(a, b) / (norm_a * norm_b))

    # ========================================================================
    # Feature 4: Context Budget Management
    # ========================================================================

    def _calculate_budget(
        self,
        rag_context: str,
        fact_sheet: LegalFactSheet,
        summary: str,
        recent_messages: list[dict[str, str]],
    ) -> dict[str, int]:
        """
        Calculate smart token allocation across context components.

        Priority order:
        1. System prompt: 2K (fixed)
        2. RAG context: 3K max
        3. Fact sheet: 1K max
        4. Summary: 1K max
        5. Recent turns: variable (up to 20K)
        6. Output reserve: 6K (fixed)

        If total exceeds budget, reduce recent turns first, then summary,
        then RAG context. Fact sheet is always preserved.
        """
        rag_tokens = self._estimate_tokens(rag_context)
        fact_tokens = self._estimate_tokens(fact_sheet.to_text())
        summary_tokens = self._estimate_tokens(summary)
        recent_tokens = sum(
            self._estimate_tokens(m["content"]) for m in recent_messages
        )

        # Fixed allocations
        fixed = self.SYSTEM_PROMPT_BUDGET + self.OUTPUT_RESERVE
        available = self.TOTAL_BUDGET - fixed

        # Cap each component
        rag_capped = min(rag_tokens, self.RAG_CONTEXT_BUDGET)
        fact_capped = min(fact_tokens, self.FACT_SHEET_BUDGET)
        summary_capped = min(summary_tokens, self.SUMMARY_BUDGET)

        # Remaining budget goes to recent messages
        remaining = available - rag_capped - fact_capped - summary_capped
        recent_budget = max(remaining, 2000)  # At least 2K for recent

        return {
            "rag_tokens": rag_capped,
            "fact_tokens": fact_capped,
            "summary_tokens": summary_capped,
            "recent_budget": recent_budget,
            "total_allocated": rag_capped + fact_capped + summary_capped + recent_budget + fixed,
        }

    def _truncate_to_budget(self, text: str, max_tokens: int) -> str:
        """Truncate text to fit within token budget."""
        estimated = self._estimate_tokens(text)
        if estimated <= max_tokens:
            return text

        # Calculate max characters (reverse of token estimation)
        max_chars = int(max_tokens * 1.5)
        return text[:max_chars] + "\n...[已截断以适应上下文预算]"

    def _trim_messages_to_budget(
        self,
        messages: list[dict[str, str]],
        budget_tokens: int,
    ) -> list[dict[str, str]]:
        """
        Trim message list to fit within token budget.

        Removes oldest messages first to stay within budget,
        always preserving at least the most recent turn.
        """
        if not messages:
            return messages

        # Start from most recent, add messages until budget exhausted
        selected = []
        total_tokens = 0

        for msg in reversed(messages):
            msg_tokens = self._estimate_tokens(msg["content"])
            if total_tokens + msg_tokens > budget_tokens and selected:
                break
            selected.insert(0, msg)
            total_tokens += msg_tokens

        return selected

    async def _get_recent_messages_with_budget(
        self,
        conversation_id: str,
        budget_tokens: int = 20000,
    ) -> list[dict[str, str]]:
        """
        Load recent messages within a token budget.

        Loads more messages than needed, then trims to fit the budget.
        """
        # Load generous number of messages, then trim
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(self.MAX_RECENT_TURNS * 4)  # Load 4x to have room to trim
        )
        messages = list(reversed(result.scalars().all()))

        history = []
        for msg in messages:
            content = msg.content
            if len(content) > self.MAX_MESSAGE_TOKENS * 2:
                content = content[:self.MAX_MESSAGE_TOKENS * 2] + "...[已截断]"
            history.append({
                "role": msg.role,
                "content": content,
            })

        # Trim to budget
        return self._trim_messages_to_budget(history, budget_tokens)

    async def _generate_summary(self, messages: list[Message]) -> str:
        """
        Generate conversation summary using LLM.

        Args:
            messages: List of old messages to summarize

        Returns:
            Compressed summary text
        """
        from app.services.llm_service import get_raw_llm_service

        # Format messages for summarization
        conversation_text = "\n".join(
            f"{'用户' if m.role == 'user' else '助手'}: {m.content[:500]}"
            for m in messages
        )

        # Build summarization prompt
        prompt = f"""请将以下法律对话历史压缩为简洁的摘要，保留关键信息：
- 用户咨询的核心法律问题
- 助手提供的主要法律建议
- 重要的法律条文引用
- 已达成的结论或建议

要求：
1. 摘要不超过300字
2. 保留关键法律术语和条文编号
3. 省略寒暄和重复内容
4. 使用客观第三人称叙述

对话历史：
{conversation_text}

请生成摘要："""

        try:
            llm_service = get_raw_llm_service()
            result = await llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500,
            )
            summary = result.get("content", "").strip()

            # Truncate if too long
            if len(summary) > self.MAX_SUMMARY_TOKENS * 2:
                summary = summary[:self.MAX_SUMMARY_TOKENS * 2] + "..."

            return summary

        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            # Fallback: simple extraction
            return self._fallback_summary(messages)

    def _fallback_summary(self, messages: list[Message]) -> str:
        """
        Fallback summary: extract first user question and last assistant response.
        """
        first_user = next(
            (m for m in messages if m.role == "user"),
            None
        )
        last_assistant = next(
            (m for m in reversed(messages) if m.role == "assistant"),
            None
        )

        parts = []
        if first_user:
            parts.append(f"用户咨询: {first_user.content[:200]}")
        if last_assistant:
            parts.append(f"助手回复: {last_assistant.content[:200]}")

        return " | ".join(parts) if parts else ""

    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for Chinese text.

        Rule of thumb: 1 token ≈ 1.5 Chinese characters
        """
        if not text:
            return 0
        return int(len(text) / 1.5)


# ============================================================================
# Integration with Chat API
# ============================================================================

async def build_chat_context(
    db: AsyncSession,
    conversation_id: str,
    current_message: str,
    rag_context: str = "",
    user_memory_text: str = "",
) -> list[dict[str, str]]:
    """
    Build context for chat API using ContextManager.

    This is the main integration point with the existing chat endpoint.
    Integrates all production features: fact sheet, incremental summary,
    topic segments, and budget-managed context assembly.

    Args:
        db: Database session
        conversation_id: Conversation UUID
        current_message: Current user message
        rag_context: RAG-retrieved legal context
        user_memory_text: 跨会话长期画像（P1-5），新会话首轮注入

    Returns:
        List of message dicts for LLM (system + history + current)
    """
    manager = ContextManager(db)
    context = await manager.build_context(
        conversation_id=conversation_id,
        current_message=current_message,
        rag_context=rag_context,
    )

    fact_sheet = context.get("fact_sheet")

    # Assemble messages for LLM
    messages = []

    # System message with RAG context and fact sheet
    system_parts = []

    if rag_context:
        system_parts.append(f"""你是一个专业的法律智能助手。请基于以下法律知识库内容回答用户问题。

## 相关法律条文
{rag_context}

## 要求
1. 明确引用具体法律条文（如《民法典》第X条）
2. 提供准确的法律分析
3. 必要时提示法律风险
4. 保持专业、客观的语气""")

    # Inject fact sheet into system prompt if available
    if fact_sheet:
        fact_text = fact_sheet.to_text()
        if fact_text:
            system_parts.append(f"""## 案件事实摘要
{fact_text}

请基于以上案件事实回答用户问题，确保与已确认事实保持一致。如有新的法律分析，请在回复中明确指出。""")

    # P1-5：注入跨会话长期画像（新会话首轮也能"记得"用户）
    if user_memory_text:
        from app.services.long_term_memory import build_memory_system_block

        block = build_memory_system_block(user_memory_text)
        if block:
            system_parts.append(block)

    if system_parts:
        messages.append({
            "role": "system",
            "content": "\n\n".join(system_parts),
        })

    # History (summary + topic segments + recent messages)
    if context["history"]:
        messages.append({
            "role": "system",
            "content": f"## 对话历史\n{context['history']}",
        })

    # Current message
    messages.append({"role": "user", "content": current_message})

    return messages


# ============================================================================
# Utility Functions
# ============================================================================

async def get_conversation_stats(
    db: AsyncSession,
    conversation_id: str,
) -> dict[str, Any]:
    """
    Get conversation statistics for debugging/monitoring.

    Returns:
        Dict with message_count, turn_count, estimated_tokens,
        fact_sheet info, and topic segment data.
    """
    manager = ContextManager(db)

    message_count = await manager._get_message_count(conversation_id)
    conversation = await manager._get_conversation(conversation_id)

    fact_sheet_data = getattr(conversation, "fact_sheet", None) if conversation else None
    fact_sheet = LegalFactSheet.from_dict(fact_sheet_data)
    topic_segments = getattr(conversation, "topic_segments", None) if conversation else None

    stats = {
        "conversation_id": conversation_id,
        "message_count": message_count,
        "turn_count": message_count // 2,
        "has_summary": bool(conversation.summary if conversation else False),
        "summary_length": len(conversation.summary) if conversation and conversation.summary else 0,
        "has_fact_sheet": bool(fact_sheet_data),
        "fact_sheet": {
            "parties_count": len(fact_sheet.parties),
            "dispute_focus_count": len(fact_sheet.dispute_focus),
            "cited_laws_count": len(fact_sheet.cited_laws),
            "agreed_facts_count": len(fact_sheet.agreed_facts),
            "pending_questions_count": len(fact_sheet.pending_questions),
            "timeline_events_count": len(fact_sheet.timeline),
        },
        "topic_segments_count": len(topic_segments) if topic_segments else 0,
        "context_budget_total": manager.TOTAL_BUDGET,
    }

    return stats
