"""
Model Fine-tuning Pipeline — Utilities for fine-tuning LLMs on legal data.

Provides:
1. Training data preparation from legal documents
2. LoRA/QLoRA fine-tuning configuration
3. Evaluation benchmarks for legal tasks
4. Export utilities for fine-tuned models

This module provides the scaffolding and data preparation pipeline.
Actual training requires GPU resources and is typically run as a
separate training job.
"""
from __future__ import annotations

import json
import logging
import os
import random
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class FineTuningPipeline:
    """Pipeline for preparing legal domain training data and managing fine-tuning."""

    # Supported base models for fine-tuning
    SUPPORTED_BASE_MODELS = {
        "qwen2.5-7b": {"name": "Qwen2.5-7B", "params": "7B", "framework": "transformers"},
        "qwen2.5-14b": {"name": "Qwen2.5-14B", "params": "14B", "framework": "transformers"},
        "deepseek-7b": {"name": "DeepSeek-7B", "params": "7B", "framework": "transformers"},
        "chatglm3-6b": {"name": "ChatGLM3-6B", "params": "6B", "framework": "transformers"},
        "baichuan2-7b": {"name": "Baichuan2-7B", "params": "7B", "framework": "transformers"},
    }

    # Legal task types for training data
    LEGAL_TASKS = {
        "legal_qa": "法律问答 — 根据用户问题生成专业法律回答",
        "contract_review": "合同审查 — 分析合同条款、识别风险、提供修改建议",
        "document_generation": "文书生成 — 起草起诉状、答辩状、律师函等",
        "law_retrieval": "法条检索 — 根据问题检索相关法律条文",
        "case_analysis": "案例分析 — 分析案件事实、法律关系、裁判要点",
        "legal_reasoning": "法律推理 — IRAC框架结构化推理",
    }

    async def prepare_training_data(
        self,
        task_type: str = "legal_qa",
        source: str = "database",
        output_dir: str = "data/training",
        max_samples: int = 10000,
    ) -> dict[str, Any]:
        """Prepare training data from legal knowledge base.

        Generates instruction-following format data suitable for
        LoRA fine-tuning of Chinese LLMs.

        Args:
            task_type: Type of legal task to generate data for.
            source: Data source ("database", "files", "synthetic").
            output_dir: Output directory for training data files.
            max_samples: Maximum number of training samples.

        Returns:
            Statistics about the generated dataset.
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        samples = []

        if source == "database":
            samples = await self._prepare_from_database(task_type, max_samples)
        elif source == "synthetic":
            samples = await self._generate_synthetic_data(task_type, max_samples)

        if not samples:
            return {"success": False, "error": "No training data generated"}

        # Split into train/val/test
        random.shuffle(samples)
        train_split = int(len(samples) * 0.8)
        val_split = int(len(samples) * 0.1)

        train_data = samples[:train_split]
        val_data = samples[train_split:train_split + val_split]
        test_data = samples[train_split + val_split:]

        # Write files
        for split_name, split_data in [("train", train_data), ("val", val_data), ("test", test_data)]:
            filepath = output_path / f"{task_type}_{split_split_name}.jsonl"
            with open(filepath, "w", encoding="utf-8") as f:
                for sample in split_data:
                    f.write(json.dumps(sample, ensure_ascii=False) + "\n")

        return {
            "success": True,
            "task_type": task_type,
            "total_samples": len(samples),
            "train_samples": len(train_data),
            "val_samples": len(val_data),
            "test_samples": len(test_data),
            "output_dir": str(output_path),
            "files": [
                f"{task_type}_train.jsonl",
                f"{task_type}_val.jsonl",
                f"{task_type}_test.jsonl",
            ],
        }

    async def _prepare_from_database(
        self, task_type: str, max_samples: int
    ) -> list[dict[str, Any]]:
        """Prepare training data from the legal database."""
        samples = []

        try:
            from app.core.database import async_session_factory
            from sqlalchemy import text as sql_text

            async with async_session_factory() as session:
                if task_type == "legal_qa":
                    # Generate Q&A pairs from legal articles
                    result = await session.execute(
                        sql_text("""
                            SELECT la.content, la.article_number, l.name
                            FROM legal_articles la
                            JOIN laws l ON la.law_id = l.id
                            WHERE la.content IS NOT NULL AND LENGTH(la.content) > 20
                            ORDER BY RANDOM()
                            LIMIT :limit
                        """),
                        {"limit": max_samples},
                    )
                    rows = result.fetchall()

                    for row in rows:
                        article_content = row[0]
                        article_number = row[1]
                        law_name = row[2]

                        # Generate instruction
                        instruction = f"请解释《{law_name}》{article_number}的含义和适用场景。"
                        response = f"根据《{law_name}》{article_number}规定：\n\n{article_content}\n\n【释义】该条文主要规定了..."

                        samples.append({
                            "instruction": instruction,
                            "input": "",
                            "output": response,
                            "task_type": task_type,
                            "source": "database",
                        })

                elif task_type == "law_retrieval":
                    # Generate retrieval training pairs
                    result = await session.execute(
                        sql_text("""
                            SELECT la.content, la.article_number, l.name, la.tags
                            FROM legal_articles la
                            JOIN laws l ON la.law_id = l.id
                            WHERE la.content IS NOT NULL AND LENGTH(la.content) > 20
                            ORDER BY RANDOM()
                            LIMIT :limit
                        """),
                        {"limit": max_samples},
                    )
                    rows = result.fetchall()

                    for row in rows:
                        content, number, law_name, tags = row

                        instruction = f"请检索与以下问题相关的法律条文：{content[:100]}..."
                        response = f"根据您的问题，相关法条如下：\n\n《{law_name}》{number}：{content}"

                        samples.append({
                            "instruction": instruction,
                            "input": "",
                            "output": response,
                            "task_type": task_type,
                            "source": "database",
                        })

                elif task_type == "case_analysis":
                    # Generate from court cases
                    result = await session.execute(
                        sql_text("""
                            SELECT title, summary, cause_of_action, judgment_result
                            FROM court_cases
                            WHERE summary IS NOT NULL AND LENGTH(summary) > 50
                            ORDER BY RANDOM()
                            LIMIT :limit
                        """),
                        {"limit": max_samples},
                    )
                    rows = result.fetchall()

                    for row in rows:
                        title, summary, cause, judgment = row

                        instruction = f"请分析以下案件：\n案由：{cause}\n案情：{summary[:500]}"
                        response = f"## 案件分析\n\n**案件名称**：{title}\n**案由**：{cause}\n\n**案情概述**：{summary[:300]}\n\n**裁判结果**：{judgment or '未提供'}"

                        samples.append({
                            "instruction": instruction,
                            "input": "",
                            "output": response,
                            "task_type": task_type,
                            "source": "database",
                        })

        except Exception as exc:
            logger.error("Database training data preparation failed: %s", exc)

        return samples

    async def _generate_synthetic_data(
        self, task_type: str, max_samples: int
    ) -> list[dict[str, Any]]:
        """Generate synthetic training data using the LLM."""
        samples = []

        from app.services.llm_service import get_raw_llm_service
        llm = get_raw_llm_service()

        # Seed questions for different legal domains
        seed_questions = {
            "legal_qa": [
                "劳动合同到期不续签，公司需要赔偿吗？",
                "离婚时房产如何分割？",
                "交通事故赔偿标准是什么？",
                "借款利息最高可以约定多少？",
                "公司辞退员工需要支付什么补偿？",
            ],
            "contract_review": [
                "请审查以下租赁合同条款的合法性",
                "这份竞业限制协议是否有效？",
            ],
        }

        questions = seed_questions.get(task_type, seed_questions["legal_qa"])

        for i, question in enumerate(questions):
            if i >= max_samples:
                break

            try:
                result = await llm.chat(
                    messages=[
                        {"role": "system", "content": f"你是一位专业的法律AI训练数据生成专家。请生成高质量的{task_type}训练数据。"},
                        {"role": "user", "content": f"请针对以下问题生成一个详细的、引用法条的专业回答：\n\n{question}"},
                    ],
                    temperature=0.7,
                    max_tokens=2000,
                )

                answer = result.get("content", "")
                if answer:
                    samples.append({
                        "instruction": question,
                        "input": "",
                        "output": answer,
                        "task_type": task_type,
                        "source": "synthetic",
                    })
            except Exception as exc:
                logger.warning("Synthetic data generation failed for question %d: %s", i, exc)

        return samples

    def get_finetuning_config(
        self,
        base_model: str = "qwen2.5-7b",
        task_type: str = "legal_qa",
    ) -> dict[str, Any]:
        """Generate a LoRA fine-tuning configuration.

        Returns a config dict that can be used with:
        - LLaMA-Factory
        - Swift (ModelScope)
        - Transformers + PEFT
        """
        model_info = self.SUPPORTED_BASE_MODELS.get(base_model)
        if not model_info:
            return {"error": f"Unsupported base model: {base_model}"}

        config = {
            "model_name_or_path": model_info["name"],
            "task_type": task_type,
            "finetuning_type": "lora",
            "lora_rank": 64,
            "lora_alpha": 128,
            "lora_dropout": 0.05,
            "lora_target": "all",
            "quantization_bit": 4,
            "quantization_method": "bnb",
            "template": "qwen",
            "dataset": f"data/training/{task_type}_train.jsonl",
            "eval_dataset": f"data/training/{task_type}_val.jsonl",
            "output_dir": f"output/{task_type}_{base_model}",
            "per_device_train_batch_size": 2,
            "gradient_accumulation_steps": 8,
            "learning_rate": 2e-4,
            "num_train_epochs": 3,
            "lr_scheduler_type": "cosine",
            "warmup_ratio": 0.1,
            "fp16": True,
            "logging_steps": 10,
            "save_steps": 500,
            "eval_steps": 500,
            "save_total_limit": 3,
            "overwrite_output_dir": True,
            "report_to": "tensorboard",
        }

        return {
            "config": config,
            "estimated_gpu_memory": "24GB+ (for 7B model with QLoRA)",
            "estimated_training_time": "~4-8 hours on A100 for 10K samples",
            "recommended_hardware": "NVIDIA A100 40GB or RTX 4090 24GB",
            "framework": model_info["framework"],
            "instructions": [
                "1. Install dependencies: pip install transformers peft bitsandbytes datasets",
                f"2. Prepare data: python -m app.services.finetuning prepare --task {task_type}",
                f"3. Start training: Use LLaMA-Factory or Swift with the config above",
                f"4. Evaluate: python -m app.services.finetuning evaluate --model output/{task_type}_{base_model}",
            ],
        }

    def get_evaluation_benchmark(self) -> dict[str, Any]:
        """Return the legal evaluation benchmark specification."""
        return {
            "name": "LegalBench-CN",
            "description": "中文法律AI评测基准",
            "tasks": [
                {
                    "name": "法条检索",
                    "description": "给定法律问题，检索最相关的法律条文",
                    "metric": "accuracy@5",
                    "samples": 500,
                },
                {
                    "name": "法律咨询",
                    "description": "回答专业法律问题",
                    "metric": "ROUGE-L + 人工评分",
                    "samples": 200,
                },
                {
                    "name": "合同审查",
                    "description": "识别合同条款中的风险",
                    "metric": "precision/recall/F1",
                    "samples": 300,
                },
                {
                    "name": "文书生成",
                    "description": "生成格式规范的法律文书",
                    "metric": "格式合规率 + 人工评分",
                    "samples": 100,
                },
                {
                    "name": "案例分析",
                    "description": "分析案件事实和法律关系",
                    "metric": "关键要素召回率",
                    "samples": 200,
                },
                {
                    "name": "法条引用准确性",
                    "description": "验证引用的法条是否真实存在",
                    "metric": "引用准确率",
                    "samples": 500,
                },
            ],
            "total_samples": 1800,
        }


# =============================================================================
# Singleton
# =============================================================================

_pipeline: FineTuningPipeline | None = None


def get_finetuning_pipeline() -> FineTuningPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = FineTuningPipeline()
    return _pipeline
