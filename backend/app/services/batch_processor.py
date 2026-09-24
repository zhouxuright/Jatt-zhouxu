"""Batch processing service for multiple documents.

Processes multiple files asynchronously with progress tracking,
supporting extract, summarize, and analyze modes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# TTL for batch results in Redis (24 hours)
BATCH_TTL_SECONDS = 86400

_SUMMARY_SYSTEM_PROMPT = (
    "你是法律文档摘要助手。请用不超过150字的中文概括给定文档的核心内容，"
    "直接输出摘要正文，不要任何前缀、标题或解释。"
)

_KEY_POINTS_SYSTEM_PROMPT = (
    "你是法律文档分析助手。请从给定文档中提取3至6条关键要点，每条一行，"
    "以「• 」开头，直接输出要点列表，不要任何其他解释。"
)

# Max characters of document text fed to the LLM per call
_LLM_INPUT_CHAR_LIMIT = 6000


class BatchProcessor:
    """Process multiple documents in batch.

    Features:
    - Accept multiple files (PDF, DOCX, TXT)
    - Process each file asynchronously
    - Track progress per file
    - Generate combined results
    - Support for different processing modes:
      - 'extract': Just extract text
      - 'summarize': Extract + summarize each document
      - 'analyze': Full analysis (extract + summarize + key points)
    """

    def __init__(self) -> None:
        self._redis = None
        self._memory_store: dict[str, dict[str, Any]] = {}
        self._use_redis = True

    async def _get_redis(self):
        """Get Redis connection (lazy loaded)."""
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
            await self._redis.ping()
            return self._redis
        except Exception as exc:
            logger.warning("Redis unavailable for batch processor, using memory store: %s", exc)
            self._use_redis = False
            return None

    def _batch_key(self, batch_id: str) -> str:
        return f"batch:{batch_id}"

    async def process_batch(
        self,
        files: list[tuple[str, bytes]],  # (filename, content)
        user_id: str,
        mode: str = "analyze",
    ) -> dict:
        """Process a batch of files.

        Returns:
            {
                'batch_id': str,
                'total_files': int,
                'status': 'processing',
                'message': str,
            }
        """
        batch_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Initialize file results
        file_results: list[dict[str, Any]] = []
        for filename, _content in files:
            file_results.append({
                "filename": filename,
                "status": "pending",
                "text": None,
                "summary": None,
                "key_points": None,
                "error": None,
                "processing_time_seconds": None,
                "document_id": None,
            })

        batch_data = {
            "batch_id": batch_id,
            "user_id": user_id,
            "status": "processing",
            "mode": mode,
            "total_files": len(files),
            "completed_files": 0,
            "failed_files": 0,
            "progress_percent": 0.0,
            "started_at": now,
            "completed_at": None,
            "results": file_results,
        }

        # Store initial state
        await self._save_batch(batch_id, batch_data)

        # Save files to disk for processing
        upload_dir = os.path.join(settings.UPLOAD_DIR, "batch", batch_id)
        os.makedirs(upload_dir, exist_ok=True)
        for i, (filename, content) in enumerate(files):
            safe_name = f"{i}_{filename}"
            file_path = os.path.join(upload_dir, safe_name)
            with open(file_path, "wb") as f:
                f.write(content)
            batch_data["results"][i]["_file_path"] = file_path

        # Save updated state with file paths
        await self._save_batch(batch_id, batch_data)

        # Launch background processing
        asyncio.create_task(self._process_files(batch_id))

        logger.info(
            "Batch %s created: %d files, mode=%s, user=%s",
            batch_id, len(files), mode, user_id[:8],
        )

        return {
            "batch_id": batch_id,
            "total_files": len(files),
            "status": "processing",
            "message": f"批量处理任务已提交，共 {len(files)} 个文件，正在后台处理。",
        }

    async def _process_files(self, batch_id: str) -> None:
        """Background task: process each file in the batch."""
        batch_data = await self.get_batch_raw(batch_id)
        if not batch_data:
            logger.error("Batch %s not found for processing", batch_id)
            return

        mode = batch_data.get("mode", "analyze")
        results = batch_data.get("results", [])
        total = len(results)

        # Process files sequentially to avoid overwhelming resources
        for i, file_result in enumerate(results):
            file_path = file_result.get("_file_path", "")
            filename = file_result.get("filename", "unknown")

            # Update status to processing
            file_result["status"] = "processing"
            batch_data["status"] = "processing"
            await self._save_batch(batch_id, batch_data)

            start_time = time.time()

            try:
                from app.rag.document_processor import DocumentProcessor

                processor = DocumentProcessor()
                parsed = await processor.parse_file(file_path)
                text = parsed.get("text", "")

                file_result["text"] = text[:10000] if text else ""  # Limit stored text
                file_result["status"] = "completed"

                # Generate summary for summarize and analyze modes
                if mode in ("summarize", "analyze") and text:
                    file_result["summary"] = await self._generate_summary(text)

                # Generate key points for analyze mode
                if mode == "analyze" and text:
                    file_result["key_points"] = await self._extract_key_points(text)

                elapsed = time.time() - start_time
                file_result["processing_time_seconds"] = round(elapsed, 2)

                batch_data["completed_files"] = batch_data.get("completed_files", 0) + 1

                logger.info("Batch %s file %d/%d completed: %s", batch_id, i + 1, total, filename)

            except Exception as exc:
                elapsed = time.time() - start_time
                file_result["status"] = "failed"
                file_result["error"] = str(exc)
                file_result["processing_time_seconds"] = round(elapsed, 2)

                batch_data["failed_files"] = batch_data.get("failed_files", 0) + 1

                logger.error("Batch %s file %d/%d failed: %s - %s", batch_id, i + 1, total, filename, exc)

            # Update progress
            processed = batch_data.get("completed_files", 0) + batch_data.get("failed_files", 0)
            batch_data["progress_percent"] = round((processed / total) * 100, 1) if total > 0 else 0
            await self._save_batch(batch_id, batch_data)

        # Clean up file paths from results before final save
        for fr in batch_data.get("results", []):
            fr.pop("_file_path", None)

        # Mark batch as completed
        batch_data["status"] = "completed"
        batch_data["completed_at"] = datetime.now(timezone.utc).isoformat()
        batch_data["progress_percent"] = 100.0
        await self._save_batch(batch_id, batch_data)

        logger.info("Batch %s completed: %d total, %d completed, %d failed",
                     batch_id, total, batch_data["completed_files"], batch_data["failed_files"])

    async def _generate_summary(self, text: str) -> str:
        """Generate a document summary via LLM, falling back to extraction."""
        text = text.strip()
        if not text:
            return ""

        try:
            from app.services.llm_service import get_raw_llm_service

            result = await get_raw_llm_service().chat_with_fallback(
                messages=[
                    {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": text[:_LLM_INPUT_CHAR_LIMIT]},
                ],
                temperature=0.3,
                max_tokens=400,
            )
            if result.get("provider") != "fallback":
                summary = (result.get("content") or "").strip()
                if summary:
                    return summary
        except Exception as exc:
            logger.warning("LLM summary failed, using extractive fallback: %s", exc)

        return self._extractive_summary(text)

    @staticmethod
    def _extractive_summary(text: str) -> str:
        """Fallback: extract the first few sentences as a summary."""
        if len(text) <= 500:
            return text

        # Try to cut at a sentence boundary
        summary = text[:500]
        last_period = max(summary.rfind("。"), summary.rfind("！"), summary.rfind("？"), summary.rfind("."))
        if last_period > 200:
            summary = summary[:last_period + 1]

        return summary + "..."

    async def _extract_key_points(self, text: str) -> str:
        """Extract key points via LLM, falling back to regex heuristics."""
        text = text.strip()
        if not text:
            return "无法提取关键要点"

        try:
            from app.services.llm_service import get_raw_llm_service

            result = await get_raw_llm_service().chat_with_fallback(
                messages=[
                    {"role": "system", "content": _KEY_POINTS_SYSTEM_PROMPT},
                    {"role": "user", "content": text[:_LLM_INPUT_CHAR_LIMIT]},
                ],
                temperature=0.3,
                max_tokens=500,
            )
            if result.get("provider") != "fallback":
                points = (result.get("content") or "").strip()
                if points:
                    return points
        except Exception as exc:
            logger.warning("LLM key-point extraction failed, using regex fallback: %s", exc)

        return self._regex_key_points(text)

    @staticmethod
    def _regex_key_points(text: str) -> str:
        """Fallback: extract numbered items and important-looking lines."""
        import re
        lines = text.split("\n")
        key_lines: list[str] = []

        # Look for numbered items or bullet points
        for line in lines[:100]:  # Only scan first 100 lines
            stripped = line.strip()
            if not stripped:
                continue
            # Match numbered items like "1.", "一、", "（一）"
            if re.match(r"^[\d]+[.、)]", stripped) or re.match(r"^[一二三四五六七八九十]+[、.]", stripped):
                key_lines.append(stripped)
                if len(key_lines) >= 10:
                    break

        if key_lines:
            return "\n".join(key_lines)

        # Fallback: extract first few non-empty lines
        for line in lines[:50]:
            stripped = line.strip()
            if stripped and len(stripped) > 10:
                key_lines.append(stripped)
                if len(key_lines) >= 5:
                    break

        return "\n".join(key_lines) if key_lines else "无法提取关键要点"

    async def _save_batch(self, batch_id: str, data: dict[str, Any]) -> None:
        """Save batch data to Redis or memory store."""
        redis = await self._get_redis()
        if redis:
            await redis.setex(
                self._batch_key(batch_id),
                BATCH_TTL_SECONDS,
                json.dumps(data, ensure_ascii=False),
            )
        else:
            self._memory_store[batch_id] = data

    async def get_batch_raw(self, batch_id: str) -> dict[str, Any] | None:
        """Get raw batch data."""
        redis = await self._get_redis()
        if redis:
            data = await redis.get(self._batch_key(batch_id))
            if data:
                return json.loads(data)
            return None
        else:
            return self._memory_store.get(batch_id)

    async def get_batch_status(self, batch_id: str) -> dict[str, Any] | None:
        """Get batch processing status."""
        data = await self.get_batch_raw(batch_id)
        if not data:
            return None

        # Return status without full text content
        results_summary = []
        for r in data.get("results", []):
            results_summary.append({
                "filename": r.get("filename"),
                "status": r.get("status"),
                "summary": r.get("summary"),
                "key_points": r.get("key_points"),
                "error": r.get("error"),
                "processing_time_seconds": r.get("processing_time_seconds"),
                "document_id": r.get("document_id"),
            })

        return {
            "batch_id": data["batch_id"],
            "status": data["status"],
            "total_files": data["total_files"],
            "completed_files": data.get("completed_files", 0),
            "failed_files": data.get("failed_files", 0),
            "progress_percent": data.get("progress_percent", 0.0),
            "mode": data.get("mode", "analyze"),
            "started_at": data["started_at"],
            "completed_at": data.get("completed_at"),
            "results": results_summary,
        }

    async def get_batch_results(self, batch_id: str) -> dict[str, Any] | None:
        """Get batch processing results with full content."""
        data = await self.get_batch_raw(batch_id)
        if not data:
            return None

        # Return results without internal file paths
        results = []
        for r in data.get("results", []):
            result = {
                "filename": r.get("filename"),
                "status": r.get("status"),
                "text": r.get("text"),
                "summary": r.get("summary"),
                "key_points": r.get("key_points"),
                "error": r.get("error"),
                "processing_time_seconds": r.get("processing_time_seconds"),
                "document_id": r.get("document_id"),
            }
            results.append(result)

        return {
            "batch_id": data["batch_id"],
            "status": data["status"],
            "total_files": data["total_files"],
            "completed_files": data.get("completed_files", 0),
            "failed_files": data.get("failed_files", 0),
            "progress_percent": data.get("progress_percent", 0.0),
            "mode": data.get("mode", "analyze"),
            "started_at": data["started_at"],
            "completed_at": data.get("completed_at"),
            "results": results,
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_batch_processor: BatchProcessor | None = None


def get_batch_processor() -> BatchProcessor:
    """Return a singleton BatchProcessor instance."""
    global _batch_processor
    if _batch_processor is None:
        _batch_processor = BatchProcessor()
    return _batch_processor
