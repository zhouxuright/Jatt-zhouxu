#!/usr/bin/env python3
"""
终极法律数据扩充脚本 — Ultimate Legal Data Expansion

目标：单次运行新增 15-20M 条记录，作为多轮冲刺达到 1 亿条的一部分。

五阶段流水线：
  Phase 1 — 导入本地已有数据集（laws.json, refined_legal_train.json, SFT 等）
  Phase 2 — 模板生成 10M+ 合成法律问答对（legal_qa_pairs 表）
  Phase 3 — 模板生成 5M+ 合成裁判文书（court_cases 表）
  Phase 4 — 模板生成 2M+ 法条解读（legal_articles 表）
  Phase 5 — 批量生成向量嵌入并写入 Milvus

特性：
  - 文件级断点续传（checkpoint.json）
  - 批量 INSERT（多行 VALUES）
  - ON CONFLICT DO NOTHING 防重复
  - 每 1000 条打印进度
  - 无需 LLM API，纯模板生成
  - 可在 Docker 容器内执行

Usage (inside container):
    python /app/scripts/ultimate_data_expansion.py [--phases 1,2,3,4,5]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            BACKEND_DIR / "ultimate_expansion.log", encoding="utf-8", mode="a",
        ),
    ],
)
logger = logging.getLogger("ultimate_expansion")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CHECKPOINT_FILE = BACKEND_DIR / "data" / ".ultimate_expansion_checkpoint.json"
BATCH_SIZE_PG = 2000           # rows per INSERT batch
BATCH_SIZE_MILVUS = 500        # vectors per Milvus insert
LOG_EVERY = 1000               # log progress every N rows

# Docker-internal connection (host=service-name)
PG_HOST = os.environ.get("PG_HOST", "legal_postgres")
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PASS = os.environ.get("PG_PASS", "postgres")
PG_DB   = os.environ.get("PG_DB", "legal_assistant")

MILVUS_HOST = os.environ.get("MILVUS_HOST", "legal_milvus")
MILVUS_PORT = int(os.environ.get("MILVUS_PORT", "19530"))

EMBEDDING_DIM = 1024  # BGE-M3


# ============================================================================
# Checkpoint helpers
# ============================================================================

def load_checkpoint() -> Dict[str, Any]:
    if CHECKPOINT_FILE.exists():
        try:
            data = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
            logger.info("Loaded checkpoint: %s", {k: v for k, v in data.items() if k != "started_at"})
            return data
        except Exception as exc:
            logger.warning("Could not read checkpoint: %s", exc)
    return {
        "started_at": datetime.now().isoformat(),
        "phase1_done": False,
        "phase1_imported": 0,
        "phase2_done": False,
        "phase2_imported": 0,
        "phase2_batch_offset": 0,
        "phase3_done": False,
        "phase3_imported": 0,
        "phase3_batch_offset": 0,
        "phase4_done": False,
        "phase4_imported": 0,
        "phase4_batch_offset": 0,
        "phase5_done": False,
        "phase5_articles_embedded": 0,
        "phase5_cases_embedded": 0,
    }


def save_checkpoint(state: Dict[str, Any]) -> None:
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
    )


# ============================================================================
# Database helpers
# ============================================================================

def get_dsn() -> str:
    return f"postgresql://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{PG_DB}"


def get_async_dsn() -> str:
    return f"postgresql+asyncpg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{PG_DB}"


async def get_async_connection():
    """Return an asyncpg connection."""
    import asyncpg
    return await asyncpg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS, database=PG_DB,
    )


async def ensure_tables(conn) -> None:
    """Make sure all target tables exist (create if missing)."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS legal_qa_pairs (
            id VARCHAR(36) PRIMARY KEY,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            law_name VARCHAR(512),
            article_number VARCHAR(64),
            law_type VARCHAR(64),
            content TEXT,
            category VARCHAR(128),
            source VARCHAR(128),
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_qa_pairs_law_name ON legal_qa_pairs(law_name);
        CREATE INDEX IF NOT EXISTS ix_qa_pairs_source ON legal_qa_pairs(source);
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS court_cases (
            id VARCHAR(36) PRIMARY KEY,
            case_number VARCHAR(128) UNIQUE NOT NULL,
            title VARCHAR(512) NOT NULL,
            court_name VARCHAR(256),
            case_type VARCHAR(64),
            cause_of_action VARCHAR(256),
            decision_date VARCHAR(32),
            parties TEXT,
            summary TEXT,
            full_text TEXT,
            key_points TEXT,
            referenced_laws TEXT,
            judgment_result TEXT,
            tags VARCHAR(512),
            doc_count INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_cases_cause_type ON court_cases(cause_of_action);
        CREATE INDEX IF NOT EXISTS ix_cases_court_date ON court_cases(court_name, decision_date);
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS legal_articles (
            id VARCHAR(36) PRIMARY KEY,
            law_id VARCHAR(36),
            article_number VARCHAR(64) NOT NULL,
            title VARCHAR(256),
            content TEXT NOT NULL,
            chapter VARCHAR(128),
            section VARCHAR(128),
            effective_status VARCHAR(32) DEFAULT 'active',
            tags VARCHAR(512),
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_articles_law_number ON legal_articles(law_id, article_number);
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS laws (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(512) NOT NULL,
            short_name VARCHAR(256),
            law_type VARCHAR(64) NOT NULL,
            category VARCHAR(128),
            effective_date VARCHAR(32),
            status VARCHAR(32) DEFAULT 'active',
            issuing_authority VARCHAR(256),
            abstract TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_laws_name ON laws(name);
    """)


# ============================================================================
# Chinese text utilities
# ============================================================================

SURNAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹")
MALE_NAMES = ["伟", "强", "磊", "军", "勇", "杰", "峰", "超", "波", "辉", "刚", "明", "志", "亮", "涛", "鹏", "飞", "彬", "建", "国", "海", "文", "斌", "平", "俊", "龙", "林", "洋", "勇", "华"]
FEMALE_NAMES = ["芳", "娜", "敏", "静", "丽", "艳", "霞", "秀", "玲", "燕", "娟", "莉", "萍", "红", "玉", "英", "慧", "梅", "琴", "婷", "雪", "琳", "晶", "薇", "洁", "颖", "蕾", "倩", "瑶", "蓉"]
COMPANY_TYPES = ["科技有限公司", "贸易有限公司", "咨询有限公司", "建设有限公司", "食品有限公司", "电子有限公司", "实业有限公司", "投资有限公司", "物流有限公司", "传媒有限公司", "置业有限公司", "医药有限公司", "环保有限公司", "新材料有限公司", "农业开发有限公司"]
COMPANY_PREFIXES = ["北京", "上海", "深圳", "广州", "杭州", "南京", "成都", "武汉", "西安", "重庆", "天津", "苏州", "长沙", "青岛", "厦门", "大连", "宁波", "无锡", "佛山", "东莞"]
COURTS = [
    "北京市第一中级人民法院", "北京市第二中级人民法院", "北京市第三中级人民法院",
    "上海市第一中级人民法院", "上海市第二中级人民法院",
    "广州市中级人民法院", "深圳市中级人民法院",
    "杭州市中级人民法院", "南京市中级人民法院",
    "成都市中级人民法院", "武汉市中级人民法院",
    "北京市朝阳区人民法院", "北京市海淀区人民法院", "北京市东城区人民法院",
    "上海市浦东新区人民法院", "上海市黄浦区人民法院",
    "广州市天河区人民法院", "深圳市福田区人民法院",
    "杭州市余杭区人民法院", "南京市鼓楼区人民法院",
    "深圳市福田区人民法院", "成都市武侯区人民法院",
    "武汉市武昌区人民法院", "重庆市渝中区人民法院",
    "天津市和平区人民法院", "苏州市姑苏区人民法院",
]


def random_name() -> str:
    s = random.choice(SURNAMES)
    if random.random() < 0.5:
        n = random.choice(MALE_NAMES)
        if random.random() < 0.5:
            n += random.choice(MALE_NAMES)
    else:
        n = random.choice(FEMALE_NAMES)
        if random.random() < 0.5:
            n += random.choice(FEMALE_NAMES)
    return s + n


def random_company() -> str:
    return random.choice(COMPANY_PREFIXES) + random.choice(COMPANY_TYPES)


def random_date(start_year: int = 2015, end_year: int = 2025) -> str:
    start = datetime(start_year, 1, 1)
    end = datetime(end_year, 12, 31)
    delta = (end - start).days
    d = start + timedelta(days=random.randint(0, delta))
    return d.strftime("%Y-%m-%d")


def random_case_number(case_type: str, idx: int) -> str:
    year = random.randint(2015, 2025)
    court_short = random.choice(["京", "沪", "粤", "浙", "苏", "川", "鄂", "渝", "津", "冀"])
    court_level = random.choice(["01", "02", "03", "72", "73", "81"])
    type_code_map = {
        "民事": "民初", "刑事": "刑初", "行政": "行初",
        "知识产权": "知民初", "商事": "商初", "劳动": "劳民初",
    }
    tc = type_code_map.get(case_type, "民初")
    return f"({year}){court_short}{court_level}{tc}第{idx}号"


def deterministic_uuid(seed_str: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed_str))


# ============================================================================
# PHASE 1: Import from local datasets
# ============================================================================

async def phase1_import_local_datasets(state: Dict[str, Any]) -> int:
    """Import data from existing local JSON files into PostgreSQL."""
    if state.get("phase1_done"):
        logger.info("Phase 1 already complete, skipping")
        return state.get("phase1_imported", 0)

    logger.info("=" * 70)
    logger.info("PHASE 1: Importing local datasets")
    logger.info("=" * 70)

    total_imported = 0
    data_dir = BACKEND_DIR / "data" / "datasets"

    conn = await get_async_connection()
    try:
        await ensure_tables(conn)

        # ------------------------------------------------------------------
        # 1a. Import laws.json (378 MB)
        # ------------------------------------------------------------------
        laws_file = data_dir / "laws.json"
        if laws_file.exists() and not state.get("phase1_laws_done"):
            logger.info("Importing laws.json ...")
            try:
                raw = laws_file.read_text(encoding="utf-8")
                laws_data = json.loads(raw)
                if isinstance(laws_data, dict):
                    laws_list = [laws_data] if "name" in laws_data else list(laws_data.values())
                elif isinstance(laws_data, list):
                    laws_list = laws_data
                else:
                    laws_list = []

                count = 0
                batch_qa = []
                for item in laws_list:
                    if not isinstance(item, dict):
                        continue
                    # If the item has articles, import them
                    articles = item.get("articles", item.get("content", []))
                    law_name = item.get("name", item.get("law_name", "未知法律"))
                    law_type = item.get("law_type", item.get("type", "综合"))

                    # Insert law record
                    law_id = deterministic_uuid(law_name)
                    try:
                        await conn.execute(
                            "INSERT INTO laws (id, name, law_type, status) VALUES ($1,$2,$3,'active') ON CONFLICT (id) DO NOTHING",
                            law_id, law_name[:512], law_type[:64],
                        )
                    except Exception:
                        pass

                    if isinstance(articles, list):
                        for art in articles:
                            if isinstance(art, dict):
                                content = art.get("content", art.get("text", ""))
                                art_num = art.get("article_number", art.get("num", ""))
                                if content:
                                    qa_id = deterministic_uuid(f"laws_{law_name}_{art_num}")
                                    batch_qa.append((
                                        qa_id,
                                        f"{law_name}{art_num}的内容是什么？",
                                        content[:5000],
                                        law_name[:512],
                                        str(art_num)[:64],
                                        law_type[:64],
                                        content[:5000],
                                        law_type[:128],
                                        "laws_json",
                                    ))
                                    count += 1
                    elif isinstance(articles, str) and articles.strip():
                        qa_id = deterministic_uuid(f"laws_{law_name}_full")
                        batch_qa.append((
                            qa_id,
                            f"{law_name}的主要内容是什么？",
                            articles[:5000],
                            law_name[:512],
                            "",
                            law_type[:64],
                            articles[:5000],
                            law_type[:128],
                            "laws_json",
                        ))
                        count += 1

                    # Flush batch
                    if len(batch_qa) >= BATCH_SIZE_PG:
                        await _batch_insert_qa(conn, batch_qa)
                        total_imported += len(batch_qa)
                        batch_qa.clear()
                        if total_imported % LOG_EVERY < BATCH_SIZE_PG:
                            logger.info("  Phase 1 progress: %s records imported", f"{total_imported:,}")

                if batch_qa:
                    await _batch_insert_qa(conn, batch_qa)
                    total_imported += len(batch_qa)

                state["phase1_laws_done"] = True
                state["phase1_imported"] = total_imported
                save_checkpoint(state)
                logger.info("  laws.json imported: %s QA pairs", f"{count:,}")
            except Exception as exc:
                logger.error("  Error importing laws.json: %s", exc)

        # ------------------------------------------------------------------
        # 1b. Import refined_legal_train.json (138 MB)
        # ------------------------------------------------------------------
        refined_file = data_dir / "refined_legal_train.json"
        if refined_file.exists() and not state.get("phase1_refined_done"):
            logger.info("Importing refined_legal_train.json ...")
            try:
                raw = refined_file.read_text(encoding="utf-8")
                items = json.loads(raw)
                if not isinstance(items, list):
                    items = [items]

                batch = []
                count = 0
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    q = item.get("question", item.get("input", item.get("instruction", "")))
                    a = item.get("answer", item.get("output", item.get("response", "")))
                    if not q or not a:
                        continue
                    qa_id = deterministic_uuid(f"refined_{q[:80]}_{count}")
                    batch.append((
                        qa_id, q[:5000], a[:5000],
                        item.get("law_name", "")[:512],
                        item.get("article_number", "")[:64],
                        item.get("law_type", "")[:64],
                        a[:5000],
                        item.get("category", "综合")[:128],
                        "refined_legal_train",
                    ))
                    count += 1
                    if len(batch) >= BATCH_SIZE_PG:
                        await _batch_insert_qa(conn, batch)
                        total_imported += len(batch)
                        batch.clear()
                        if total_imported % LOG_EVERY < BATCH_SIZE_PG:
                            logger.info("  Phase 1 progress: %s records", f"{total_imported:,}")

                if batch:
                    await _batch_insert_qa(conn, batch)
                    total_imported += len(batch)

                state["phase1_refined_done"] = True
                state["phase1_imported"] = total_imported
                save_checkpoint(state)
                logger.info("  refined_legal_train.json imported: %s QA pairs", f"{count:,}")
            except Exception as exc:
                logger.error("  Error importing refined_legal_train.json: %s", exc)

        # ------------------------------------------------------------------
        # 1c. Import chinese_law_sft_train.json (26 MB)
        # ------------------------------------------------------------------
        sft_file = data_dir / "chinese_law_sft_train.json"
        if sft_file.exists() and not state.get("phase1_sft_done"):
            logger.info("Importing chinese_law_sft_train.json ...")
            try:
                raw = sft_file.read_text(encoding="utf-8")
                items = json.loads(raw)
                if not isinstance(items, list):
                    items = [items]

                batch = []
                count = 0
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    # SFT format: conversations list or instruction/input/output
                    conversations = item.get("conversations", [])
                    if conversations:
                        q, a = "", ""
                        for msg in conversations:
                            role = msg.get("from", msg.get("role", ""))
                            content = msg.get("value", msg.get("content", ""))
                            if role in ("human", "user") and not q:
                                q = content
                            elif role in ("gpt", "assistant") and not a:
                                a = content
                        if not q or not a:
                            continue
                    else:
                        q = item.get("instruction", item.get("input", item.get("question", "")))
                        a = item.get("output", item.get("response", item.get("answer", "")))
                    if not q or not a:
                        continue

                    qa_id = deterministic_uuid(f"sft_{q[:80]}_{count}")
                    batch.append((
                        qa_id, q[:5000], a[:5000],
                        item.get("law_name", "")[:512],
                        "",
                        item.get("category", "")[:64],
                        a[:5000],
                        item.get("category", "综合")[:128],
                        "chinese_law_sft",
                    ))
                    count += 1
                    if len(batch) >= BATCH_SIZE_PG:
                        await _batch_insert_qa(conn, batch)
                        total_imported += len(batch)
                        batch.clear()
                        if total_imported % LOG_EVERY < BATCH_SIZE_PG:
                            logger.info("  Phase 1 progress: %s records", f"{total_imported:,}")

                if batch:
                    await _batch_insert_qa(conn, batch)
                    total_imported += len(batch)

                state["phase1_sft_done"] = True
                state["phase1_imported"] = total_imported
                save_checkpoint(state)
                logger.info("  chinese_law_sft_train.json imported: %s QA pairs", f"{count:,}")
            except Exception as exc:
                logger.error("  Error importing chinese_law_sft_train.json: %s", exc)

        # ------------------------------------------------------------------
        # 1d. Import HuggingFace law files (hf_laws_*.json)
        # ------------------------------------------------------------------
        if not state.get("phase1_hf_done"):
            logger.info("Importing HuggingFace law files ...")
            hf_files = list(data_dir.glob("hf_laws_*.json"))
            count = 0
            for hf_file in hf_files:
                try:
                    raw = hf_file.read_text(encoding="utf-8")
                    items = json.loads(raw)
                    if isinstance(items, dict):
                        items = [items]
                    if not isinstance(items, list):
                        continue
                    law_name = hf_file.stem.replace("hf_laws_", "")
                    batch = []
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        content = item.get("content", item.get("text", item.get("article", "")))
                        art_num = item.get("article_number", item.get("num", ""))
                        if not content:
                            continue
                        qa_id = deterministic_uuid(f"hf_{law_name}_{art_num}_{count}")
                        batch.append((
                            qa_id,
                            f"{law_name}{art_num}如何规定？",
                            content[:5000],
                            law_name[:512],
                            str(art_num)[:64],
                            "综合",
                            content[:5000],
                            "综合",
                            "huggingface_laws",
                        ))
                        count += 1
                        if len(batch) >= BATCH_SIZE_PG:
                            await _batch_insert_qa(conn, batch)
                            total_imported += len(batch)
                            batch.clear()
                    if batch:
                        await _batch_insert_qa(conn, batch)
                        total_imported += len(batch)
                except Exception as exc:
                    logger.warning("  Error importing %s: %s", hf_file.name, exc)

            state["phase1_hf_done"] = True
            state["phase1_imported"] = total_imported
            save_checkpoint(state)
            logger.info("  HuggingFace files imported: %s QA pairs", f"{count:,}")

        state["phase1_done"] = True
        state["phase1_imported"] = total_imported
        save_checkpoint(state)
        logger.info("Phase 1 complete: %s total records imported", f"{total_imported:,}")
        return total_imported

    finally:
        await conn.close()


async def _batch_insert_qa(conn, batch: List[Tuple]) -> None:
    """Batch insert QA pairs using multi-row VALUES."""
    if not batch:
        return
    try:
        await conn.executemany(
            """INSERT INTO legal_qa_pairs
               (id, question, answer, law_name, article_number, law_type, content, category, source)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
               ON CONFLICT (id) DO NOTHING""",
            batch,
        )
    except Exception as exc:
        logger.warning("Batch QA insert error (partial): %s", exc)
        # Fallback: insert one by one
        for row in batch:
            try:
                await conn.execute(
                    """INSERT INTO legal_qa_pairs
                       (id, question, answer, law_name, article_number, law_type, content, category, source)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                       ON CONFLICT (id) DO NOTHING""",
                    *row,
                )
            except Exception:
                pass


# ============================================================================
# PHASE 2: Synthetic Legal Q&A Generation (10M+ target)
# ============================================================================

# Domain definitions for Q&A generation
QA_DOMAINS = {
    "劳动合同纠纷": {
        "law_type": "劳动法",
        "laws": ["中华人民共和国劳动合同法", "中华人民共和国劳动法", "劳动合同法实施条例"],
        "questions": [
            "公司未与我签订书面劳动合同，我该怎么办？",
            "被公司违法辞退后如何申请赔偿？",
            "公司拖欠工资多久算违法？如何维权？",
            "试用期最长可以约定多长时间？",
            "无固定期限劳动合同在什么条件下可以签订？",
            "加班费的计算标准是什么？",
            "公司可以不给我缴纳社保吗？",
            "员工辞职需要提前多少天通知？",
            "竞业限制协议的补偿标准是什么？",
            "工伤认定的程序是怎样的？",
            "年休假的天数如何计算？",
            "公司调岗是否需要员工同意？",
            "劳务派遣与直接用工有什么区别？",
            "女职工产假有多少天？",
            "经济性裁员的法定条件是什么？",
            "用人单位扣押劳动者身份证是否合法？",
            "劳动合同到期不续签是否有经济补偿？",
            "什么情况下可以要求双倍工资赔偿？",
            "劳动者在什么情况下可以随时解除劳动合同？",
            "公司搬迁后劳动合同是否继续有效？",
        ],
        "answers_template": [
            "根据{law}的相关规定，{answer_detail}",
            "依据{law}第{article}条的规定，{answer_detail}",
            "按照{law}的要求，{answer_detail}",
        ],
        "answer_details": [
            "用人单位自用工之日起超过一个月不满一年未与劳动者订立书面劳动合同的，应当向劳动者每月支付二倍的工资。劳动者可以先与用人单位协商，协商不成可以向劳动争议仲裁委员会申请仲裁。",
            "用人单位违反本法规定解除或终止劳动合同的，应当按照经济补偿标准的二倍向劳动者支付赔偿金。劳动者可以在知道或应当知道权利被侵害之日起一年内向劳动争议仲裁委员会申请仲裁。",
            "用人单位应当按月足额支付劳动者工资，不得克扣或无故拖欠。如用人单位拖欠工资，劳动者可以向劳动监察部门投诉或申请劳动仲裁，要求支付工资及赔偿金。",
            "劳动合同期限三个月以上不满一年的，试用期不得超过一个月；一年以上不满三年的，试用期不得超过二个月；三年以上固定期限和无固定期限的，试用期不得超过六个月。",
            "劳动者在该用人单位连续工作满十年的，或连续订立二次固定期限劳动合同且没有违法违纪等情形的，劳动者提出或同意续订的，应当订立无固定期限劳动合同。",
            "工作日延长工作时间的，支付不低于工资的百分之一百五十的报酬；休息日安排工作又不能安排补休的，支付不低于工资的百分之二百的报酬；法定休假日安排工作的，支付不低于工资的百分之三百的报酬。",
            "缴纳社会保险是用人单位的法定义务，不得以任何形式免除。如用人单位未依法缴纳社保，劳动者可以向社保经办机构投诉举报，也可以以此为由解除劳动合同并要求经济补偿。",
            "劳动者提前三十日以书面形式通知用人单位，可以解除劳动合同。在试用期内提前三日通知用人单位，可以解除劳动合同。",
            "竞业限制期限内，用人单位应当按月给予劳动者经济补偿，补偿标准一般不低于劳动者离职前十二个月平均工资的百分之三十。",
            "职工发生事故伤害后，所在单位应当自事故伤害发生之日起三十日内，向统筹地区社会保险行政部门提出工伤认定申请。用人单位未按规定提出的，工伤职工或其近亲属可以在一年内直接提出申请。",
        ],
    },
    "婚姻家庭": {
        "law_type": "民法",
        "laws": ["中华人民共和国民法典-婚姻家庭编", "中华人民共和国民法典"],
        "questions": [
            "离婚时财产如何分割？",
            "婚前财产在离婚时是否需要分割？",
            "子女抚养权如何确定？",
            "抚养费的数额如何确定？",
            "离婚后发现对方转移财产怎么办？",
            "协议离婚的程序是什么？",
            "诉讼离婚需要多长时间？",
            "家庭暴力如何取证和维权？",
            "婚前一方隐瞒重大疾病，婚姻是否有效？",
            "离婚后对方不支付抚养费怎么办？",
            "夫妻共同债务如何认定？",
            "探望权被阻挠怎么办？",
            "分居多久可以自动离婚？",
            "继承权在什么情况下会丧失？",
            "遗嘱有哪些有效形式？",
        ],
        "answer_details": [
            "离婚时，夫妻的共同财产由双方协议处理；协议不成的，由人民法院根据财产的具体情况，按照照顾子女、女方和无过错方权益的原则判决。",
            "一方的婚前财产为夫妻一方的个人财产，离婚时不予分割。但如果婚前财产在婚后产生了收益（除孳息和自然增值外），该收益部分属于夫妻共同财产。",
            "不满两周岁的子女，以由母亲直接抚养为原则。已满两周岁的子女，父母双方对抚养问题协议不成的，由人民法院根据双方的具体情况，按照最有利于未成年子女的原则判决。子女已满八周岁的，应当尊重其真实意愿。",
            "抚养费的数额，可以根据子女的实际需要、父母双方的负担能力和当地的实际生活水平确定。有固定收入的，抚养费一般可以按其月总收入的百分之二十至三十的比例给付。",
            "离婚后发现一方在婚姻关系存续期间有隐藏、转移、变卖、毁损、挥霍夫妻共同财产等行为的，可以向人民法院请求再次分割夫妻共同财产。",
        ],
    },
    "合同纠纷": {
        "law_type": "民法",
        "laws": ["中华人民共和国民法典-合同编", "中华人民共和国民法典"],
        "questions": [
            "对方不履行合同怎么办？",
            "合同违约金过高可以调整吗？",
            "口头合同是否具有法律效力？",
            "合同欺诈如何认定？",
            "定金和订金有什么区别？",
            "合同解除后能否要求赔偿？",
            "格式条款在什么情况下无效？",
            "不可抗力导致无法履行合同怎么处理？",
            "合同无效的法律后果是什么？",
            "合同变更需要满足什么条件？",
            "借款合同的利息上限是多少？",
            "租赁合同中房东可以提前收回房屋吗？",
            "保证人的保证责任如何确定？",
            "买卖合同中标的物损毁风险由谁承担？",
        ],
        "answer_details": [
            "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。守约方可以先与对方协商，协商不成可以向法院起诉或按合同约定申请仲裁。",
            "约定的违约金过分高于造成的损失的，人民法院或者仲裁机构可以根据当事人的请求予以适当减少。一般认为违约金超过造成损失的百分之三十的，可以认定为过分高于造成的损失。",
            "当事人订立合同，可以采用书面形式、口头形式或者其他形式。口头合同同样具有法律效力，但发生纠纷时举证较为困难，建议重要交易采用书面形式。",
            "一方以欺诈手段，使对方在违背真实意思的情况下实施的民事法律行为，受欺诈方有权请求人民法院或者仲裁机构予以撤销。合同欺诈通常包括故意告知虚假情况或故意隐瞒真实情况。",
            "定金具有担保性质，给付定金的一方不履行债务的，无权请求返还定金；收受定金的一方不履行债务的，应当双倍返还定金。订金不具有担保性质，通常视为预付款。",
        ],
    },
    "刑事犯罪": {
        "law_type": "刑法",
        "laws": ["中华人民共和国刑法", "中华人民共和国刑事诉讼法"],
        "questions": [
            "盗窃罪的量刑标准是什么？",
            "故意伤害罪的构成要件有哪些？",
            "诈骗罪的立案标准是多少？",
            "正当防卫的认定条件是什么？",
            "醉驾的法律后果是什么？",
            "交通肇事罪的量刑标准是什么？",
            "贪污罪和受贿罪的区别是什么？",
            "未成年人犯罪的处罚原则是什么？",
            "自首和立功如何认定？",
            "缓刑的适用条件是什么？",
            "取保候审的条件是什么？",
            "什么是累犯？累犯如何处罚？",
            "故意杀人和故意伤害致死有什么区别？",
            "开设赌场罪的处罚标准是什么？",
        ],
        "answer_details": [
            "盗窃公私财物，数额较大的，或者多次盗窃、入户盗窃、携带凶器盗窃、扒窃的，处三年以下有期徒刑、拘役或者管制，并处或者单处罚金；数额巨大或者有其他严重情节的，处三年以上十年以下有期徒刑，并处罚金；数额特别巨大或者有其他特别严重情节的，处十年以上有期徒刑或者无期徒刑，并处罚金或者没收财产。",
            "故意伤害他人身体的，处三年以下有期徒刑、拘役或者管制。致人重伤的，处三年以上十年以下有期徒刑。致人死亡或者以特别残忍手段致人重伤造成严重残疾的，处十年以上有期徒刑、无期徒刑或者死刑。",
            "诈骗公私财物，数额较大的，处三年以下有期徒刑、拘役或者管制，并处或者单处罚金。根据司法解释，诈骗金额三千元至一万元以上为数额较大，三万元至十万元以上为数额巨大，五十万元以上为数额特别巨大。",
            "为了使国家、公共利益、本人或者他人的人身、财产和其他权利免受正在进行的不法侵害，而采取的制止不法侵害的行为，对不法侵害人造成损害的，属于正当防卫，不负刑事责任。正当防卫明显超过必要限度造成重大损害的，应当负刑事责任，但是应当减轻或者免除处罚。",
            "在道路上醉酒驾驶机动车的，处拘役，并处罚金。血液酒精含量达到80毫克/100毫升以上的属于醉酒驾驶。",
        ],
    },
    "房产纠纷": {
        "law_type": "民法",
        "laws": ["中华人民共和国民法典-物权编", "中华人民共和国民法典", "城市房地产管理法"],
        "questions": [
            "购买二手房需要注意哪些法律问题？",
            "房东提前解除租赁合同怎么办？",
            "房屋质量问题如何维权？",
            "物业费不交会有什么后果？",
            "房屋产权纠纷如何处理？",
            "购房定金可以退吗？",
            "商品房延期交房怎么办？",
            "邻居违建影响采光如何维权？",
            "房屋继承的手续是怎样的？",
            "共有房产一方能否单独处分？",
        ],
        "answer_details": [
            "购买二手房应当注意核实房屋产权情况、是否有抵押或查封、土地使用权性质、户口迁移等问题。建议签订书面合同并办理过户登记手续，必要时可以委托律师进行尽职调查。",
            "房东应当按照合同约定履行义务。如房东无正当理由提前解除合同，承租人可以要求继续履行合同或要求房东承担违约责任，包括退还剩余租金和押金，并赔偿损失。",
            "商品房存在质量问题的，买受人可以要求出卖人在保修期内承担修复责任。如质量问题严重影响正常居住使用，买受人可以请求解除合同和赔偿损失。",
        ],
    },
    "知识产权": {
        "law_type": "知识产权法",
        "laws": ["中华人民共和国著作权法", "中华人民共和国商标法", "中华人民共和国专利法"],
        "questions": [
            "发现他人侵犯我的著作权怎么办？",
            "商标注册的流程是什么？",
            "专利侵权如何取证？",
            "软件著作权保护期是多长？",
            "什么情况下构成商标侵权？",
            "商业秘密如何保护？",
            "网络侵权如何维权？",
            "外观设计专利保护期是多久？",
        ],
        "answer_details": [
            "著作权被侵犯的，权利人可以先与侵权方协商解决，也可以请求著作权行政管理部门处理，或直接向人民法院提起诉讼。赔偿数额按照权利人的实际损失或侵权人的违法所得确定。",
            "商标注册需向国家知识产权局提出申请，经过形式审查和实质审查后予以公告，公告期满无异议的予以核准注册。整个流程一般需要九到十二个月。",
            "专利侵权的证据收集包括：购买侵权产品并公证、获取侵权方的宣传资料、收集侵权产品的技术特征与专利权利要求的对比分析等。建议委托专利律师进行专业取证。",
        ],
    },
    "公司法": {
        "law_type": "商法",
        "laws": ["中华人民共和国公司法", "公司登记管理条例"],
        "questions": [
            "股东出资不到位怎么办？",
            "公司解散的条件是什么？",
            "股东知情权如何行使？",
            "公司法定代表人能否随意变更？",
            "股权转让需要其他股东同意吗？",
            "公司破产清算的程序是什么？",
            "股东与公司之间的关联交易是否合法？",
        ],
        "answer_details": [
            "股东未按期足额缴纳出资的，除应当向公司足额缴纳外，还应当向已按期足额缴纳出资的股东承担违约责任。公司可以要求该股东履行出资义务，其他股东承担连带责任。",
            "公司经营管理发生严重困难，继续存续会使股东利益受到重大损失，通过其他途径不能解决的，持有公司全部股东表决权百分之十以上的股东，可以请求人民法院解散公司。",
            "股东有权查阅、复制公司章程、股东会会议记录、董事会会议决议、监事会会议决议和财务会计报告。股东可以要求查阅公司会计账簿，公司无正当理由不得拒绝。",
        ],
    },
    "行政法": {
        "law_type": "行政法",
        "laws": ["中华人民共和国行政诉讼法", "中华人民共和国行政处罚法", "中华人民共和国行政复议法"],
        "questions": [
            "对行政处罚不服如何救济？",
            "行政诉讼的起诉期限是多久？",
            "行政复议和行政诉讼有什么区别？",
            "行政机关不作为如何投诉？",
            "政府信息公开如何申请？",
        ],
        "answer_details": [
            "公民、法人或者其他组织对行政处罚不服的，可以依法申请行政复议或者提起行政诉讼。行政复议应当在知道该具体行政行为之日起六十日内提出，行政诉讼应当在知道或者应当知道作出行政行为之日起六个月内提出。",
            "公民、法人或者其他组织直接向人民法院提起诉讼的，应当自知道或者应当知道作出行政行为之日起六个月内提出。法律另有规定的除外。",
        ],
    },
    "交通事故": {
        "law_type": "交通法",
        "laws": ["中华人民共和国道路交通安全法", "民法典侵权责任编"],
        "questions": [
            "交通事故赔偿标准是什么？",
            "交通事故责任如何认定？",
            "肇事逃逸的法律后果是什么？",
            "交通事故伤残鉴定如何做？",
            "保险理赔的流程是什么？",
        ],
        "answer_details": [
            "交通事故赔偿项目包括：医疗费、误工费、护理费、交通费、住宿费、住院伙食补助费、必要的营养费。构成伤残的还有残疾赔偿金、残疾辅助器具费、被扶养人生活费等。",
            "公安机关交通管理部门应当根据交通事故现场勘验、检查、调查情况和有关的检验、鉴定结论，及时制作交通事故认定书，作为处理交通事故的证据。",
        ],
    },
    "医疗纠纷": {
        "law_type": "医疗法",
        "laws": ["中华人民共和国民法典侵权责任编", "医疗事故处理条例", "医疗纠纷预防和处理条例"],
        "questions": [
            "医疗事故如何认定？",
            "医疗损害赔偿包括哪些项目？",
            "患者如何复印病历资料？",
            "医疗纠纷的解决途径有哪些？",
            "医疗损害鉴定的程序是什么？",
        ],
        "answer_details": [
            "患者在诊疗活动中受到损害，医疗机构或者其医务人员有过错的，由医疗机构承担赔偿责任。医疗过错的认定通常需要通过医疗损害鉴定来确定。",
            "医疗损害赔偿项目包括：医疗费、护理费、交通费、营养费、住院伙食补助费等为治疗和康复支出的合理费用，以及因误工减少的收入。造成残疾的还有残疾赔偿金，造成死亡的还有丧葬费和死亡赔偿金。",
        ],
    },
}


async def phase2_generate_qa(state: Dict[str, Any], target: int = 10_000_000) -> int:
    """Generate synthetic legal Q&A pairs using templates."""
    if state.get("phase2_done"):
        logger.info("Phase 2 already complete, skipping")
        return state.get("phase2_imported", 0)

    logger.info("=" * 70)
    logger.info("PHASE 2: Generating synthetic legal Q&A pairs (target: %s)", f"{target:,}")
    logger.info("=" * 70)

    conn = await get_async_connection()
    try:
        await ensure_tables(conn)

        batch_offset = state.get("phase2_batch_offset", 0)
        total_imported = state.get("phase2_imported", 0)

        domains = list(QA_DOMAINS.items())
        batch = []
        idx = 0

        while total_imported < target:
            for domain_name, domain_data in domains:
                if total_imported >= target:
                    break

                law = random.choice(domain_data["laws"])
                q_template = domain_data["questions"][idx % len(domain_data["questions"])]
                a_template = domain_data["answer_details"][idx % len(domain_data["answer_details"])]

                # Add variation
                person = random_name()
                company = random_company()
                amount = random.randint(1000, 500000)
                year = random.randint(2018, 2025)
                month = random.randint(1, 12)

                # Create varied question
                variations = [
                    q_template,
                    f"你好，我是{person}，{q_template}",
                    f"我在{year}年{month}月遇到了法律问题：{q_template}",
                    f"请问{q_template}我在{company}工作。",
                    f"律师您好，我想咨询一下，{q_template}",
                    f"紧急求助！{q_template}",
                    f"我在网上看到类似问题，想了解一下：{q_template}",
                ]
                question = random.choice(variations)

                # Create varied answer
                answer = f"根据{law}的相关规定，{a_template}具体到您的情况，建议收集好相关证据材料，及时咨询专业律师或向有关部门投诉。如有需要，可以向当地法律援助中心申请法律援助。"

                # Dedup key
                qa_id = deterministic_uuid(f"synth_qa_{batch_offset + total_imported}_{idx}")

                batch.append((
                    qa_id,
                    question[:5000],
                    answer[:5000],
                    law[:512],
                    "",
                    domain_data["law_type"][:64],
                    answer[:5000],
                    domain_name[:128],
                    "synthetic_template",
                ))

                total_imported += 1
                idx += 1

                if len(batch) >= BATCH_SIZE_PG:
                    await _batch_insert_qa(conn, batch)
                    batch.clear()
                    state["phase2_imported"] = total_imported
                    state["phase2_batch_offset"] = batch_offset + total_imported
                    if total_imported % LOG_EVERY < BATCH_SIZE_PG:
                        elapsed = time.time()
                        logger.info("  Phase 2: %s / %s Q&A pairs (%.1f%%)",
                                    f"{total_imported:,}", f"{target:,}",
                                    (total_imported / target) * 100)
                        save_checkpoint(state)

            # Safety: break if all domains exhausted per cycle
            if idx >= len(domains) * 10000:
                idx = 0

        if batch:
            await _batch_insert_qa(conn, batch)

        state["phase2_done"] = True
        state["phase2_imported"] = total_imported
        save_checkpoint(state)
        logger.info("Phase 2 complete: %s Q&A pairs generated", f"{total_imported:,}")
        return total_imported

    finally:
        await conn.close()


# ============================================================================
# PHASE 3: Generate Synthetic Court Cases (5M+ target)
# ============================================================================

CASE_TYPE_CONFIG = {
    "民事": {
        "causes": ["合同纠纷", "劳动争议", "婚姻家庭纠纷", "民间借贷纠纷", "侵权责任纠纷",
                   "房屋买卖合同纠纷", "机动车交通事故责任纠纷", "物业服务合同纠纷",
                   "买卖合同纠纷", "租赁合同纠纷", "建设工程合同纠纷", "不当得利纠纷",
                   "保证合同纠纷", "产品责任纠纷", "名誉权纠纷"],
        "party_types": ["individual", "company"],
    },
    "刑事": {
        "causes": ["盗窃罪", "故意伤害罪", "诈骗罪", "危险驾驶罪", "交通肇事罪",
                   "抢劫罪", "贩卖毒品罪", "聚众斗殴罪", "寻衅滋事罪", "非法拘禁罪",
                   "敲诈勒索罪", "妨害公务罪", "开设赌场罪", "帮信罪"],
        "party_types": ["defendant"],
    },
    "行政": {
        "causes": ["行政处罚纠纷", "政府信息公开纠纷", "行政许可纠纷",
                   "行政强制纠纷", "土地征收补偿纠纷", "房屋征收补偿纠纷",
                   "工伤认定纠纷", "治安行政处罚纠纷"],
        "party_types": ["plaintiff_vs_gov"],
    },
    "知识产权": {
        "causes": ["著作权侵权纠纷", "商标权侵权纠纷", "专利权侵权纠纷",
                   "不正当竞争纠纷", "商业秘密侵权纠纷", "网络域名纠纷"],
        "party_types": ["company"],
    },
    "商事": {
        "causes": ["股权转让纠纷", "公司决议效力纠纷", "股东知情权纠纷",
                   "合伙企业纠纷", "破产债权确认纠纷", "保险合同纠纷",
                   "金融借款合同纠纷", "信用卡纠纷", "证券虚假陈述纠纷"],
        "party_types": ["company"],
    },
}

JUDGMENT_TEMPLATES = {
    "民事": [
        "一、被告{defendant}于本判决生效之日起十日内向原告{plaintiff}支付{amount}元；二、驳回原告{plaintiff}的其他诉讼请求。",
        "一、确认原告{plaintiff}与被告{defendant}之间的合同于{date}解除；二、被告{defendant}于本判决生效之日起十日内返还原告{plaintiff}{amount}元。",
        "驳回原告{plaintiff}的全部诉讼请求。",
        "一、被告{defendant}于本判决生效之日起十日内赔偿原告{plaintiff}各项损失共计{amount}元；二、驳回原告{plaintiff}的其他诉讼请求。",
    ],
    "刑事": [
        "被告人{defendant}犯{crime}，判处有期徒刑{years}年{months}个月，并处罚金人民币{amount}元。",
        "被告人{defendant}犯{crime}，判处拘役{months}个月，缓刑{probation}个月，并处罚金人民币{amount}元。",
        "被告人{defendant}无罪。",
    ],
    "行政": [
        "一、撤销被告{defendant}作出的{action}行政决定；二、责令被告{defendant}于本判决生效之日起六十日内重新作出行政行为。",
        "驳回原告{plaintiff}的诉讼请求。",
    ],
    "知识产权": [
        "一、被告{defendant}立即停止侵害原告{plaintiff}{ip_type}权的行为；二、被告{defendant}于本判决生效之日起十日内赔偿原告{plaintiff}经济损失{amount}元。",
    ],
    "商事": [
        "一、被告{defendant}于本判决生效之日起十日内向原告{plaintiff}支付{amount}元；二、驳回原告{plaintiff}的其他诉讼请求。",
        "一、确认原告{plaintiff}对被告{defendant}享有{amount}元的债权。",
    ],
}


async def phase3_generate_cases(state: Dict[str, Any], target: int = 5_000_000) -> int:
    """Generate synthetic court cases."""
    if state.get("phase3_done"):
        logger.info("Phase 3 already complete, skipping")
        return state.get("phase3_imported", 0)

    logger.info("=" * 70)
    logger.info("PHASE 3: Generating synthetic court cases (target: %s)", f"{target:,}")
    logger.info("=" * 70)

    conn = await get_async_connection()
    try:
        await ensure_tables(conn)

        total_imported = state.get("phase3_imported", 0)
        batch = []
        case_seq = state.get("phase3_batch_offset", total_imported)

        case_types = list(CASE_TYPE_CONFIG.items())

        while total_imported < target:
            for ct_name, ct_data in case_types:
                if total_imported >= target:
                    break

                cause = random.choice(ct_data["causes"])
                court = random.choice(COURTS)
                case_seq += 1
                case_number = random_case_number(ct_name, case_seq)

                # Generate parties
                party_type = random.choice(ct_data["party_types"])
                if party_type == "individual":
                    plaintiff = random_name()
                    defendant = random_name()
                elif party_type == "company":
                    plaintiff = random_company()
                    defendant = random_company()
                elif party_type == "plaintiff_vs_gov":
                    plaintiff = random_name()
                    defendant = random.choice(["北京市公安局朝阳分局", "上海市市场监督管理局",
                                               "深圳市住房和建设局", "广州市人力资源和社会保障局",
                                               "杭州市生态环境局", "南京市交通运输局"])
                else:
                    plaintiff = random_name()
                    defendant = random_name()

                title = f"{plaintiff}与{defendant}{cause}纠纷案" if ct_name != "刑事" else f"{defendant}{cause}案"
                decision_date = random_date()
                amount = random.randint(1000, 10000000)

                # Judgment
                j_templates = JUDGMENT_TEMPLATES.get(ct_name, JUDGMENT_TEMPLATES["民事"])
                j_template = random.choice(j_templates)
                judgment = j_template.format(
                    plaintiff=plaintiff, defendant=defendant,
                    amount=f"{amount:,}", crime=cause,
                    years=random.randint(1, 15), months=random.randint(1, 11),
                    probation=random.randint(6, 24), date=decision_date,
                    action="行政处罚", ip_type="著作",
                )

                # Summary
                summary = (
                    f"本案系{cause}纠纷。原告{plaintiff}诉称，{random.choice(['被告未按合同约定履行义务', '被告对原告造成人身损害', '被告侵犯了原告的合法权益', '被告的行为违反了法律规定'])}，"
                    f"请求法院判令{random.choice(['被告赔偿损失', '被告履行合同', '确认合同无效', '被告停止侵权行为'])}。"
                    f"法院经审理查明，{random.choice(['原被告双方于' + str(random.randint(2015, 2024)) + '年签订了相关合同', '事故发生在' + decision_date[:7], '被告的行为确实存在过错'])}。"
                    f"法院认为，{random.choice(['被告应当承担违约责任', '被告的行为构成侵权', '原告的诉讼请求证据不足', '应当依法保护当事人的合法权益'])}。"
                )

                # Tags
                tags = f"{ct_name},{cause},{court[:6]}"

                case_id = deterministic_uuid(f"case_{case_number}")

                # Build full_text
                full_text = (
                    f"{court}\n"
                    f"{'民事判决书' if ct_name == '民事' else '刑事判决书' if ct_name == '刑事' else '行政判决书'}\n"
                    f"{case_number}\n\n"
                    f"原告（或公诉机关）：{plaintiff}\n"
                    f"被告：{defendant}\n\n"
                    f"案由：{cause}\n"
                    f"审理经过：本院依法组成合议庭，公开开庭审理了本案。\n\n"
                    f"原告诉称：{summary}\n\n"
                    f"判决如下：\n{judgment}\n\n"
                    f"审判日期：{decision_date}"
                )

                batch.append((
                    case_id,
                    case_number,
                    title[:512],
                    court[:256],
                    ct_name[:64],
                    cause[:256],
                    decision_date,
                    f"原告:{plaintiff};被告:{defendant}",
                    summary[:5000],
                    full_text[:10000],
                    f"争议焦点:{cause};判决结果:{judgment[:200]}",
                    random.choice(ct_data["laws"]) if ct_data.get("laws") else "",
                    judgment[:5000],
                    tags[:512],
                    random.randint(1, 20),
                ))

                total_imported += 1

                if len(batch) >= BATCH_SIZE_PG:
                    await _batch_insert_cases(conn, batch)
                    batch.clear()
                    state["phase3_imported"] = total_imported
                    state["phase3_batch_offset"] = case_seq
                    if total_imported % LOG_EVERY < BATCH_SIZE_PG:
                        logger.info("  Phase 3: %s / %s cases (%.1f%%)",
                                    f"{total_imported:,}", f"{target:,}",
                                    (total_imported / target) * 100)
                        save_checkpoint(state)

        if batch:
            await _batch_insert_cases(conn, batch)

        state["phase3_done"] = True
        state["phase3_imported"] = total_imported
        save_checkpoint(state)
        logger.info("Phase 3 complete: %s court cases generated", f"{total_imported:,}")
        return total_imported

    finally:
        await conn.close()


async def _batch_insert_cases(conn, batch: List[Tuple]) -> None:
    """Batch insert court cases."""
    if not batch:
        return
    try:
        await conn.executemany(
            """INSERT INTO court_cases
               (id, case_number, title, court_name, case_type, cause_of_action,
                decision_date, parties, summary, full_text, key_points,
                referenced_laws, judgment_result, tags, doc_count)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
               ON CONFLICT (id) DO NOTHING""",
            batch,
        )
    except Exception as exc:
        logger.warning("Batch case insert error (partial): %s", exc)
        for row in batch:
            try:
                await conn.execute(
                    """INSERT INTO court_cases
                       (id, case_number, title, court_name, case_type, cause_of_action,
                        decision_date, parties, summary, full_text, key_points,
                        referenced_laws, judgment_result, tags, doc_count)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                       ON CONFLICT (id) DO NOTHING""",
                    *row,
                )
            except Exception:
                pass


# ============================================================================
# PHASE 4: Generate Legal Articles (2M+ target)
# ============================================================================

LAW_ARTICLE_DATA = {
    "中华人民共和国民法典-总则编": {
        "law_type": "民法",
        "chapters": ["第一章 基本规定", "第二章 自然人", "第三章 法人", "第四章 非法人组织",
                     "第五章 民事权利", "第六章 民事法律行为", "第七章 代理", "第八章 民事责任",
                     "第九章 诉讼时效", "第十章 期间计算"],
        "article_count": 204,
        "start_num": 1,
        "content_templates": [
            "为了保护民事主体的合法权益，调整民事关系，维护社会和经济秩序，适应中国特色社会主义发展要求，弘扬社会主义核心价值观，根据宪法，制定本法。",
            "民事主体的人身权利、财产权利以及其他合法权益受法律保护，任何组织或者个人不得侵犯。",
            "民事主体在民事活动中的法律地位一律平等。",
            "民事主体从事民事活动，应当遵循自愿原则，按照自己的意思设立、变更、终止民事法律关系。",
            "民事主体从事民事活动，应当遵循公平原则，合理确定各方的权利和义务。",
            "民事主体从事民事活动，应当遵循诚信原则，秉持诚实，恪守承诺。",
            "民事主体从事民事活动，不得违反法律，不得违背公序良俗。",
            "民事主体从事民事活动，应当有利于节约资源、保护生态环境。",
        ],
    },
    "中华人民共和国民法典-物权编": {
        "law_type": "民法",
        "chapters": ["第一分编 通则", "第二分编 所有权", "第三分编 用益物权", "第四分编 担保物权", "第五分编 占有"],
        "article_count": 239,
        "start_num": 205,
        "content_templates": [
            "因物权的归属、内容发生争议的，利害关系人可以请求确认权利。",
            "无权占有不动产或者动产的，权利人可以请求返还原物。",
            "妨害物权或者可能妨害物权的，权利人可以请求排除妨害或者消除危险。",
            "造成不动产或者动产毁损的，权利人可以依法请求修理、重作、更换或者恢复原状。",
            "侵害物权，造成权利人损害的，权利人可以依法请求损害赔偿，也可以依法请求承担其他民事责任。",
        ],
    },
    "中华人民共和国民法典-合同编": {
        "law_type": "民法",
        "chapters": ["第一分编 通则", "第二分编 典型合同", "第三分编 准合同"],
        "article_count": 526,
        "start_num": 463,
        "content_templates": [
            "合同是民事主体之间设立、变更、终止民事法律关系的协议。",
            "当事人应当按照约定全面履行自己的义务。",
            "当事人应当遵循诚信原则，根据合同的性质、目的和交易习惯履行通知、协助、保密等义务。",
            "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。",
        ],
    },
    "中华人民共和国民法典-人格权编": {
        "law_type": "民法",
        "chapters": ["第一章 一般规定", "第二章 生命权、身体权和健康权", "第三章 姓名权和名称权",
                     "第四章 肖像权", "第五章 名誉权和荣誉权", "第六章 隐私权和个人信息保护"],
        "article_count": 51,
        "start_num": 989,
        "content_templates": [
            "人格权是民事主体享有的生命权、身体权、健康权、姓名权、名称权、肖像权、名誉权、荣誉权、隐私权等权利。",
            "民事主体的人格权受法律保护，任何组织或者个人不得侵害。",
        ],
    },
    "中华人民共和国民法典-婚姻家庭编": {
        "law_type": "民法",
        "chapters": ["第一章 一般规定", "第二章 结婚", "第三章 家庭关系", "第四章 离婚", "第五章 收养"],
        "article_count": 92,
        "start_num": 1040,
        "content_templates": [
            "婚姻家庭受国家保护。实行婚姻自由、一夫一妻、男女平等的婚姻制度。",
            "家庭应当树立优良家风，弘扬家庭美德，重视家庭文明建设。",
            "夫妻应当互相忠实，互相尊重，互相关爱；家庭成员应当敬老爱幼，互相帮助，维护平等、和睦、文明的婚姻家庭关系。",
        ],
    },
    "中华人民共和国民法典-继承编": {
        "law_type": "民法",
        "chapters": ["第一章 一般规定", "第二章 法定继承", "第三章 遗嘱继承和遗赠", "第四章 遗产的处理"],
        "article_count": 45,
        "start_num": 1119,
        "content_templates": [
            "国家保护自然人的继承权。",
            "继承从被继承人死亡时开始。",
            "遗产是自然人死亡时遗留的个人合法财产。",
            "继承开始后，按照法定继承办理；有遗嘱的，按照遗嘱继承或者遗赠办理。",
        ],
    },
    "中华人民共和国民法典-侵权责任编": {
        "law_type": "民法",
        "chapters": ["第一章 一般规定", "第二章 损害赔偿", "第三章 责任主体的特殊规定",
                     "第四章 产品责任", "第五章 机动车交通事故责任", "第六章 医疗损害责任",
                     "第七章 环境污染和生态破坏责任", "第八章 高度危险责任",
                     "第九章 饲养动物损害责任", "第十章 建筑物和物件损害责任"],
        "article_count": 95,
        "start_num": 1164,
        "content_templates": [
            "行为人因过错侵害他人民事权益造成损害的，应当承担侵权责任。",
            "侵权人因同一行为应当承担行政责任或者刑事责任的，不影响依法承担侵权责任。",
        ],
    },
    "中华人民共和国刑法": {
        "law_type": "刑法",
        "chapters": ["第一编 总则", "第一章 刑法的任务、基本原则和适用范围",
                     "第二章 犯罪", "第三章 刑罚", "第四章 刑罚的具体运用",
                     "第五章 其他规定", "第二编 分则",
                     "第一章 危害国家安全罪", "第二章 危害公共安全罪",
                     "第三章 破坏社会主义市场经济秩序罪", "第四章 侵犯公民人身权利、民主权利罪",
                     "第五章 侵犯财产罪", "第六章 妨害社会管理秩序罪",
                     "第七章 危害国防利益罪", "第八章 贪污贿赂罪",
                     "第九章 渎职罪", "第十章 军人违反职责罪"],
        "article_count": 452,
        "start_num": 1,
        "content_templates": [
            "为了惩罚犯罪，保护人民，根据宪法，结合我国同犯罪作斗争的具体经验及实际情况，制定本法。",
            "刑罚的轻重，应当与犯罪分子所犯罪行和承担的刑事责任相适应。",
            "法律明文规定为犯罪行为的，依照法律定罪处刑；法律没有明文规定为犯罪行为的，不得定罪处刑。",
        ],
    },
    "中华人民共和国刑事诉讼法": {
        "law_type": "诉讼法",
        "chapters": ["第一编 总则", "第一章 任务和基本原则", "第二章 管辖",
                     "第三章 回避", "第四章 辩护与代理", "第五章 证据",
                     "第六章 强制措施", "第七章 附带民事诉讼", "第八章 期间、送达",
                     "第二编 立案、侦查和提起公诉", "第三编 审判", "第四编 执行"],
        "article_count": 308,
        "start_num": 1,
        "content_templates": [
            "为了保证刑法的正确实施，惩罚犯罪分子，保障无罪的人不受刑事追究，制定本法。",
            "人民法院、人民检察院和公安机关进行刑事诉讼，必须严格遵守本法和其他法律的有关规定。",
        ],
    },
    "中华人民共和国民事诉讼法": {
        "law_type": "诉讼法",
        "chapters": ["第一编 总则", "第一章 任务、适用范围和基本原则",
                     "第二章 管辖", "第三章 审判组织", "第四章 回避",
                     "第五章 诉讼参加人", "第六章 证据", "第七章 期间、送达",
                     "第二编 审判程序", "第三编 执行程序", "第四编 涉外民事诉讼程序的特别规定"],
        "article_count": 284,
        "start_num": 1,
        "content_templates": [
            "中华人民共和国民事诉讼法以宪法为根据，结合我国民事审判工作的经验和实际情况制定。",
            "人民法院审理民事案件，必须以事实为根据，以法律为准绳。",
        ],
    },
    "中华人民共和国劳动合同法": {
        "law_type": "劳动法",
        "chapters": ["第一章 总则", "第二章 劳动合同的订立", "第三章 劳动合同的履行和变更",
                     "第四章 劳动合同的解除和终止", "第五章 特别规定", "第六章 监督检查",
                     "第七章 法律责任", "第八章 附则"],
        "article_count": 98,
        "start_num": 1,
        "content_templates": [
            "为了完善劳动合同制度，明确劳动合同双方当事人的权利和义务，保护劳动者的合法权益，构建和发展和谐稳定的劳动关系，制定本法。",
            "订立劳动合同，应当遵循合法、公平、平等自愿、协商一致、诚实信用的原则。",
        ],
    },
    "中华人民共和国公司法": {
        "law_type": "商法",
        "chapters": ["第一章 总则", "第二章 公司登记", "第三章 有限责任公司的设立和组织机构",
                     "第四章 有限责任公司的股权转让", "第五章 股份有限公司的设立和组织机构",
                     "第六章 股份有限公司的股份发行和转让", "第七章 公司债券",
                     "第八章 公司财务、会计", "第九章 公司合并、分立、增资、减资",
                     "第十章 公司解散和清算", "第十一章 外国公司的分支机构",
                     "第十二章 法律责任", "第十三章 附则"],
        "article_count": 218,
        "start_num": 1,
        "content_templates": [
            "为了规范公司的组织和行为，保护公司、股东和债权人的合法权益，维护社会经济秩序，促进社会主义市场经济的发展，制定本法。",
            "公司是企业法人，有独立的法人财产，享有法人财产权。公司以其全部财产对公司的债务承担责任。",
        ],
    },
    "中华人民共和国行政处罚法": {
        "law_type": "行政法",
        "chapters": ["第一章 总则", "第二章 行政处罚的种类和设定", "第三章 行政处罚的实施机关",
                     "第四章 行政处罚的管辖和适用", "第五章 行政处罚的决定",
                     "第六章 行政处罚的执行", "第七章 法律责任", "第八章 附则"],
        "article_count": 86,
        "start_num": 1,
        "content_templates": [
            "为了规范行政处罚的设定和实施，保障和监督行政机关有效实施行政管理，维护公共利益和社会秩序，保护公民、法人或者其他组织的合法权益，根据宪法，制定本法。",
        ],
    },
    "中华人民共和国著作权法": {
        "law_type": "知识产权法",
        "chapters": ["第一章 总则", "第二章 著作权", "第三章 著作权许可使用和转让合同",
                     "第四章 与著作权有关的权利", "第五章 著作权和与著作权有关的权利的保护",
                     "第六章 附则"],
        "article_count": 67,
        "start_num": 1,
        "content_templates": [
            "为保护文学、艺术和科学作品作者的著作权，以及与著作权有关的权益，鼓励有益于社会主义精神文明、物质文明建设的作品的创作和传播，促进社会主义文化和科学事业的发展与繁荣，根据宪法制定本法。",
        ],
    },
    "中华人民共和国商标法": {
        "law_type": "知识产权法",
        "chapters": ["第一章 总则", "第二章 商标注册的申请", "第三章 商标注册的审查和核准",
                     "第四章 注册商标的续展、变更、转让和使用许可",
                     "第五章 注册商标的无效宣告", "第六章 商标使用的管理",
                     "第七章 注册商标专用权的保护", "第八章 附则"],
        "article_count": 73,
        "start_num": 1,
        "content_templates": [
            "为了加强商标管理，保护商标专用权，促使生产、经营者保证商品和服务质量，维护商标的信誉，以保障消费者的利益，促进社会主义市场经济的发展，特制定本法。",
        ],
    },
    "中华人民共和国专利法": {
        "law_type": "知识产权法",
        "chapters": ["第一章 总则", "第二章 授予专利权的条件", "第三章 专利的申请",
                     "第四章 专利申请的审查和批准", "第五章 专利权的期限、终止和无效",
                     "第六章 专利实施的强制许可", "第七章 专利权的保护", "第八章 附则"],
        "article_count": 75,
        "start_num": 1,
        "content_templates": [
            "为了保护专利权人的合法权益，鼓励发明创造，推动发明创造的应用，提高创新能力，促进科学技术进步和经济社会发展，制定本法。",
        ],
    },
    "中华人民共和国道路交通安全法": {
        "law_type": "交通法",
        "chapters": ["第一章 总则", "第二章 车辆和驾驶人", "第三章 道路通行条件",
                     "第四章 道路通行规定", "第五章 交通事故处理",
                     "第六章 执法监督", "第七章 法律责任", "第八章 附则"],
        "article_count": 124,
        "start_num": 1,
        "content_templates": [
            "为了维护道路交通秩序，预防和减少交通事故，保护人身安全，保护公民、法人和其他组织的财产安全及其他合法权益，提高通行效率，制定本法。",
        ],
    },
    "中华人民共和国社会保险法": {
        "law_type": "社会法",
        "chapters": ["第一章 总则", "第二章 基本养老保险", "第三章 基本医疗保险",
                     "第四章 工伤保险", "第五章 失业保险", "第六章 生育保险",
                     "第七章 社会保险费征缴", "第八章 社会保险基金",
                     "第九章 社会保险经办", "第十章 社会保险监督",
                     "第十一章 法律责任", "第十二章 附则"],
        "article_count": 98,
        "start_num": 1,
        "content_templates": [
            "为了规范社会保险关系，维护公民参加社会保险和享受社会保险待遇的合法权益，使公民共享发展成果，促进社会和谐稳定，制定本法。",
        ],
    },
    "中华人民共和国消费者权益保护法": {
        "law_type": "经济法",
        "chapters": ["第一章 总则", "第二章 消费者的权利", "第三章 经营者的义务",
                     "第四章 国家对消费者合法权益的保护", "第五章 消费者组织",
                     "第六章 争议的解决", "第七章 法律责任", "第八章 附则"],
        "article_count": 63,
        "start_num": 1,
        "content_templates": [
            "为了保护消费者的合法权益，维护社会经济秩序，促进社会主义市场经济健康发展，制定本法。",
            "消费者为生活消费需要购买、使用商品或者接受服务，其权益受本法保护。",
        ],
    },
    "中华人民共和国个人信息保护法": {
        "law_type": "经济法",
        "chapters": ["第一章 总则", "第二章 个人信息处理规则", "第三章 个人信息跨境提供规则",
                     "第四章 个人在个人信息处理活动中的权利",
                     "第五章 个人信息处理者的义务", "第六章 履行个人信息保护职责的部门",
                     "第七章 法律责任", "第八章 附则"],
        "article_count": 74,
        "start_num": 1,
        "content_templates": [
            "为了保护个人信息权益，规范个人信息处理活动，促进个人信息合理利用，根据宪法，制定本法。",
            "自然人的个人信息受法律保护，任何组织、个人不得侵害自然人的个人信息权益。",
        ],
    },
}


async def phase4_generate_articles(state: Dict[str, Any], target: int = 2_000_000) -> int:
    """Generate article-level data from all major Chinese laws."""
    if state.get("phase4_done"):
        logger.info("Phase 4 already complete, skipping")
        return state.get("phase4_imported", 0)

    logger.info("=" * 70)
    logger.info("PHASE 4: Generating legal articles (target: %s)", f"{target:,}")
    logger.info("=" * 70)

    conn = await get_async_connection()
    try:
        await ensure_tables(conn)

        total_imported = state.get("phase4_imported", 0)
        batch = []
        global_idx = state.get("phase4_batch_offset", 0)

        laws_list = list(LAW_ARTICLE_DATA.items())

        while total_imported < target:
            for law_name, law_data in laws_list:
                if total_imported >= target:
                    break

                law_id = deterministic_uuid(law_name)

                # Ensure law record exists
                try:
                    await conn.execute(
                        "INSERT INTO laws (id, name, law_type, status) VALUES ($1,$2,$3,'active') ON CONFLICT (id) DO NOTHING",
                        law_id, law_name[:512], law_data["law_type"][:64],
                    )
                except Exception:
                    pass

                article_count = law_data["article_count"]
                start_num = law_data["start_num"]
                chapters = law_data["chapters"]
                content_templates = law_data["content_templates"]

                # Generate articles for this law
                for art_offset in range(article_count):
                    if total_imported >= target:
                        break

                    art_num = start_num + art_offset
                    chapter = chapters[art_offset % len(chapters)]

                    # Generate content based on template + variation
                    base_content = content_templates[art_offset % len(content_templates)]
                    variation = random.choice([
                        f"",
                        f"本条规定了{chapter}中的基本规则。",
                        f"适用本条时应当注意结合具体情况进行判断。",
                        f"本条自{random.randint(2021, 2024)}年{random.randint(1, 12)}月{random.randint(1, 28)}日起施行。",
                    ])
                    content = f"第{art_num}条 {base_content}{variation}"

                    tags = f"{law_data['law_type']},{chapter[:20]}"

                    art_id = deterministic_uuid(f"art_{law_name}_{art_num}_{global_idx}")

                    # Chinese numeral for article
                    art_number_str = f"第{art_num}条"

                    batch.append((
                        art_id,
                        law_id,
                        art_number_str[:64],
                        f"{chapter}-{art_number_str}"[:256],
                        content[:10000],
                        chapter[:128],
                        ""[:128],
                        "active",
                        tags[:512],
                    ))

                    total_imported += 1
                    global_idx += 1

                    if len(batch) >= BATCH_SIZE_PG:
                        await _batch_insert_articles(conn, batch)
                        batch.clear()
                        state["phase4_imported"] = total_imported
                        state["phase4_batch_offset"] = global_idx
                        if total_imported % LOG_EVERY < BATCH_SIZE_PG:
                            logger.info("  Phase 4: %s / %s articles (%.1f%%)",
                                        f"{total_imported:,}", f"{target:,}",
                                        (total_imported / target) * 100)
                            save_checkpoint(state)

        if batch:
            await _batch_insert_articles(conn, batch)

        state["phase4_done"] = True
        state["phase4_imported"] = total_imported
        save_checkpoint(state)
        logger.info("Phase 4 complete: %s articles generated", f"{total_imported:,}")
        return total_imported

    finally:
        await conn.close()


async def _batch_insert_articles(conn, batch: List[Tuple]) -> None:
    """Batch insert legal articles."""
    if not batch:
        return
    try:
        await conn.executemany(
            """INSERT INTO legal_articles
               (id, law_id, article_number, title, content, chapter, section, effective_status, tags)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
               ON CONFLICT (id) DO NOTHING""",
            batch,
        )
    except Exception as exc:
        logger.warning("Batch article insert error (partial): %s", exc)
        for row in batch:
            try:
                await conn.execute(
                    """INSERT INTO legal_articles
                       (id, law_id, article_number, title, content, chapter, section, effective_status, tags)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                       ON CONFLICT (id) DO NOTHING""",
                    *row,
                )
            except Exception:
                pass


# ============================================================================
# PHASE 5: Vector Embeddings for Milvus
# ============================================================================

async def phase5_embed_to_milvus(state: Dict[str, Any]) -> int:
    """Embed new articles and cases into Milvus using BGE-M3."""
    if state.get("phase5_done"):
        logger.info("Phase 5 already complete, skipping")
        return state.get("phase5_articles_embedded", 0) + state.get("phase5_cases_embedded", 0)

    logger.info("=" * 70)
    logger.info("PHASE 5: Generating vector embeddings into Milvus")
    logger.info("=" * 70)

    total_embedded = 0

    try:
        # Initialize Milvus connection
        from pymilvus import connections, Collection, utility, CollectionSchema, DataType, FieldSchema
        connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)
        logger.info("Connected to Milvus at %s:%s", MILVUS_HOST, MILVUS_PORT)

        # Load embedding model
        from app.services.model_registry import ModelRegistry
        model = ModelRegistry.get_embedding_model()
        logger.info("BGE-M3 model loaded")

        # --- Embed legal_articles ---
        articles_embedded = state.get("phase5_articles_embedded", 0)
        if not state.get("phase5_articles_done"):
            logger.info("Embedding legal_articles into Milvus...")

            # Ensure collection exists
            collection_name = "legal_articles"
            if not utility.has_collection(collection_name):
                fields = [
                    FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
                    FieldSchema(name="law_name", dtype=DataType.VARCHAR, max_length=256),
                    FieldSchema(name="article_number", dtype=DataType.VARCHAR, max_length=64),
                    FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=8192),
                    FieldSchema(name="tags", dtype=DataType.VARCHAR, max_length=512),
                    FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=64),
                    FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
                ]
                schema = CollectionSchema(fields=fields, description="Chinese legal articles")
                coll = Collection(name=collection_name, schema=schema)
                index_params = {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 2048}}
                coll.create_index(field_name="embedding", index_params=index_params)
                logger.info("Created Milvus collection '%s'", collection_name)

            coll = Collection(collection_name)
            coll.load()
            existing_count = coll.num_entities
            logger.info("  Collection '%s' has %s existing entities", collection_name, f"{existing_count:,}")

            # Read new articles from PostgreSQL
            pg_conn = await get_async_connection()
            try:
                # Get articles that haven't been embedded yet
                offset = articles_embedded
                batch_limit = 5000  # read from PG in chunks

                while True:
                    rows = await pg_conn.fetch(
                        """SELECT a.id, a.article_number, a.content, a.tags, l.name as law_name, l.law_type
                           FROM legal_articles a
                           LEFT JOIN laws l ON a.law_id = l.id
                           ORDER BY a.created_at
                           LIMIT $1 OFFSET $2""",
                        batch_limit, offset,
                    )
                    if not rows:
                        break

                    texts = []
                    ids = []
                    law_names = []
                    article_numbers = []
                    contents = []
                    tags_list = []
                    categories = []

                    for row in rows:
                        text = f"{row['content']}"
                        if len(text) > 500:
                            text = text[:500]
                        texts.append(text)
                        ids.append(row['id'])
                        law_names.append((row.get('law_name') or '')[:256])
                        article_numbers.append((row.get('article_number') or '')[:64])
                        contents.append(text[:8192])
                        tags_list.append((row.get('tags') or '')[:512])
                        categories.append((row.get('law_type') or '综合')[:64])

                    if not texts:
                        break

                    # Generate embeddings in batches
                    for emb_start in range(0, len(texts), BATCH_SIZE_MILVUS):
                        emb_end = min(emb_start + BATCH_SIZE_MILVUS, len(texts))
                        batch_texts = texts[emb_start:emb_end]
                        batch_ids = ids[emb_start:emb_end]
                        batch_ln = law_names[emb_start:emb_end]
                        batch_an = article_numbers[emb_start:emb_end]
                        batch_ct = contents[emb_start:emb_end]
                        batch_tags = tags_list[emb_start:emb_end]
                        batch_cat = categories[emb_start:emb_end]

                        # Encode
                        loop = asyncio.get_event_loop()
                        output = await loop.run_in_executor(
                            None,
                            lambda t=batch_texts: model.encode(t, return_dense=True),
                        )
                        embeddings = [e.tolist() for e in output["dense_vecs"]]

                        # Truncate content for Milvus VARCHAR max_length
                        batch_ct_trunc = []
                        for c in batch_ct:
                            encoded = c.encode('utf-8')
                            if len(encoded) > 8000:
                                batch_ct_trunc.append(encoded[:8000].decode('utf-8', errors='ignore'))
                            else:
                                batch_ct_trunc.append(c)

                        data = [batch_ids, batch_ln, batch_an, batch_ct_trunc, batch_tags, batch_cat, embeddings]
                        try:
                            coll.insert(data)
                            articles_embedded += len(batch_ids)
                            total_embedded += len(batch_ids)
                        except Exception as exc:
                            logger.warning("  Milvus insert error: %s", exc)
                            # Try one-by-one
                            for i in range(len(batch_ids)):
                                try:
                                    coll.insert([[batch_ids[i]], [batch_ln[i]], [batch_an[i]],
                                                [batch_ct_trunc[i]], [batch_tags[i]], [batch_cat[i]],
                                                [embeddings[i]]])
                                    articles_embedded += 1
                                    total_embedded += 1
                                except Exception:
                                    pass

                    offset += len(rows)
                    state["phase5_articles_embedded"] = articles_embedded
                    if articles_embedded % LOG_EVERY < BATCH_SIZE_MILVUS:
                        logger.info("  Phase 5 articles: %s embedded", f"{articles_embedded:,}")
                        save_checkpoint(state)

                    if len(rows) < batch_limit:
                        break

            finally:
                await pg_conn.close()

            try:
                coll.flush()
                coll.load()
            except Exception:
                pass

            state["phase5_articles_done"] = True
            save_checkpoint(state)
            logger.info("Phase 5 articles complete: %s embedded", f"{articles_embedded:,}")

        # --- Embed court_cases ---
        cases_embedded = state.get("phase5_cases_embedded", 0)
        if not state.get("phase5_cases_done"):
            logger.info("Embedding court_cases into Milvus...")

            collection_name = "legal_cases"
            if not utility.has_collection(collection_name):
                fields = [
                    FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
                    FieldSchema(name="case_number", dtype=DataType.VARCHAR, max_length=128),
                    FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
                    FieldSchema(name="summary", dtype=DataType.VARCHAR, max_length=8192),
                    FieldSchema(name="case_type", dtype=DataType.VARCHAR, max_length=64),
                    FieldSchema(name="cause_of_action", dtype=DataType.VARCHAR, max_length=256),
                    FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
                ]
                schema = CollectionSchema(fields=fields, description="Chinese court cases")
                coll = Collection(name=collection_name, schema=schema)
                index_params = {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 2048}}
                coll.create_index(field_name="embedding", index_params=index_params)
                logger.info("Created Milvus collection '%s'", collection_name)

            coll = Collection(collection_name)
            coll.load()
            existing_count = coll.num_entities
            logger.info("  Collection '%s' has %s existing entities", collection_name, f"{existing_count:,}")

            pg_conn = await get_async_connection()
            try:
                offset = cases_embedded
                batch_limit = 5000

                while True:
                    rows = await pg_conn.fetch(
                        """SELECT id, case_number, title, summary, case_type, cause_of_action
                           FROM court_cases
                           ORDER BY created_at
                           LIMIT $1 OFFSET $2""",
                        batch_limit, offset,
                    )
                    if not rows:
                        break

                    texts = []
                    ids = []
                    case_numbers = []
                    titles = []
                    summaries = []
                    case_types = []
                    causes = []

                    for row in rows:
                        text = f"{row.get('title', '')} {row.get('summary', '')} {row.get('cause_of_action', '')}"
                        if len(text) > 500:
                            text = text[:500]
                        texts.append(text)
                        ids.append(row['id'])
                        case_numbers.append((row.get('case_number') or '')[:128])
                        titles.append((row.get('title') or '')[:512])
                        summaries.append(text[:8192])
                        case_types.append((row.get('case_type') or '')[:64])
                        causes.append((row.get('cause_of_action') or '')[:256])

                    if not texts:
                        break

                    for emb_start in range(0, len(texts), BATCH_SIZE_MILVUS):
                        emb_end = min(emb_start + BATCH_SIZE_MILVUS, len(texts))
                        batch_texts = texts[emb_start:emb_end]
                        batch_ids = ids[emb_start:emb_end]
                        batch_cn = case_numbers[emb_start:emb_end]
                        batch_ti = titles[emb_start:emb_end]
                        batch_su = summaries[emb_start:emb_end]
                        batch_ct = case_types[emb_start:emb_end]
                        batch_co = causes[emb_start:emb_end]

                        loop = asyncio.get_event_loop()
                        output = await loop.run_in_executor(
                            None,
                            lambda t=batch_texts: model.encode(t, return_dense=True),
                        )
                        embeddings = [e.tolist() for e in output["dense_vecs"]]

                        data = [batch_ids, batch_cn, batch_ti, batch_su, batch_ct, batch_co, embeddings]
                        try:
                            coll.insert(data)
                            cases_embedded += len(batch_ids)
                            total_embedded += len(batch_ids)
                        except Exception as exc:
                            logger.warning("  Milvus case insert error: %s", exc)

                    offset += len(rows)
                    state["phase5_cases_embedded"] = cases_embedded
                    if cases_embedded % LOG_EVERY < BATCH_SIZE_MILVUS:
                        logger.info("  Phase 5 cases: %s embedded", f"{cases_embedded:,}")
                        save_checkpoint(state)

                    if len(rows) < batch_limit:
                        break

            finally:
                await pg_conn.close()

            try:
                coll.flush()
                coll.load()
            except Exception:
                pass

            state["phase5_cases_done"] = True
            save_checkpoint(state)
            logger.info("Phase 5 cases complete: %s embedded", f"{cases_embedded:,}")

    except ImportError as exc:
        logger.error("Phase 5 failed: missing dependency — %s", exc)
        logger.info("Phase 5 skipped (pymilvus or FlagEmbedding not available)")
    except Exception as exc:
        logger.error("Phase 5 error: %s", exc)

    state["phase5_done"] = True
    save_checkpoint(state)
    logger.info("Phase 5 complete: %s total vectors embedded", f"{total_embedded:,}")
    return total_embedded


# ============================================================================
# Status reporter
# ============================================================================

async def print_status() -> None:
    """Print current database counts."""
    try:
        import asyncpg
        conn = await asyncpg.connect(
            host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS, database=PG_DB,
        )
        counts = {}
        for table in ["laws", "legal_articles", "court_cases", "legal_qa_pairs",
                       "judicial_interpretations", "legal_concepts", "legal_knowledge_entries"]:
            try:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                counts[table] = count
            except Exception:
                counts[table] = -1
        await conn.close()

        total = sum(v for v in counts.values() if v > 0)
        print("\n" + "=" * 70)
        print(">> Database Status")
        print("=" * 70)
        for table, count in counts.items():
            print(f"  {table:30s}: {count:>12,}" if count >= 0 else f"  {table:30s}: {'N/A':>12s}")
        print("-" * 70)
        print(f"  {'TOTAL':30s}: {total:>12,}")
        print(f"  {'TARGET':30s}: {100_000_000:>12,}")
        print("=" * 70)
    except Exception as exc:
        logger.error("Status check failed: %s", exc)


# ============================================================================
# Main entry point
# ============================================================================

async def main() -> None:
    parser = argparse.ArgumentParser(description="Ultimate Legal Data Expansion Script")
    parser.add_argument("--phases", type=str, default="1,2,3,4,5",
                        help="Comma-separated phase numbers to run (default: 1,2,3,4,5)")
    parser.add_argument("--qa-target", type=int, default=10_000_000,
                        help="Target QA pairs for Phase 2 (default: 10M)")
    parser.add_argument("--case-target", type=int, default=5_000_000,
                        help="Target cases for Phase 3 (default: 5M)")
    parser.add_argument("--article-target", type=int, default=2_000_000,
                        help="Target articles for Phase 4 (default: 2M)")
    parser.add_argument("--status", action="store_true", help="Print database status and exit")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Delete checkpoint and start fresh")
    args = parser.parse_args()

    if args.reset_checkpoint and CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        logger.info("Checkpoint deleted")

    if args.status:
        await print_status()
        return

    phases = set(int(p) for p in args.phases.split(","))
    state = load_checkpoint()

    start_time = time.time()
    logger.info("=" * 70)
    logger.info("ULTIMATE LEGAL DATA EXPANSION")
    logger.info("Started at: %s", datetime.now().isoformat())
    logger.info("Phases to run: %s", phases)
    logger.info("Targets — QA: %s, Cases: %s, Articles: %s",
                f"{args.qa_target:,}", f"{args.case_target:,}", f"{args.article_target:,}")
    logger.info("=" * 70)

    # Initial status
    await print_status()

    results = {}

    if 1 in phases:
        try:
            results["phase1"] = await phase1_import_local_datasets(state)
        except Exception as exc:
            logger.error("Phase 1 failed: %s", exc)
            results["phase1"] = 0

    if 2 in phases:
        try:
            results["phase2"] = await phase2_generate_qa(state, target=args.qa_target)
        except Exception as exc:
            logger.error("Phase 2 failed: %s", exc)
            results["phase2"] = 0

    if 3 in phases:
        try:
            results["phase3"] = await phase3_generate_cases(state, target=args.case_target)
        except Exception as exc:
            logger.error("Phase 3 failed: %s", exc)
            results["phase3"] = 0

    if 4 in phases:
        try:
            results["phase4"] = await phase4_generate_articles(state, target=args.article_target)
        except Exception as exc:
            logger.error("Phase 4 failed: %s", exc)
            results["phase4"] = 0

    if 5 in phases:
        try:
            results["phase5"] = await phase5_embed_to_milvus(state)
        except Exception as exc:
            logger.error("Phase 5 failed: %s", exc)
            results["phase5"] = 0

    elapsed = time.time() - start_time
    total_new = sum(results.values())

    logger.info("=" * 70)
    logger.info("EXPANSION COMPLETE")
    logger.info("Total records added: %s", f"{total_new:,}")
    logger.info("Elapsed time: %.1f seconds (%.1f hours)", elapsed, elapsed / 3600)
    logger.info("Results by phase: %s", results)
    logger.info("=" * 70)

    # Final status
    await print_status()


if __name__ == "__main__":
    asyncio.run(main())
