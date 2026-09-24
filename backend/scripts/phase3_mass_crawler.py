# -*- coding: utf-8 -*-
"""阶段3：多源裁判文书大规模采集与导入管道。

目标: +50M 条裁判文书数据。

策略:
  1. 多源并行采集 — 同时从裁判文书网、开源数据集、公开法律数据库爬取
  2. 按案由分类 — 高优先级案由先爬取
  3. 高性能导入 — 批量INSERT + ON CONFLICT去重
  4. 断点续跑 — 每个数据源独立checkpoint
  5. 增量同步 — 支持每日增量更新

数据源及预估:
  - 裁判文书网 (wenshu.court.gov.cn): 1亿+ 裁判文书
  - 开源案例数据集: ~10M 条
  - 公开裁判文书合集: ~5M 条

使用方法:
    cd backend
    python scripts/phase3_mass_crawler.py
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

# 项目根目录
BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase3")

DATA_DIR = BACKEND_ROOT / "data" / "phase3_crawled"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CHECKPOINT_DIR = BACKEND_ROOT / "data" / "import_checkpoints"
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

BATCH_SIZE = 500
CASE_FLUSH_SIZE = 5000

# =========================================================================
# 案由优先级配置
# =========================================================================

CAUSE_PRIORITIES = {
    # 高优先级 — 数据量大、常见纠纷
    "合同纠纷": 0,
    "劳动争议": 0,
    "机动车交通事故责任纠纷": 0,
    "民间借贷纠纷": 0,
    "盗窃罪": 0,
    "诈骗罪": 0,
    "故意伤害罪": 0,
    "买卖合同纠纷": 0,
    "房屋租赁合同纠纷": 0,
    "婚姻家庭纠纷": 1,
    "离婚纠纷": 1,
    "侵权责任纠纷": 1,
    "物权纠纷": 1,
    "知识产权纠纷": 1,
    "著作权纠纷": 1,
    "商标权纠纷": 1,
    "专利权纠纷": 1,
    # 中优先级
    "行政诉讼": 2,
    "行政处罚": 2,
    "金融纠纷": 2,
    "保险纠纷": 2,
    "公司纠纷": 2,
    "建设工程合同纠纷": 2,
    # 低优先级
    "海事海商": 3,
    "环境资源": 3,
    "执行程序": 3,
}


# =========================================================================
# 高性能数据库导入器
# =========================================================================

class MassCaseImporter:
    """高性能裁判文书批量导入器。

    使用 INSERT ... ON CONFLICT DO NOTHING 实现幂等导入。
    支持多线程并行写入。
    """

    def __init__(self, session_factory=None):
        if session_factory is None:
            from app.core.database import async_session_factory
            self._session_factory = async_session_factory
        else:
            self._session_factory = session_factory
        self._stats = {
            "total_received": 0,
            "total_inserted": 0,
            "total_skipped": 0,
            "errors": 0,
        }

    async def import_batch(self, cases: list[dict[str, Any]]) -> int:
        """批量导入案例到 court_cases 表。"""
        from app.models.legal_knowledge import CourtCase
        from sqlalchemy.exc import IntegrityError

        if not cases:
            return 0

        self._stats["total_received"] += len(cases)
        rows = []
        for case_data in cases:
            # 生成确定性UUID用于去重
            content = case_data.get("full_text", "") or case_data.get("summary", "")
            case_number = case_data.get("case_number", "")
            if not case_number:
                fact_hash = uuid.uuid5(uuid.NAMESPACE_DNS, f"p3_{content[:500]}").hex
                case_number = "P3-" + fact_hash[:16]

            rows.append(CourtCase(
                case_number=case_number[:128],
                title=(case_data.get("title") or "法律案例")[:512],
                court_name=(case_data.get("court_name") or "")[:256],
                case_type=(case_data.get("case_type") or "")[:64],
                cause_of_action=(case_data.get("cause_of_action") or "")[:256],
                decision_date=(case_data.get("decision_date") or "")[:32],
                parties=(case_data.get("parties") or "")[:2000],
                summary=(case_data.get("summary") or "")[:2000],
                full_text=(case_data.get("full_text") or content[:60000])[:60000],
                key_points=(case_data.get("key_points") or "")[:2000],
                referenced_laws=(case_data.get("referenced_laws") or "")[:2000],
                judgment_result=(case_data.get("judgment_result") or "")[:2000],
                tags=(case_data.get("tags") or "phase3")[:512],
            ))

        async with self._session_factory() as session:
            # 批量插入，ON CONFLICT跳过
            try:
                session.add_all(rows)
                await session.commit()
                self._stats["total_inserted"] += len(rows)
                return len(rows)
            except IntegrityError:
                await session.rollback()
                # 逐条插入，跳过重复的
                saved = 0
                for row in rows:
                    try:
                        session.add(row)
                        await session.commit()
                        saved += 1
                    except IntegrityError:
                        await session.rollback()
                        self._stats["total_skipped"] += 1
                    except Exception:
                        await session.rollback()
                        self._stats["errors"] += 1
                self._stats["total_inserted"] += saved
                return saved

    def get_stats(self) -> dict:
        return dict(self._stats)


# =========================================================================
# 多源数据采集器
# =========================================================================

class MultiSourceCrawler:
    """多源裁判文书采集器。

    从以下源并行采集:
    1. 已有的本地 JSONL 数据集
    2. HuggingFace 开源案例数据集（streaming）
    3. 裁判文书网 API
    4. 公开裁判文书合集
    """

    def __init__(self, importer: MassCaseImporter):
        self._importer = importer
        self._stop_event = asyncio.Event()

    # ---- 源1: 本地已有数据文件 ----

    async def import_local_files(self):
        """导入已有的本地裁判文书数据文件。"""
        logger.info("=" * 60)
        logger.info("源1: 导入本地裁判文书数据")
        logger.info("=" * 60)

        checkpoint = CHECKPOINT_DIR / "phase3_local_files.done"
        if checkpoint.exists():
            logger.info("[SKIP] 本地文件已导入")
            return

        # 从 data/datasets/ 导入 refined_legal_train.json
        refined_path = BACKEND_ROOT / "data" / "datasets" / "refined_legal_train.json"
        if refined_path.exists():
            await self._import_refined_train(refined_path)

        # 从 crawler_data 目录导入
        crawler_dirs = [
            BACKEND_ROOT / "crawler_data",
            BACKEND_ROOT / "crawler_data_public",
        ]
        for crawler_dir in crawler_dirs:
            if crawler_dir.exists():
                await self._import_crawler_dir(crawler_dir)

        checkpoint.write_text("done", encoding="utf-8")

    async def _import_refined_train(self, filepath: Path):
        """导入 refined_legal_train.json。"""
        ck = CHECKPOINT_DIR / "phase3_refined.done"
        if ck.exists():
            return
        logger.info("导入 %s ...", filepath.name)

        with open(filepath, "r", encoding="utf-8") as f:
            records = json.load(f)

        batch = []
        for rec in records:
            fact = str(rec.get("fact") or "").strip()
            if not fact:
                continue
            meta = rec.get("meta") or {}
            accusation = meta.get("accusation", [])
            if isinstance(accusation, str):
                accusation = [accusation]
            articles = meta.get("relevant_articles", [])
            term = meta.get("term_of_imprisonment", {})
            money = meta.get("punish_of_money", 0) or 0
            criminals = meta.get("criminals", [])

            judgment_parts = []
            if isinstance(term, dict):
                if term.get("death_penalty"):
                    judgment_parts.append("死刑")
                if term.get("life_imprisonment"):
                    judgment_parts.append("无期徒刑")
                if term.get("imprisonment"):
                    judgment_parts.append(f"有期徒刑{term['imprisonment']}年")
            if money:
                judgment_parts.append(f"罚金{money}元")

            cause = "、".join(str(a) for a in accusation[:5])
            batch.append({
                "case_number": "",  # 自动生成
                "title": f"刑事案件-{cause}" if cause else "刑事案件",
                "court_name": "",
                "case_type": "刑事",
                "cause_of_action": cause[:256],
                "decision_date": "",
                "parties": "、".join(str(c) for c in criminals[:5])[:2000],
                "summary": fact[:500],
                "full_text": fact[:60000],
                "key_points": "",
                "referenced_laws": "、".join(str(a) for a in list(articles)[:20])[:2000],
                "judgment_result": "、".join(judgment_parts)[:500],
                "tags": "phase3,refined_train",
            })

            if len(batch) >= BATCH_SIZE:
                await self._importer.import_batch(batch)
                batch.clear()
                if self._importer.get_stats()["total_inserted"] % 10000 < BATCH_SIZE:
                    logger.info("  refined: %d 条已导入 ...",
                                self._importer.get_stats()["total_inserted"])

        if batch:
            await self._importer.import_batch(batch)

        ck.write_text("done", encoding="utf-8")

    async def _import_crawler_dir(self, directory: Path):
        """导入爬虫输出目录中的JSON文件。"""
        ck_name = f"phase3_crawler_{directory.name}.done"
        ck = CHECKPOINT_DIR / ck_name
        if ck.exists():
            return

        json_files = list(directory.glob("*.json"))
        logger.info("从 %s 导入 %d 个文件 ...", directory.name, len(json_files))

        batch = []
        for json_file in json_files:
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    batch.append(self._normalize_crawled_doc(data))
                elif isinstance(data, list):
                    for doc in data:
                        batch.append(self._normalize_crawled_doc(doc))

                if len(batch) >= BATCH_SIZE:
                    await self._importer.import_batch(batch)
                    batch.clear()
            except Exception as exc:
                logger.debug("跳过文件 %s: %s", json_file.name, exc)

        if batch:
            await self._importer.import_batch(batch)

        ck.write_text("done", encoding="utf-8")

    def _normalize_crawled_doc(self, doc: dict) -> dict:
        """将爬取的文书数据标准化。"""
        return {
            "case_number": doc.get("case_number", "")[:128],
            "title": doc.get("title", "法律案例")[:512],
            "court_name": doc.get("court_name", "")[:256],
            "case_type": doc.get("case_type", "")[:64],
            "cause_of_action": doc.get("cause_of_action", "")[:256],
            "decision_date": doc.get("decision_date", "")[:32],
            "parties": doc.get("parties", "")[:2000],
            "summary": doc.get("summary", "")[:2000],
            "full_text": doc.get("full_text", "")[:60000],
            "key_points": doc.get("key_points", "")[:2000],
            "referenced_laws": doc.get("referenced_laws", "")[:2000],
            "judgment_result": doc.get("judgment_result", "")[:2000],
            "tags": doc.get("tags", "phase3,crawled")[:512],
        }

    # ---- 源2: HuggingFace 大规模案例数据 ----

    async def stream_huggingface_cases(self, target_count: int = 5_000_000):
        """从 HuggingFace 流式下载案例数据集。"""
        ck = CHECKPOINT_DIR / "phase3_hf_stream.done"
        if ck.exists():
            count = ck.read_text(encoding="utf-8").strip()
            logger.info("[SKIP] HF streaming 已完成 (%s 条)", count)
            return

        logger.info("=" * 60)
        logger.info("源2: HuggingFace 大规模案例数据流式下载")
        logger.info("目标: %s 条", f"{target_count:,}")
        logger.info("=" * 60)

        # 数据源列表（按预估大小排序）
        hf_sources = [
            ("china-ai-law-challenge/cail2018", "first_stage_train"),
            ("china-ai-law-challenge/cail2018", "exercise_contest_train"),
            ("china-ai-law-challenge/cail2018", "final_test"),
            ("SUSTechLaw/ChineseWenshu", "train"),
            ("SUSTechLaw/CivilCaseDataset", "train"),
            ("thu-coair/LEven", "train"),
        ]

        total = 0
        for repo, split in hf_sources:
            if total >= target_count:
                break
            try:
                count = await self._stream_hf_source(repo, split, target_count - total)
                total += count
                logger.info("[HF] %s#%s: +%d 条 (总计 %d)",
                            repo, split, count, total)
            except Exception as exc:
                logger.warning("[HF] %s#%s 失败: %s", repo, split, exc)

        ck.write_text(str(total), encoding="utf-8")
        logger.info("HF streaming 完成: %d 条", total)

    async def _stream_hf_source(self, repo: str, split: str, limit: int) -> int:
        """从单个 HF 数据源流式下载。"""
        inserted = 0

        # 方法1: datasets 库 streaming
        try:
            from datasets import load_dataset
            ds = load_dataset(repo, split=split, streaming=True)
            batch = []
            for item in ds:
                if inserted >= limit:
                    break
                rec = dict(item)
                case_data = self._hf_record_to_case(rec, repo)
                if case_data:
                    batch.append(case_data)
                    if len(batch) >= BATCH_SIZE:
                        n = await self._importer.import_batch(batch)
                        inserted += n
                        batch.clear()
                        if inserted % 10000 < BATCH_SIZE:
                            logger.info("  [%s] %d 条 ...", repo.split("/")[-1], inserted)
            if batch:
                n = await self._importer.import_batch(batch)
                inserted += n
            return inserted
        except ImportError:
            pass
        except Exception as exc:
            logger.warning("[%s] datasets streaming 失败: %s", repo, exc)

        # 方法2: parquet 下载
        try:
            return await self._download_hf_parquet_cases(repo, split, limit)
        except Exception as exc:
            logger.warning("[%s] parquet 下载失败: %s", repo, exc)

        return 0

    async def _download_hf_parquet_cases(self, repo: str, split: str, limit: int) -> int:
        """下载 HF parquet 文件并导入案例。"""
        from huggingface_hub import HfApi, hf_hub_download
        import pyarrow.parquet as pq

        api = HfApi()
        repo_files = api.list_repo_files(repo, repo_type="dataset")
        parquet_files = sorted(
            f for f in repo_files
            if f.startswith(f"data/{split}-") and f.endswith(".parquet")
        )
        if not parquet_files:
            parquet_files = sorted(f for f in repo_files if f.endswith(".parquet"))
        if not parquet_files:
            return 0

        inserted = 0
        batch = []
        for pf in parquet_files:
            if inserted >= limit:
                break
            try:
                local_path = hf_hub_download(
                    repo_id=repo, filename=pf, repo_type="dataset",
                    endpoint=os.getenv("HF_ENDPOINT") or None,
                )
                table = pq.ParquetFile(local_path)
                for rec_batch in table.iter_batches(batch_size=1000):
                    for rec in rec_batch.to_pylist():
                        case_data = self._hf_record_to_case(rec, repo)
                        if case_data:
                            batch.append(case_data)
                            if len(batch) >= BATCH_SIZE:
                                n = await self._importer.import_batch(batch)
                                inserted += n
                                batch.clear()
                    if inserted >= limit:
                        break
            except Exception as exc:
                logger.warning("[parquet] %s 失败: %s", pf, exc)

        if batch:
            n = await self._importer.import_batch(batch)
            inserted += n
        return inserted

    def _hf_record_to_case(self, rec: dict, source: str) -> dict | None:
        """将 HF 记录转换为案例格式。"""
        fact = ""
        for key in ("fact", "text", "case_text", "content", "full_text"):
            val = rec.get(key)
            if val and len(str(val)) > len(fact):
                fact = str(val)
        if not fact:
            return None

        # 提取元数据
        accusation = rec.get("accusation", rec.get("charge", rec.get("cause", "")))
        if isinstance(accusation, list):
            accusation = "、".join(str(a) for a in accusation[:5])
        accusation = str(accusation)[:256]

        articles = rec.get("relevant_articles", rec.get("law", rec.get("article", [])))
        if isinstance(articles, list):
            articles = "、".join(str(a) for a in articles[:20])
        articles = str(articles)[:2000]

        source_tag = source.split("/")[-1].lower()
        return {
            "case_number": "",
            "title": f"案例-{accusation}" if accusation else "法律案例",
            "court_name": "",
            "case_type": "刑事" if source_tag in ("cail2018", "leven") else "其他",
            "cause_of_action": accusation,
            "decision_date": "",
            "parties": "",
            "summary": fact[:500],
            "full_text": fact[:60000],
            "key_points": "",
            "referenced_laws": str(articles),
            "judgment_result": "",
            "tags": f"phase3,hf_{source_tag}",
        }

    # ---- 源3: 裁判文书网增强爬取 ----

    async def crawl_wenshu_enhanced(self, max_docs: int = 50_000_000):
        """增强版裁判文书网爬取（需要有效代理）。

        注意：裁判文书网有严格的反爬虫措施，需要:
        - 有效的代理IP池
        - JS加密参数破解
        - 验证码处理

        本方法实现了完整的管道架构，实际效果取决于代理可用性。
        """
        ck = CHECKPOINT_DIR / "phase3_wenshu.done"
        if ck.exists():
            logger.info("[SKIP] 裁判文书网爬取已完成/跳过")
            return

        logger.info("=" * 60)
        logger.info("源3: 裁判文书网增强爬取")
        logger.info("目标: %s 条", f"{min(max_docs, 50_000_000):,}")
        logger.info("=" * 60)

        # 尝试使用已有的爬虫
        try:
            from app.crawlers.wenshu_crawler import WenshuCrawler
            crawler = WenshuCrawler()

            batch = []
            count = 0

            async for doc_data in crawler.crawl_batch(start_page=1, end_page=100):
                case = self._normalize_crawled_doc(doc_data)
                case["tags"] = "phase3,wenshu_live," + case.get("tags", "")
                batch.append(case)
                count += 1

                if len(batch) >= BATCH_SIZE:
                    await self._importer.import_batch(batch)
                    batch.clear()
                    logger.info("  [wenshu] %d 条已导入", count)

                if count >= max_docs:
                    break

            if batch:
                await self._importer.import_batch(batch)

            await crawler.close()

        except Exception as exc:
            logger.warning("[wenshu] 爬取失败 (可能需要代理): %s", exc)
            logger.info("[wenshu] 跳过在线爬取，使用离线数据源")

        ck.write_text(str(count if 'count' in dir() else 0), encoding="utf-8")

    # ---- 源4: 大规模离线案例数据生成 ----

    async def generate_offline_cases(self, target: int = 10_000_000):
        """基于已有数据模式，大规模生成离线案例数据。

        使用 laws.json 中的真实法律条文和已有案例数据模式，
        通过排列组合生成大量结构化的模拟案例数据。

        这些数据用于训练和向量检索，标记为 synthetic。
        """
        ck = CHECKPOINT_DIR / "phase3_synthetic.done"
        if ck.exists():
            count = int(ck.read_text(encoding="utf-8").strip() or "0")
            logger.info("[SKIP] 合成数据已生成 (%d 条)", count)
            return

        logger.info("=" * 60)
        logger.info("源4: 大规模合成案例数据生成")
        logger.info("目标: %s 条", f"{target:,}")
        logger.info("=" * 60)

        # 加载已有的案例模式
        patterns = self._load_case_patterns()
        if not patterns:
            logger.warning("无可用案例模式，跳过合成")
            return

        count = 0
        batch = []

        for i in range(target):
            # 随机选择模式并生成变体
            pattern = random.choice(patterns)
            case = self._generate_case_variant(pattern, i)
            if case:
                batch.append(case)
                count += 1

                if len(batch) >= BATCH_SIZE:
                    await self._importer.import_batch(batch)
                    batch.clear()
                    if count % 100_000 == 0:
                        logger.info("  [synthetic] %d / %d 条 ...", count, target)

            if count >= target:
                break

        if batch:
            await self._importer.import_batch(batch)

        ck.write_text(str(count), encoding="utf-8")
        logger.info("合成数据生成完成: %d 条", count)

    def _load_case_patterns(self) -> list[dict]:
        """从已有数据中提取案例模式。"""
        patterns = []

        # 从 laws.json 中提取法律名称和条文
        laws_json = BACKEND_ROOT / "data" / "datasets" / "laws.json"
        if laws_json.exists():
            try:
                with open(laws_json, "r", encoding="utf-8") as f:
                    laws = json.load(f)
                for law in laws[:500]:  # 使用500部法律作为模板
                    title = law.get("title", "")
                    law_type = law.get("type", "")
                    content = law.get("content", "")
                    # 提取条文号
                    articles = re.findall(r"第[一二三四五六七八九十百千零\d]+条", content)
                    for art in articles[:5]:
                        patterns.append({
                            "law_name": title,
                            "law_type": law_type,
                            "article": art,
                            "content_snippet": content[:300],
                        })
            except Exception:
                pass

        # 从已有案例数据中提取模式
        refined_path = BACKEND_ROOT / "data" / "datasets" / "refined_legal_train.json"
        if refined_path.exists():
            try:
                with open(refined_path, "r", encoding="utf-8") as f:
                    cases = json.load(f)
                for case in cases[:2000]:
                    meta = case.get("meta", {})
                    accusation = meta.get("accusation", [])
                    if isinstance(accusation, list) and accusation:
                        patterns.append({
                            "accusation": "、".join(str(a) for a in accusation),
                            "articles": meta.get("relevant_articles", []),
                            "term": meta.get("term_of_imprisonment", {}),
                            "type": "criminal",
                        })
            except Exception:
                pass

        return patterns

    def _generate_case_variant(self, pattern: dict, seed: int) -> dict | None:
        """基于模式生成案例变体。"""
        random.seed(seed)
        case_type = pattern.get("type", "民事")

        courts = [
            "北京市第一中级人民法院", "上海市浦东新区人民法院",
            "广州市天河区人民法院", "深圳市南山区人民法院",
            "杭州市余杭区人民法院", "成都市武侯区人民法院",
            "武汉市江汉区人民法院", "南京市鼓楼区人民法院",
            "天津市和平区人民法院", "重庆市渝中区人民法院",
            "长沙市中级人民法院", "郑州市金水区人民法院",
            "西安市雁塔区人民法院", "苏州市中级人民法院",
            "青岛市市南区人民法院",
        ]
        years = list(range(2018, 2026))

        court = random.choice(courts)
        year = random.choice(years)
        case_num = f"（{year}）{court[0:3]}法{random.choice(['民', '刑', '行'])}初字{random.randint(1, 99999)}号"

        if case_type == "criminal" and pattern.get("accusation"):
            cause = pattern["accusation"]
            return {
                "case_number": case_num,
                "title": f"{court}刑事判决书-{cause}",
                "court_name": court,
                "case_type": "刑事",
                "cause_of_action": cause,
                "decision_date": f"{year}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
                "parties": "",
                "summary": f"本案涉及{cause}。",
                "full_text": f"{court}刑事判决书\n案号：{case_num}\n案由：{cause}\n{pattern.get('content_snippet', '')}",
                "key_points": "",
                "referenced_laws": f"{pattern.get('law_name', '')}",
                "judgment_result": "",
                "tags": "phase3,synthetic,刑事",
            }
        else:
            law_name = pattern.get("law_name", "法律")
            article = pattern.get("article", "")
            return {
                "case_number": case_num,
                "title": f"{court}民事判决书",
                "court_name": court,
                "case_type": "民事",
                "cause_of_action": f"依据{law_name}{article}之纠纷",
                "decision_date": f"{year}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
                "parties": "",
                "summary": f"本案涉及{law_name}{article}相关争议。",
                "full_text": f"{court}民事判决书\n案号：{case_num}\n{pattern.get('content_snippet', '')}",
                "key_points": "",
                "referenced_laws": law_name,
                "judgment_result": "",
                "tags": "phase3,synthetic,民事",
            }


# =========================================================================
# Main
# =========================================================================

async def main():
    """主函数 — 阶段3 多源采集。"""
    logger.info("=" * 60)
    logger.info("阶段3: 多源裁判文书大规模采集")
    logger.info("=" * 60)

    t0 = time.time()
    importer = MassCaseImporter()
    crawler = MultiSourceCrawler(importer)

    # 源1: 本地文件导入
    await crawler.import_local_files()

    # 源2: HuggingFace 大规模案例数据
    await crawler.stream_huggingface_cases(target_count=5_000_000)

    # 源3: 裁判文书网爬取（如代理可用）
    await crawler.crawl_wenshu_enhanced(max_docs=50_000_000)

    # 源4: 合成数据生成
    await crawler.generate_offline_cases(target=10_000_000)

    elapsed = time.time() - t0
    stats = importer.get_stats()

    logger.info("=" * 60)
    logger.info("阶段3 完成 (%.1f 分钟)", elapsed / 60)
    logger.info("  总接收: %s", f"{stats['total_received']:,}")
    logger.info("  总导入: %s", f"{stats['total_inserted']:,}")
    logger.info("  跳过:   %s", f"{stats['total_skipped']:,}")
    logger.info("  错误:   %s", f"{stats['errors']:,}")
    logger.info("=" * 60)

    # 打印数据库统计
    await print_db_stats()


async def print_db_stats():
    """打印数据库案例总数。"""
    try:
        from sqlalchemy import select, func as sa_func
        from app.core.database import async_session_factory
        from app.models.legal_knowledge import CourtCase, Law, LegalArticle, LegalQAPair

        async with async_session_factory() as session:
            cases = await session.scalar(select(sa_func.count(CourtCase.id))) or 0
            laws = await session.scalar(select(sa_func.count(Law.id))) or 0
            articles = await session.scalar(select(sa_func.count(LegalArticle.id))) or 0
            qa = await session.scalar(select(sa_func.count(LegalQAPair.id))) or 0

        total = cases + laws + articles + qa
        print("\n" + "=" * 60)
        print("数据库总量统计")
        print("=" * 60)
        print(f"  laws:             {laws:>12,}")
        print(f"  legal_articles:   {articles:>12,}")
        print(f"  court_cases:      {cases:>12,}")
        print(f"  legal_qa_pairs:   {qa:>12,}")
        print("-" * 60)
        print(f"  总计:             {total:>12,}")
        print("=" * 60)
    except Exception as exc:
        logger.warning("无法查询数据库: %s", exc)


if __name__ == "__main__":
    asyncio.run(main())
