"""裁判文书网爬虫 - 爬取公开裁判文书数据。

数据源：
- 中国裁判文书网 (wenshu.court.gov.cn)
- 各级法院公开裁判文书

注意：遵守 robots.txt，控制爬取频率，仅用于法律研究和公共服务。
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
from datetime import datetime
from typing import Any, AsyncGenerator, Optional

import httpx
from bs4 import BeautifulSoup

from app.services.proxy_service import get_proxy_service

logger = logging.getLogger(__name__)


# =============================================================================
# 配置
# =============================================================================

class CrawlerConfig:
    """爬虫配置"""
    # 基础 URL
    BASE_URL = "https://wenshu.court.gov.cn"

    # 请求间隔（秒），避免过于频繁
    MIN_DELAY = 2.0
    MAX_DELAY = 5.0

    # 并发请求数
    MAX_CONCURRENT = 3

    # 最大爬取页数（用于测试）
    MAX_PAGES = 100

    # User-Agent 列表
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]

    # 数据存储目录
    DATA_DIR = "./crawler_data"

    # 断点续传文件
    CHECKPOINT_FILE = "./crawler_data/checkpoint.json"

    # 代理配置（可选）
    # 说明：
    # - 默认不启用代理，避免在未配置可用代理时导致所有请求失败
    # - 如需启用：把 USE_PROXY 改为 True，并通过环境变量 WENSHU_PROXY_URL 指定代理
    # - 代理格式示例：http://user:pass@host:port
    PROXY_URL_ENV = "WENSHU_PROXY_URL"

    # 是否使用代理（默认关闭；生产环境建议开启并配置可用代理池）
    USE_PROXY = False


# =============================================================================
# 反爬虫处理
# =============================================================================

class AntiCrawl:
    """反爬虫策略"""

    @staticmethod
    def get_random_headers() -> dict[str, str]:
        """生成随机请求头"""
        return {
            "User-Agent": random.choice(CrawlerConfig.USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        }

    @staticmethod
    async def random_delay():
        """随机延迟"""
        delay = random.uniform(CrawlerConfig.MIN_DELAY, CrawlerConfig.MAX_DELAY)
        await asyncio.sleep(delay)


# =============================================================================
# 断点续传
# =============================================================================

class Checkpoint:
    """断点续传管理"""

    def __init__(self, checkpoint_file: str = CrawlerConfig.CHECKPOINT_FILE):
        self.checkpoint_file = checkpoint_file
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        """加载断点数据"""
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"加载断点文件失败: {e}")
        return {
            "last_page": 0,
            "last_doc_id": None,
            "crawled_ids": [],
            "start_time": datetime.now().isoformat(),
        }

    def save(self):
        """保存断点数据"""
        os.makedirs(os.path.dirname(self.checkpoint_file), exist_ok=True)
        with open(self.checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def update(self, page: int, doc_id: str):
        """更新断点"""
        self.data["last_page"] = page
        self.data["last_doc_id"] = doc_id
        if doc_id not in self.data["crawled_ids"]:
            self.data["crawled_ids"].append(doc_id)
            # 只保留最近 10000 个 ID
            if len(self.data["crawled_ids"]) > 10000:
                self.data["crawled_ids"] = self.data["crawled_ids"][-10000:]
        self.save()

    def is_crawled(self, doc_id: str) -> bool:
        """检查是否已爬取"""
        return doc_id in self.data["crawled_ids"]


# =============================================================================
# 裁判文书网爬虫
# =============================================================================

class WenshuCrawler:
    """裁判文书网爬虫"""

    def __init__(self):
        self._proxy_service = get_proxy_service()
        self.client: Optional[httpx.AsyncClient] = None
        self.checkpoint = Checkpoint()
        self.stats = {
            "total_crawled": 0,
            "total_saved": 0,
            "errors": 0,
            "start_time": time.time(),
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """Get an httpx client, using the proxy pool if available."""
        if self.client is not None:
            return self.client

        if self._proxy_service.enabled:
            self.client = await self._proxy_service.get_crawl_client(timeout=30.0)
            logger.info("使用代理池访问")
        else:
            self.client = httpx.AsyncClient(timeout=30.0)
            logger.info("不使用代理，直接连接")
        return self.client

    async def close(self):
        """关闭爬虫"""
        if self.client:
            await self.client.aclose()
            self.client = None

    async def _make_proxy_request(self, url, method="GET", **kwargs):
        """Make an HTTP request, using proxy pool if enabled."""
        client = await self._get_client()
        if method.upper() == "POST":
            return await client.post(url, **kwargs)
        return await client.get(url, **kwargs)

    async def crawl_list_page(self, page: int = 1) -> list[dict[str, Any]]:
        """爬取列表页，获取文书 ID 列表"""
        url = f"{CrawlerConfig.BASE_URL}/website/parse/rest.q4w"

        # 请求参数
        params = {
            "sortFields": "courage,cause,caseType,courtName,docType,judgeDate",
            "ciphertext": self._generate_ciphertext(),
            "pageNum": page,
            "pageSize": 15,
            "queryCondition": json.dumps([{"key": "s8", "value": "裁判文书"}]),
            "cfg": "com.lawyee.common498.mobile.listen498",
            "__RequestVerificationToken": self._generate_token(),
        }

        headers = AntiCrawl.get_random_headers()
        headers["Referer"] = f"{CrawlerConfig.BASE_URL}/website/parse/rest.q4w"

        try:
            response = await self._make_proxy_request(
                url, method="POST", data=params, headers=headers
            )
            response.raise_for_status()

            data = response.json()
            if data.get("result"):
                return data["result"].get("queryResult", [])
            return []

        except Exception as e:
            logger.error(f"爬取列表页失败 (page={page}): {e}")
            self.stats["errors"] += 1
            return []

    async def crawl_document(self, doc_id: str) -> dict[str, Any] | None:
        """爬取单个裁判文书详情"""
        if self.checkpoint.is_crawled(doc_id):
            logger.debug(f"文书已爬取，跳过: {doc_id}")
            return None

        url = f"{CrawlerConfig.BASE_URL}/website/parse/rest.q4w"

        params = {
            "docId": doc_id,
            "cfg": "com.lawyee.common498.mobile.listen498",
            "__RequestVerificationToken": self._generate_token(),
        }

        headers = AntiCrawl.get_random_headers()
        headers["Referer"] = f"{CrawlerConfig.BASE_URL}/website/parse/rest.q4w"

        try:
            response = await self._make_proxy_request(
                url, method="POST", data=params, headers=headers
            )
            response.raise_for_status()

            data = response.json()
            if data.get("result"):
                doc_data = self._parse_document(data["result"])
                doc_data["doc_id"] = doc_id
                doc_data["crawl_time"] = datetime.now().isoformat()
                return doc_data

        except Exception as e:
            logger.error(f"爬取文书详情失败 (doc_id={doc_id}): {e}")
            self.stats["errors"] += 1

        return None

    def _parse_document(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """解析裁判文书数据"""
        # 提取 HTML 内容
        html_content = raw_data.get("qwContent", "")
        soup = BeautifulSoup(html_content, "html.parser")

        # 提取文本
        text = soup.get_text(separator="\n", strip=True)

        # 提取关键信息
        title = self._extract_title(text)
        case_number = self._extract_case_number(text)
        court = self._extract_court(text)
        case_type = self._extract_case_type(text)
        cause = self._extract_cause(text)
        judge_date = self._extract_judge_date(text)
        parties = self._extract_parties(text)
        summary = self._extract_summary(text)
        key_points = self._extract_key_points(text)
        referenced_laws = self._extract_referenced_laws(text)
        judgment_result = self._extract_judgment_result(text)
        tags = self._generate_tags(case_type, cause)

        return {
            "title": title,
            "case_number": case_number,
            "court_name": court,
            "case_type": case_type,
            "cause_of_action": cause,
            "decision_date": judge_date,
            "parties": parties,
            "summary": summary,
            "full_text": text,
            "key_points": key_points,
            "referenced_laws": referenced_laws,
            "judgment_result": judgment_result,
            "tags": tags,
            "raw_html": html_content,
        }

    def _extract_title(self, text: str) -> str:
        """提取文书标题"""
        # 通常是第一行或包含"书"字的行
        lines = text.split("\n")
        for line in lines[:10]:
            if "书" in line or "判决" in line or "裁定" in line:
                return line.strip()
        return lines[0].strip() if lines else ""

    def _extract_case_number(self, text: str) -> str:
        """提取案号"""
        # 匹配模式：(年份)法院简称+案件类型+编号
        pattern = r"[（(]\d{4}[）)][^\s]+字第\d+号"
        match = re.search(pattern, text)
        return match.group(0) if match else ""

    def _extract_court(self, text: str) -> str:
        """提取法院名称"""
        # 通常在标题或案号中
        patterns = [
            r"([一-龥]+人民法院)",
            r"([一-龥]+中级人民法院)",
            r"([一-龥]+高级人民法院)",
            r"(最高人民法院)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text[:500])
            if match:
                return match.group(1)
        return ""

    def _extract_case_type(self, text: str) -> str:
        """提取案件类型"""
        if "民事" in text[:200]:
            return "民事"
        elif "刑事" in text[:200]:
            return "刑事"
        elif "行政" in text[:200]:
            return "行政"
        elif "执行" in text[:200]:
            return "执行"
        return ""

    def _extract_cause(self, text: str) -> str:
        """提取案由"""
        # 通常在"案由："之后
        match = re.search(r"案由[：:]\s*([^\n]+)", text)
        if match:
            return match.group(1).strip()

        # 或者从标题中提取
        patterns = [
            r"([一-龥]+纠纷)",
            r"([一-龥]+罪)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text[:500])
            if match:
                return match.group(1)
        return ""

    def _extract_judge_date(self, text: str) -> str:
        """提取裁判日期"""
        # 匹配：YYYY年MM月DD日 或 YYYY-MM-DD
        patterns = [
            r"(\d{4}年\d{1,2}月\d{1,2}日)",
            r"(\d{4}-\d{2}-\d{2})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return ""

    def _extract_parties(self, text: str) -> str:
        """提取当事人信息"""
        parties = []
        # 提取原告、被告、上诉人、被上诉人等
        patterns = [
            r"(原告[：:].+?)(?=被告|上诉人|被上诉人|审判)",
            r"(被告[：:].+?)(?=原告|上诉人|被上诉人|审判)",
            r"(上诉人[：:].+?)(?=被上诉人|原审|审判)",
            r"(被上诉人[：:].+?)(?=上诉人|原审|审判)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                parties.append(match.group(1).strip())
        return "\n".join(parties[:4])  # 最多4个当事人

    def _extract_summary(self, text: str) -> str:
        """提取案件摘要"""
        # 通常在前 1000 字中
        # 寻找"经审理查明"或"本院认为"等关键段落
        patterns = [
            r"(经审理查明[：:].+?)(?=本院认为|判决如下)",
            r"(本院认为[：:].+?)(?=判决如下|裁定如下)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                summary = match.group(1).strip()
                # 截取前 500 字
                return summary[:500] + "..." if len(summary) > 500 else summary
        return text[:500] + "..." if len(text) > 500 else text

    def _extract_key_points(self, text: str) -> str:
        """提取裁判要旨"""
        # 通常在"本院认为"段落
        match = re.search(r"本院认为[：:](.+?)(?=判决如下|裁定如下)", text, re.DOTALL)
        if match:
            key_points = match.group(1).strip()
            # 提取关键句
            sentences = re.split(r"[。；]", key_points)
            key_sentences = [s.strip() for s in sentences if len(s.strip()) > 20][:5]
            return "；".join(key_sentences)
        return ""

    def _extract_referenced_laws(self, text: str) -> str:
        """提取引用的法条"""
        laws = []
        # 匹配：《法律名称》第X条
        pattern = r"《([^》]+)》第[\d百千万零一二三四五六七八九十百千]+条"
        matches = re.findall(pattern, text)
        laws = list(set(matches))  # 去重
        return "、".join(laws[:10])  # 最多10个法条

    def _extract_judgment_result(self, text: str) -> str:
        """提取判决结果"""
        # 通常在"判决如下"或"裁定如下"之后
        patterns = [
            r"判决如下[：:](.+?)(?=如不服|本判决|审)",
            r"裁定如下[：:](.+?)(?=如不服|本裁定|审)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                result = match.group(1).strip()
                return result[:800] + "..." if len(result) > 800 else result
        return ""

    def _generate_tags(self, case_type: str, cause: str) -> str:
        """生成标签"""
        tags = []
        if case_type:
            tags.append(case_type)
        if cause:
            tags.append(cause)
        # 可以根据案由添加更多标签
        cause_tags = {
            "劳动合同纠纷": ["劳动法", "劳动合同"],
            "离婚纠纷": ["婚姻家庭", "离婚"],
            "盗窃罪": ["刑事", "盗窃"],
            "合同纠纷": ["民事", "合同"],
        }
        if cause in cause_tags:
            tags.extend(cause_tags[cause])
        return ",".join(tags)

    def _generate_ciphertext(self) -> str:
        """生成加密参数（简化版，实际需要根据网站加密算法）"""
        timestamp = str(int(time.time() * 1000))
        return hashlib.md5(timestamp.encode()).hexdigest()

    def _generate_token(self) -> str:
        """生成验证令牌（简化版）"""
        return hashlib.md5(str(random.random()).encode()).hexdigest()

    async def crawl_batch(
        self,
        start_page: int = 1,
        end_page: int = None,
        save_interval: int = 10,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """批量爬取"""
        end_page = end_page or CrawlerConfig.MAX_PAGES

        logger.info(f"开始批量爬取: page {start_page} - {end_page}")

        for page in range(start_page, end_page + 1):
            logger.info(f"爬取列表页: {page}/{end_page}")

            # 爬取列表
            doc_list = await self.crawl_list_page(page)
            if not doc_list:
                logger.warning(f"列表页为空，可能已到达最后一页: page={page}")
                break

            # 爬取每个文书详情
            for doc_info in doc_list:
                doc_id = doc_info.get("rowkey") or doc_info.get("docId")
                if not doc_id:
                    continue

                if self.checkpoint.is_crawled(doc_id):
                    continue

                # 爬取详情
                doc_data = await self.crawl_document(doc_id)
                if doc_data:
                    self.stats["total_crawled"] += 1
                    self.checkpoint.update(page, doc_id)

                    yield doc_data

                    # 定期保存
                    if self.stats["total_crawled"] % save_interval == 0:
                        logger.info(f"已爬取 {self.stats['total_crawled']} 条文书")

                # 随机延迟
                await AntiCrawl.random_delay()

            # 页面间延迟
            await AntiCrawl.random_delay()

        logger.info(f"爬取完成: 共 {self.stats['total_crawled']} 条文书")

    async def save_to_file(self, doc_data: dict[str, Any], output_dir: str = None):
        """保存文书到文件"""
        output_dir = output_dir or CrawlerConfig.DATA_DIR
        os.makedirs(output_dir, exist_ok=True)

        doc_id = doc_data.get("doc_id", "unknown")
        filename = f"{output_dir}/{doc_id}.json"

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(doc_data, f, ensure_ascii=False, indent=2)

        self.stats["total_saved"] += 1


# =============================================================================
# 主函数
# =============================================================================

async def main():
    """主函数"""
    crawler = WenshuCrawler()

    try:
        # 从断点继续
        start_page = crawler.checkpoint.data.get("last_page", 0) + 1

        # 批量爬取
        async for doc_data in crawler.crawl_batch(start_page=start_page):
            # 保存到文件
            await crawler.save_to_file(doc_data)

            # 打印进度
            if crawler.stats["total_crawled"] % 10 == 0:
                elapsed = time.time() - crawler.stats["start_time"]
                speed = crawler.stats["total_crawled"] / elapsed if elapsed > 0 else 0
                print(f"已爬取: {crawler.stats['total_crawled']} | "
                      f"速度: {speed:.2f} 条/秒 | "
                      f"错误: {crawler.stats['errors']}")

    except KeyboardInterrupt:
        logger.info("用户中断爬取")
    finally:
        await crawler.close()
        logger.info(f"爬取统计: {crawler.stats}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
