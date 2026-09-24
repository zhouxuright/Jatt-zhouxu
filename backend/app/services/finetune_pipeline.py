"""
法律领域模型微调管道 — LoRA/QLoRA on Qwen2.5-7B

功能:
1. 训练数据准备（从数据库导出QA对、案例、文书）
2. LoRA微调配置
3. 训练监控
4. 模型评估
5. 部署切换（vLLM推理）

使用方法:
    cd backend
    python scripts/prepare_finetune_data.py --output data/finetune/
    python scripts/run_finetune.py --config configs/finetune_law.yaml
"""

from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Training Data Preparation
# ============================================================================

@dataclass
class FinetuneSample:
    """微调训练样本"""
    instruction: str  # 指令/问题
    input: str = ""   # 输入上下文
    output: str = ""  # 期望输出
    system: str = "你是一位专业的中国法律AI助手，提供准确、可靠的法律分析和建议。"
    category: str = ""  # 类别标签


class FinetuneDataPreparer:
    """微调数据准备器"""

    # Alpaca-format template
    ALPACA_TEMPLATE = {
        "prompt": "### Instruction:\n{instruction}\n\n### Input:\n{input}\n\n### Response:\n",
        "prompt_no_input": "### Instruction:\n{instruction}\n\n### Response:\n",
    }

    # ShareGPT-format template
    SHAREGPT_TEMPLATE = {
        "conversations": [
            {"from": "system", "value": "{system}"},
            {"from": "human", "value": "{instruction}\n{input}"},
            {"from": "gpt", "value": "{output}"},
        ]
    }

    def __init__(self, data_dir: Path | str = "data/finetune"):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    async def prepare_from_database(self, max_samples: int = 50000) -> list[FinetuneSample]:
        """从数据库导出训练数据"""
        samples = []

        try:
            from sqlalchemy import text
            from app.core.database import async_session_factory

            async with async_session_factory() as session:
                # 1. 导出法律QA对
                result = await session.execute(text(
                    "SELECT question, answer, category FROM legal_qa "
                    "WHERE question IS NOT NULL AND answer IS NOT NULL "
                    "ORDER BY RANDOM() LIMIT :limit"
                ), {"limit": max_samples // 3})

                for row in result.fetchall():
                    samples.append(FinetuneSample(
                        instruction=row[0],
                        output=row[1],
                        category=row[2] or "legal_qa",
                    ))

                # 2. 导出法条解析
                result = await session.execute(text(
                    "SELECT title, content, domain FROM legal_articles "
                    "WHERE content IS NOT NULL "
                    "ORDER BY RANDOM() LIMIT :limit"
                ), {"limit": max_samples // 4})

                for row in result.fetchall():
                    samples.append(FinetuneSample(
                        instruction=f"请解释以下法条的含义和适用场景：{row[0]}",
                        output=row[1],
                        category=f"law_article:{row[2]}" if row[2] else "law_article",
                    ))

                # 3. 导出案例
                result = await session.execute(text(
                    "SELECT title, summary, domain FROM court_cases "
                    "WHERE summary IS NOT NULL "
                    "ORDER BY RANDOM() LIMIT :limit"
                ), {"limit": max_samples // 4})

                for row in result.fetchall():
                    samples.append(FinetuneSample(
                        instruction=f"分析以下案例的法律要点：{row[0]}",
                        output=row[1] or "",
                        category=f"case:{row[2]}" if row[2] else "case",
                    ))

                # 4. 导出知识条目
                result = await session.execute(text(
                    "SELECT title, content, domain FROM legal_knowledge "
                    "WHERE content IS NOT NULL AND type='qa_pair' "
                    "ORDER BY RANDOM() LIMIT :limit"
                ), {"limit": max_samples // 4})

                for row in result.fetchall():
                    samples.append(FinetuneSample(
                        instruction=row[0],
                        output=row[1],
                        category=f"knowledge:{row[2]}" if row[2] else "knowledge",
                    ))

        except Exception as e:
            logger.warning("Database export failed: %s, using file-based data", e)
            samples.extend(self._prepare_from_files())

        logger.info("Prepared %d training samples", len(samples))
        return samples

    def _prepare_from_files(self) -> list[FinetuneSample]:
        """从本地文件准备数据"""
        samples = []

        # Read QA pairs
        qa_file = Path("data/expanded_datasets/laws_qa_pairs.jsonl")
        if qa_file.exists():
            with open(qa_file, "r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i >= 20000:
                        break
                    try:
                        record = json.loads(line)
                        samples.append(FinetuneSample(
                            instruction=record.get("question", ""),
                            output=record.get("answer", ""),
                            category=record.get("domain", "qa"),
                        ))
                    except json.JSONDecodeError:
                        continue

        # Read synthetic QA
        synthetic_file = Path("data/mass_expansion/synthetic_qa_pairs.jsonl")
        if synthetic_file.exists():
            with open(synthetic_file, "r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i >= 30000:
                        break
                    try:
                        record = json.loads(line)
                        samples.append(FinetuneSample(
                            instruction=record.get("question", ""),
                            output=record.get("answer", ""),
                            category=record.get("domain", "synthetic"),
                        ))
                    except json.JSONDecodeError:
                        continue

        return samples

    def export_alpaca_format(self, samples: list[FinetuneSample], filename: str = "train_alpaca.json"):
        """导出为Alpaca格式"""
        output_path = self._data_dir / filename
        data = []
        for s in samples:
            data.append({
                "instruction": s.instruction,
                "input": s.input,
                "output": s.output,
                "system": s.system,
                "category": s.category,
            })

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info("Exported %d samples to %s (Alpaca format)", len(data), output_path)
        return output_path

    def export_sharegpt_format(self, samples: list[FinetuneSample], filename: str = "train_sharegpt.json"):
        """导出为ShareGPT格式"""
        output_path = self._data_dir / filename
        data = []
        for s in samples:
            input_text = f"{s.instruction}\n{s.input}" if s.input else s.instruction
            data.append({
                "conversations": [
                    {"from": "system", "value": s.system},
                    {"from": "human", "value": input_text},
                    {"from": "gpt", "value": s.output},
                ],
                "category": s.category,
            })

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info("Exported %d samples to %s (ShareGPT format)", len(data), output_path)
        return output_path

    def export_train_test_split(self, samples: list[FinetuneSample], test_ratio: float = 0.05):
        """导出训练集/测试集划分"""
        random.shuffle(samples)
        split_idx = int(len(samples) * (1 - test_ratio))

        train_samples = samples[:split_idx]
        test_samples = samples[split_idx:]

        self.export_alpaca_format(train_samples, "train.json")
        self.export_alpaca_format(test_samples, "test.json")

        logger.info("Train: %d, Test: %d", len(train_samples), len(test_samples))
        return train_samples, test_samples


# ============================================================================
# LoRA Configuration
# ============================================================================

LORA_CONFIG = {
    "base_model": "Qwen/Qwen2.5-7B-Instruct",
    "lora_r": 64,
    "lora_alpha": 128,
    "lora_dropout": 0.05,
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    "task_type": "CAUSAL_LM",
    "bias": "none",
}

TRAINING_CONFIG = {
    "output_dir": "models/legal-lora",
    "num_train_epochs": 3,
    "per_device_train_batch_size": 4,
    "gradient_accumulation_steps": 4,
    "learning_rate": 2e-4,
    "warmup_ratio": 0.03,
    "lr_scheduler_type": "cosine",
    "logging_steps": 10,
    "save_strategy": "steps",
    "save_steps": 500,
    "save_total_limit": 3,
    "bf16": True,
    "max_seq_length": 4096,
    "gradient_checkpointing": True,
}


# ============================================================================
# Main execution
# ============================================================================

async def prepare_data(output_dir: str = "data/finetune", max_samples: int = 50000):
    """主入口: 准备微调数据"""
    preparer = FinetuneDataPreparer(output_dir)

    # 从数据库和文件准备数据
    samples = await preparer.prepare_from_database(max_samples)

    if not samples:
        logger.error("No training samples prepared!")
        return

    # 导出各种格式
    preparer.export_alpaca_format(samples, "train_all.json")
    preparer.export_sharegpt_format(samples, "train_sharegpt.json")
    preparer.export_train_test_split(samples)

    # 打印统计
    categories: dict[str, int] = {}
    for s in samples:
        cat = s.category.split(":")[0] if ":" in s.category else s.category
        categories[cat] = categories.get(cat, 0) + 1

    logger.info("=== Training Data Statistics ===")
    logger.info("Total samples: %d", len(samples))
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        logger.info("  %s: %d", cat, count)
    logger.info("Output dir: %s", output_dir)

    return samples
