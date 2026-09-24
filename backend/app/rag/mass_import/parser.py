"""法律数据解析器。

核心功能：
1. 解析 laws.json（378 MB, 22,552 部法律法规全文）为独立条文
2. 合并 parquet_laws.json 的结构化元数据
3. 解析 HuggingFace 下载的法律文件
4. 解析 crawler_data_public 下的爬取数据
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterator

from app.rag.mass_import.progress import ImportProgress

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATASET_DIR = DATA_DIR / "datasets"
CRAWLER_DIR = PROJECT_ROOT / "crawler_data_public"

# 状态映射：laws.json 中文 → 英文
STATUS_MAP = {
    "有效": "active",
    "已修改": "amended",
    "已废止": "repealed",
    "失效": "repealed",
    "7": "unknown",  # 数据质量问题，829 条记录
}

# 法律类型映射
LAW_TYPE_KEYWORDS = {
    "宪法": ["宪法", "全国人民代表大会组织", "立法法", "选举法", "国旗", "国歌", "国徽"],
    "民法": ["民法典", "民法", "物权", "合同", "婚姻家庭", "继承", "人格权", "总则",
             "著作权", "专利", "商标", "知识产权"],
    "刑法": ["刑法", "刑事", "监狱", "社区矫正", "禁毒", "治安管理处罚"],
    "行政法": ["行政处罚", "行政许可", "行政强制", "行政复议", "个人信息", "网络安全",
               "数据安全", "环境保护", "污染防治", "噪声", "大气", "水污染", "土壤",
               "放射性", "防沙治沙", "环境影响评价", "海洋", "青藏高原", "黄河",
               "食品安全", "药品管理", "疫苗", "传染病", "国境卫生", "母婴保健",
               "精神卫生", "基本医疗卫生", "中医药", "医师", "广告", "红十字会",
               "海关", "出境入境", "居民身份证", "枪支", "保守国家秘密", "档案",
               "密码", "国家情报", "反间谍", "人民防空", "国防", "兵役", "军事",
               "警察", "武装警察", "消防", "应急救援", "对外关系", "境外非政府"],
    "经济法": ["消费者权益", "反不正当竞争", "反垄断", "电子商务", "土地管理",
               "税法", "财政", "城市房地产", "城乡规划", "测绘", "气象",
               "科学技术", "科技成果转化", "科学技术普及", "生物安全", "核安全",
               "粮食", "海岛", "海上交通安全", "港口", "铁路", "民用航空",
               "公路", "道路交通", "邮政", "电信", "旅游", "电影产业",
               "文化", "公共图书馆", "公共文化", "非物质文化", "文物",
               "体育", "教育", "义务教育", "职业教育", "高等教育", "民办教育",
               "教师", "公务员", "公证", "律师"],
    "社会法": ["劳动法", "劳动合同", "社会保险", "工会", "就业促进",
               "人口与计划生育", "未成年人", "老年人", "残疾人", "妇女权益",
               "法律援助", "志愿服务"],
    "商法": ["公司法", "合伙企业", "破产", "票据", "保险法", "海商", "证券"],
    "诉讼法": ["民事诉讼法", "刑事诉讼法", "行政诉讼法", "仲裁", "人民调解"],
    "地方性法规": ["地方", "省", "市", "自治区", "自治州", "自治县"],
    "司法解释": ["司法解释", "最高法", "最高检"],
}


# =========================================================================
# 条文拆分器（复用 milvus_full_import.py 的逻辑）
# =========================================================================

class ArticleSplitter:
    """将法律全文按"第X条"拆分为独立条文。"""

    # 匹配 "第一条"、"第二十三条"、"第一千零四十六条" 等
    ARTICLE_PATTERN = re.compile(
        r'(第[一二三四五六七八九十百零千\d]+条[ \s])'
    )

    # 匹配章节标题
    CHAPTER_PATTERN = re.compile(
        r'^(第[一二三四五六七八九十百零]+[编章节][ \s].+)$',
        re.MULTILINE,
    )

    @staticmethod
    def split(title: str, content: str) -> list[dict[str, Any]]:
        """将法律全文拆分为条文列表。

        Args:
            title: 法律名称
            content: 法律全文

        Returns:
            条文列表，每条包含 law_name, article_number, content, chapter, category, tags
        """
        articles: list[dict[str, Any]] = []
        category = guess_category(title)

        parts = ArticleSplitter.ARTICLE_PATTERN.split(content)

        if len(parts) <= 1:
            # 无法按条拆分，整段作为条目
            cleaned = re.sub(r'\s+', '', content).strip()
            if cleaned and len(cleaned) > 10:
                articles.append({
                    "law_name": title,
                    "article_number": "",
                    "content": cleaned[:8000],
                    "chapter": "",
                    "category": category,
                    "tags": guess_tags(title),
                })
            return articles

        # 提取章节位置
        current_chapter = ""
        chapter_positions: list[tuple[int, str]] = []
        for match in ArticleSplitter.CHAPTER_PATTERN.finditer(content):
            chapter_positions.append((match.start(), match.group(1).strip()))

        # 解析每条法条
        for i in range(1, len(parts) - 1, 2):
            article_num = parts[i].strip()
            raw_content = parts[i + 1] if i + 1 < len(parts) else ""

            # 去除混入的章节标题
            raw_content = ArticleSplitter.CHAPTER_PATTERN.sub("", raw_content)
            cleaned = re.sub(r'\s+', '', raw_content).strip()

            if not cleaned or len(cleaned) < 5:
                continue

            # 确定所属章节
            article_pos = content.find(parts[i])
            for ch_pos, ch_name in chapter_positions:
                if ch_pos <= article_pos:
                    current_chapter = ch_name
                else:
                    break

            articles.append({
                "law_name": title,
                "article_number": article_num,
                "content": cleaned[:8000],
                "chapter": current_chapter,
                "category": category,
                "tags": guess_tags(title),
            })

        return articles


# =========================================================================
# 分类推断（复用 milvus_full_import.py 的逻辑）
# =========================================================================

def guess_category(law_name: str) -> str:
    """根据法律名称推断分类。"""
    for cat, keywords in LAW_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in law_name:
                return cat
    return "其他"


def guess_tags(law_name: str) -> str:
    """根据法律名称生成标签。"""
    short = law_name.replace("中华人民共和国", "")
    category = guess_category(law_name)
    return f"{category},{short}"


# =========================================================================
# laws.json 解析器
# =========================================================================

class LawsJsonParser:
    """解析 laws.json（378 MB, 22,552 部法律）并合并 parquet_laws.json 元数据。"""

    def __init__(
        self,
        laws_json_path: Path | str | None = None,
        parquet_laws_path: Path | str | None = None,
    ) -> None:
        self._laws_path = Path(laws_json_path or DATASET_DIR / "laws.json")
        self._parquet_path = Path(parquet_laws_path or DATASET_DIR / "parquet_laws.json")
        self._metadata_map: dict[str, dict[str, str]] = {}
        self._load_metadata()

    def _load_metadata(self) -> None:
        """从 parquet_laws.json 加载结构化元数据。"""
        if not self._parquet_path.exists():
            logger.warning("parquet_laws.json not found at %s, using laws.json fields only",
                           self._parquet_path)
            return

        try:
            with open(self._parquet_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, list):
                logger.warning("parquet_laws.json is not a list, skipping")
                return

            for item in data:
                title = item.get("title", "")
                if not title:
                    continue
                self._metadata_map[title] = {
                    "law_type": item.get("law_type", ""),
                    "issuing_authority": item.get("issuing_authority", ""),
                    "effective_date": item.get("effective_date", ""),
                    "status": self._map_status(item.get("status", "")),
                }

            logger.info("Loaded metadata for %d laws from parquet_laws.json",
                        len(self._metadata_map))
        except Exception as exc:
            logger.error("Failed to load parquet_laws.json: %s", exc)

    @staticmethod
    def _map_status(raw_status: str) -> str:
        """将状态值映射为标准英文。"""
        return STATUS_MAP.get(str(raw_status), "unknown")

    def parse_all(
        self,
        version_policy: str = "latest_active",
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """解析 laws.json，返回法律和条文的完整列表。

        Args:
            version_policy: 版本处理策略
                - 'latest_active': 仅保留最新有效版本（默认）
                - 'all_versions': 保留所有版本
                - 'latest_all': 保留最新版本（不论状态）

        Returns:
            (laws_list, articles_list) 二元组
        """
        if not self._laws_path.exists():
            raise FileNotFoundError(f"laws.json not found at {self._laws_path}")

        logger.info("Loading laws.json from %s ...", self._laws_path)

        with open(self._laws_path, "r", encoding="utf-8") as f:
            all_laws = json.load(f)

        if not isinstance(all_laws, list):
            raise ValueError(f"Expected list in laws.json, got {type(all_laws)}")

        logger.info("Loaded %d law entries from laws.json", len(all_laws))

        # 版本去重
        if version_policy == "latest_active":
            laws = self._deduplicate_versions(all_laws, keep_active=True)
        elif version_policy == "latest_all":
            laws = self._deduplicate_versions(all_laws, keep_active=False)
        else:
            laws = all_laws

        logger.info("After version policy '%s': %d laws", version_policy, len(laws))

        # 逐部法律解析条文
        all_articles: list[dict[str, Any]] = []
        laws_list: list[dict[str, Any]] = []
        progress = ImportProgress(len(laws), "parse")

        for law_entry in laws:
            title = law_entry.get("title", "")
            content = law_entry.get("content", "")

            if not title:
                continue

            # 构建法律元数据
            law_meta = self._build_law_meta(law_entry)
            laws_list.append(law_meta)

            # 拆分条文
            if content:
                articles = ArticleSplitter.split(title, content)
                for art in articles:
                    art["law_meta"] = law_meta
                all_articles.extend(articles)

            progress.update()

        parse_stats = progress.finish()
        logger.info(
            "Parsed %d laws → %d articles (from %d entries)",
            len(laws_list), len(all_articles), len(all_laws),
        )

        return laws_list, all_articles

    def _deduplicate_versions(
        self,
        laws: list[dict[str, Any]],
        keep_active: bool = True,
    ) -> list[dict[str, Any]]:
        """对同名法律保留最新版本。"""
        best: dict[str, dict[str, Any]] = {}

        for law in laws:
            title = law.get("title", "")
            if not title:
                continue

            publish = law.get("publish", "")
            status = law.get("status", "")

            if title not in best:
                best[title] = law
                continue

            existing = best[title]
            existing_status = existing.get("status", "")
            existing_publish = existing.get("publish", "")

            if keep_active:
                # 优先保留 "有效" 版本
                if status == "有效" and existing_status != "有效":
                    best[title] = law
                    continue
                if existing_status == "有效" and status != "有效":
                    continue

            # 保留最新发布的版本
            if publish > existing_publish:
                best[title] = law

        result = list(best.values())
        logger.info(
            "Version dedup: %d → %d (removed %d duplicates)",
            len(laws), len(result), len(laws) - len(result),
        )
        return result

    def _build_law_meta(self, entry: dict[str, Any]) -> dict[str, Any]:
        """从 laws.json 条目 + parquet 元数据构建标准化法律元数据。"""
        title = entry.get("title", "")
        parquet_meta = self._metadata_map.get(title, {})

        # laws.json 的字段（中文）
        raw_type = entry.get("type", "")
        raw_office = entry.get("office", "")
        raw_publish = entry.get("publish", "")
        raw_status = entry.get("status", "")

        # parquet 的字段优先（英文、更规范），回退到 laws.json
        law_type = parquet_meta.get("law_type", "") or guess_category(title)
        issuing_authority = parquet_meta.get("issuing_authority", "") or raw_office
        effective_date = parquet_meta.get("effective_date", "") or raw_publish.split(" ")[0] if raw_publish else ""
        status = parquet_meta.get("status", "") or self._map_status(raw_status)

        # 如果 law_type 仍为空，根据标题推断
        if not law_type or law_type == "其他":
            law_type = guess_category(title)

        return {
            "name": title,
            "short_name": title.replace("中华人民共和国", ""),
            "law_type": law_type,
            "category": law_type,
            "effective_date": effective_date,
            "status": status,
            "issuing_authority": issuing_authority,
            "abstract": "",
        }


# =========================================================================
# HuggingFace 文件解析器
# =========================================================================

class HuggingFaceParser:
    """解析从 HuggingFace 下载的法律 JSON 文件。"""

    def __init__(self, dataset_dir: Path | str | None = None) -> None:
        self._dir = Path(dataset_dir or DATASET_DIR)

    def parse_all(self) -> list[dict[str, Any]]:
        """解析所有 hf_laws_*.json 文件。"""
        articles: list[dict[str, Any]] = []

        if not self._dir.exists():
            logger.warning("Dataset directory not found: %s", self._dir)
            return articles

        files = sorted(self._dir.glob("hf_laws_*.json"))
        if not files:
            logger.info("No hf_laws_*.json files found in %s", self._dir)
            return articles

        for file_path in files:
            parsed = self._parse_file(file_path)
            logger.info("  %s: %d articles", file_path.name, len(parsed))
            articles.extend(parsed)

        logger.info("HuggingFace parser: %d articles from %d files",
                     len(articles), len(files))
        return articles

    def _parse_file(self, file_path: Path) -> list[dict[str, Any]]:
        """解析单个 JSON 文件。"""
        articles: list[dict[str, Any]] = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.error("Failed to read %s: %s", file_path, exc)
            return articles

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    title = item.get("title", item.get("law_name", file_path.stem))
                    content = item.get("content", item.get("text", ""))
                    if content:
                        parsed = ArticleSplitter.split(title, content)
                        articles.extend(parsed)
                elif isinstance(item, str):
                    parsed = ArticleSplitter.split(file_path.stem, item)
                    articles.extend(parsed)

        elif isinstance(data, str):
            parsed = ArticleSplitter.split(file_path.stem, data)
            articles.extend(parsed)

        elif isinstance(data, dict):
            title = data.get("title", file_path.stem)
            content = data.get("content", data.get("text", ""))
            if content:
                parsed = ArticleSplitter.split(title, content)
                articles.extend(parsed)

        return articles


# =========================================================================
# 爬虫数据解析器
# =========================================================================

class CrawlerDataParser:
    """解析 crawler_data_public/ 下的爬取数据。"""

    def __init__(self, crawler_dir: Path | str | None = None) -> None:
        self._dir = Path(crawler_dir or CRAWLER_DIR)

    def parse_laws(self) -> list[dict[str, Any]]:
        """解析法律条文数据（law_*.json 文件）。"""
        articles: list[dict[str, Any]] = []

        if not self._dir.exists():
            logger.warning("Crawler directory not found: %s", self._dir)
            return articles

        for file_path in sorted(self._dir.glob("law_*.json")):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as exc:
                logger.warning("Failed to read %s: %s", file_path, exc)
                continue

            if isinstance(data, dict):
                # 单条记录格式
                law_name = data.get("law_name", data.get("title", file_path.stem))
                article_num = data.get("article_number", data.get("num", ""))
                content = data.get("content", data.get("text", ""))
                if content:
                    articles.append({
                        "law_name": law_name,
                        "article_number": article_num,
                        "content": re.sub(r'\s+', '', content).strip()[:8000],
                        "chapter": data.get("chapter", ""),
                        "category": data.get("category", guess_category(law_name)),
                        "tags": data.get("tags", guess_tags(law_name)),
                    })
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        law_name = item.get("law_name", item.get("title", ""))
                        content = item.get("content", item.get("text", ""))
                        if content:
                            articles.append({
                                "law_name": law_name,
                                "article_number": item.get("article_number", item.get("num", "")),
                                "content": re.sub(r'\s+', '', content).strip()[:8000],
                                "chapter": item.get("chapter", ""),
                                "category": item.get("category", guess_category(law_name)),
                                "tags": item.get("tags", guess_tags(law_name)),
                            })

        logger.info("CrawlerDataParser: %d law articles from %s",
                     len(articles), self._dir)
        return articles

    def parse_concepts(self) -> list[dict[str, Any]]:
        """解析法律概念数据（concept_*.json 文件）。"""
        concepts: list[dict[str, Any]] = []

        if not self._dir.exists():
            return concepts

        for file_path in sorted(self._dir.glob("concept_*.json")):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue

            if isinstance(data, dict):
                name = data.get("name", data.get("title", ""))
                definition = data.get("definition", "")
                if name and definition:
                    concepts.append({
                        "name": name,
                        "definition": definition,
                        "category": data.get("category", ""),
                    })

        logger.info("CrawlerDataParser: %d concepts", len(concepts))
        return concepts


# =========================================================================
# 种子数据解析器
# =========================================================================

class SeedDataParser:
    """解析 knowledge_seed.py 中的种子数据。"""

    @staticmethod
    def parse_articles() -> list[dict[str, Any]]:
        """从 knowledge_seed.py 加载法条。"""
        from app.rag.knowledge_seed import ARTICLES_DATA

        articles: list[dict[str, Any]] = []
        for item in ARTICLES_DATA:
            law_name = item.get("law", item.get("law_name", ""))
            content = item.get("content", "")
            if not content:
                continue
            articles.append({
                "law_name": law_name,
                "article_number": item.get("num", item.get("article_number", "")),
                "content": re.sub(r'\s+', '', content).strip()[:8000],
                "chapter": item.get("chapter", ""),
                "tags": item.get("tags", ""),
                "category": guess_category(law_name),
            })

        logger.info("SeedDataParser: %d articles", len(articles))
        return articles

    @staticmethod
    def parse_laws() -> list[dict[str, Any]]:
        """从 knowledge_seed.py 加载法律元数据。"""
        from app.rag.knowledge_seed import LAWS_DATA
        return list(LAWS_DATA)

    @staticmethod
    def parse_cases() -> list[dict[str, Any]]:
        """从 knowledge_seed.py 加载案例数据。"""
        from app.rag.knowledge_seed import COURT_CASES_DATA
        return list(COURT_CASES_DATA)

    @staticmethod
    def parse_concepts() -> list[dict[str, Any]]:
        """从 knowledge_seed.py 加载法律概念。"""
        from app.rag.knowledge_seed import LEGAL_CONCEPTS_DATA
        return list(LEGAL_CONCEPTS_DATA)
