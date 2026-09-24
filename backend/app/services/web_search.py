"""
Web Search Integration -- Real-time web search for legal information.

Provides:
1. Multi-engine web search (Bing/Baidu/Sogou)
2. Legal-specific search with citation extraction
3. Regulation update monitoring
4. Legal news aggregation
5. Search result credibility assessment

Supports the legal AI system with up-to-date information beyond
the training data cutoff, essential for tracking regulatory changes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Optional
from urllib.parse import quote_plus

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Search Result Types
# =============================================================================

@dataclass
class SearchResult:
    """A single web search result."""
    title: str
    url: str
    snippet: str
    source: str = ""
    published_date: str = ""
    relevance_score: float = 0.0
    is_legal_source: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "published_date": self.published_date,
            "relevance_score": self.relevance_score,
            "is_legal_source": self.is_legal_source,
            "metadata": self.metadata,
        }


@dataclass
class SearchResponse:
    """Complete search response with results and metadata."""
    query: str
    results: list[SearchResult] = field(default_factory=list)
    total_found: int = 0
    search_engine: str = ""
    elapsed_seconds: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "total_found": self.total_found,
            "search_engine": self.search_engine,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
            "metadata": self.metadata,
        }


# =============================================================================
# Trusted Legal Sources
# =============================================================================

TRUSTED_LEGAL_DOMAINS = {
    # 官方来源
    "court.gov.cn",           # 最高人民法院
    "wenshu.court.gov.cn",    # 中国裁判文书网
    "npc.gov.cn",             # 全国人大
    "gov.cn",                 # 中国政府网
    "moj.gov.cn",             # 司法部
    "spp.gov.cn",             # 最高人民检察院
    "samr.gov.cn",            # 国家市场监督管理总局
    "cnipa.gov.cn",           # 国家知识产权局
    # 权威法律平台
    "pkulaw.com",             # 北大法宝
    "faxin.cn",               # 法信
    "chinacourt.org",         # 中国法院网
    "legaldaily.com.cn",      # 法制日报
    # 学术来源
    "cnki.net",               # 中国知网
    "lawyee.net",             # 法意
}


def is_legal_source(url: str) -> bool:
    """Check if a URL is from a trusted legal source."""
    for domain in TRUSTED_LEGAL_DOMAINS:
        if domain in url:
            return True
    return False


# =============================================================================
# Web Search Engine
# =============================================================================

class WebSearchEngine:
    """Multi-engine web search for legal information.

    Supports Bing, Baidu, and Sogou as search backends with
    automatic fallback and result deduplication.
    """

    def __init__(self) -> None:
        self._default_engine = getattr(settings, "SEARCH_ENGINE", "bing")
        self._bing_api_key = getattr(settings, "BING_SEARCH_API_KEY", "")
        self._baidu_api_key = getattr(settings, "BAIDU_SEARCH_API_KEY", "")
        self._serper_api_key = getattr(settings, "SERPER_API_KEY", "")

    async def search(
        self,
        query: str,
        num_results: int = 10,
        engine: str | None = None,
        legal_only: bool = False,
        time_range: str | None = None,
    ) -> SearchResponse:
        """Perform a web search.

        Args:
            query: Search query string.
            num_results: Number of results to return.
            engine: Search engine to use (bing/baidu/sogou). None uses default.
            legal_only: If True, filter to only trusted legal sources.
            time_range: Time filter (day/week/month/year).

        Returns:
            SearchResponse with results.
        """
        start_time = time.time()
        engine = engine or self._default_engine

        try:
            # An API-backed engine is only usable when its key is configured.
            # When the *requested* engine has no key we deliberately drop into
            # the fallback chain (which prefers Serper) rather than silently
            # degrading to HTML scraping — that path returned zero results and
            # an `error: null`, so the front-end showed an empty result list
            # with no indication that anything was wrong.
            if engine == "bing" and self._bing_api_key:
                response = await self._search_bing(query, num_results, time_range)
            elif engine == "serper" and self._serper_api_key:
                response = await self._search_serper(query, num_results, time_range)
            elif engine == "baidu":
                response = await self._search_baidu_scrape(query, num_results)
            elif engine == "sogou":
                response = await self._search_sogou_scrape(query, num_results)
            else:
                # Either an unknown engine, or an API engine with no key.
                response = await self._search_with_fallback(query, num_results, time_range)

            # Surface a diagnosable error instead of a silent empty result set.
            if (
                not response.results
                and not response.error
                and not (self._serper_api_key or self._bing_api_key)
            ):
                response.error = (
                    "联网搜索未生效：未配置搜索 API Key"
                    "（SERPER_API_KEY 或 BING_SEARCH_API_KEY），"
                    "网页抓取回退路径未返回可用结果。"
                )
                logger.warning(
                    "Web search returned 0 results and no API key is configured "
                    "(engine=%s, query=%r)",
                    engine,
                    query,
                )

            # Filter to legal sources only if requested
            if legal_only:
                response.results = [
                    r for r in response.results if r.is_legal_source
                ]
                response.total_found = len(response.results)

            response.elapsed_seconds = time.time() - start_time
            return response

        except Exception as exc:
            elapsed = time.time() - start_time
            logger.error("Web search failed: %s", exc)
            return SearchResponse(
                query=query,
                error=str(exc),
                elapsed_seconds=elapsed,
            )

    async def search_legal_updates(
        self,
        topic: str,
        days_back: int = 30,
    ) -> SearchResponse:
        """Search for recent legal regulation updates on a topic.

        Specifically queries government and court sources for
        new regulations, judicial interpretations, and policy changes.
        """
        query = f"{topic} 最新法律法规 司法解释 规范性文件"
        response = await self.search(
            query=query,
            num_results=15,
            legal_only=True,
            time_range="month" if days_back <= 30 else "year",
        )

        # Add legal update metadata
        response.metadata["search_type"] = "legal_update"
        response.metadata["days_back"] = days_back
        return response

    async def search_cases(
        self,
        keywords: str,
        case_type: str | None = None,
    ) -> SearchResponse:
        """Search for recent court cases by keywords."""
        query = f"{keywords} 裁判文书 判决书"
        if case_type:
            query += f" {case_type}案件"

        response = await self.search(query=query, num_results=10, legal_only=True)
        response.metadata["search_type"] = "case_search"
        return response

    # -------------------------------------------------------------------------
    # Search Engine Implementations
    # -------------------------------------------------------------------------

    async def _search_bing(
        self, query: str, num_results: int, time_range: str | None
    ) -> SearchResponse:
        """Search using Bing Web Search API."""
        url = "https://api.bing.microsoft.com/v7.0/search"
        headers = {"Ocp-Apim-Subscription-Key": self._bing_api_key}
        params: dict[str, Any] = {
            "q": query,
            "count": min(num_results, 50),
            "mkt": "zh-CN",
            "textFormat": "HTML",
        }
        if time_range:
            params["freshness"] = time_range

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("webPages", {}).get("value", []):
            url = item.get("url", "")
            results.append(SearchResult(
                title=item.get("name", ""),
                url=url,
                snippet=item.get("snippet", ""),
                source="bing",
                published_date=item.get("dateLastCrawled", ""),
                is_legal_source=is_legal_source(url),
            ))

        return SearchResponse(
            query=query,
            results=results,
            total_found=data.get("webPages", {}).get("totalEstimatedMatches", len(results)),
            search_engine="bing",
        )

    async def _search_serper(
        self, query: str, num_results: int, time_range: str | None
    ) -> SearchResponse:
        """Search using Serper.dev API (Google search)."""
        url = "https://google.serper.dev/search"
        headers = {
            "X-API-KEY": self._serper_api_key,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "q": query,
            "num": min(num_results, 50),
            "gl": "cn",
            "hl": "zh-cn",
        }
        if time_range:
            payload["tbs"] = f"qdr:{time_range[0]}"  # d/w/m/y

        # Retry on transient network errors.  Container DNS/egress can hiccup
        # briefly right after start-up, and a single failure there used to
        # surface to the user as an empty result list.
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                last_exc = None
                break
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning(
                    "Serper search attempt %d/3 failed: %s", attempt + 1, exc
                )
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))

        if last_exc is not None:
            raise last_exc

        results = []
        for item in data.get("organic", []):
            url = item.get("link", "")
            results.append(SearchResult(
                title=item.get("title", ""),
                url=url,
                snippet=item.get("snippet", ""),
                source="serper",
                published_date=item.get("date", ""),
                is_legal_source=is_legal_source(url),
            ))

        return SearchResponse(
            query=query,
            results=results,
            total_found=data.get("searchParameters", {}).get("totalResults", len(results)),
            search_engine="serper",
        )

    async def _search_baidu_scrape(
        self, query: str, num_results: int
    ) -> SearchResponse:
        """Search Baidu via scraping (fallback when no API key)."""
        encoded_query = quote_plus(query)
        url = f"https://www.baidu.com/s?wd={encoded_query}&rn={num_results}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            html = resp.text

        # Simple HTML parsing for Baidu results
        results = []
        # Extract result blocks
        result_pattern = re.compile(
            r'<h3[^>]*class="[^"]*t[^"]*"[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?</h3>.*?'
            r'<span[^>]*class="content-right_[^"]*"[^>]*>(.*?)</span>',
            re.DOTALL
        )
        for match in result_pattern.finditer(html):
            link_url = match.group(1)
            title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            snippet = re.sub(r'<[^>]+>', '', match.group(3)).strip()
            if title and snippet:
                results.append(SearchResult(
                    title=title,
                    url=link_url,
                    snippet=snippet[:300],
                    source="baidu",
                    is_legal_source=is_legal_source(link_url),
                ))

        return SearchResponse(
            query=query,
            results=results[:num_results],
            total_found=len(results),
            search_engine="baidu",
        )

    async def _search_sogou_scrape(
        self, query: str, num_results: int
    ) -> SearchResponse:
        """Search Sogou via scraping (fallback)."""
        encoded_query = quote_plus(query)
        url = f"https://www.sogou.com/web?query={encoded_query}&num={num_results}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            html = resp.text

        results = []
        result_pattern = re.compile(
            r'<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?</h3>.*?'
            r'<p[^>]*class="[^"]*str_info[^"]*"[^>]*>(.*?)</p>',
            re.DOTALL
        )
        for match in result_pattern.finditer(html):
            link_url = match.group(1)
            title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            snippet = re.sub(r'<[^>]+>', '', match.group(3)).strip()
            if title and snippet:
                results.append(SearchResult(
                    title=title,
                    url=link_url,
                    snippet=snippet[:300],
                    source="sogou",
                    is_legal_source=is_legal_source(link_url),
                ))

        return SearchResponse(
            query=query,
            results=results[:num_results],
            total_found=len(results),
            search_engine="sogou",
        )

    async def _search_with_fallback(
        self, query: str, num_results: int, time_range: str | None
    ) -> SearchResponse:
        """Try multiple search engines with automatic fallback."""
        # Try Serper first (best quality)
        if self._serper_api_key:
            try:
                return await self._search_serper(query, num_results, time_range)
            except Exception as exc:
                logger.warning("Serper search failed: %s", exc)

        # Try Bing
        if self._bing_api_key:
            try:
                return await self._search_bing(query, num_results, time_range)
            except Exception as exc:
                logger.warning("Bing search failed: %s", exc)

        # Fallback to Baidu scraping
        try:
            return await self._search_baidu_scrape(query, num_results)
        except Exception as exc:
            logger.warning("Baidu scrape failed: %s", exc)

        # Final fallback: Sogou scraping
        try:
            return await self._search_sogou_scrape(query, num_results)
        except Exception as exc:
            return SearchResponse(
                query=query,
                error=f"All search engines failed. Last error: {exc}",
            )


# =============================================================================
# Legal Search Summarizer
# =============================================================================

async def summarize_search_results(
    query: str,
    results: list[SearchResult],
    max_tokens: int = 2000,
) -> str:
    """Use LLM to summarize web search results for the legal question.

    Takes the raw search results and produces a coherent summary
    that can be used as context for the LLM response.
    """
    if not results:
        return ""

    from app.services.llm_service import get_raw_llm_service
    llm = get_raw_llm_service()

    # Build search context
    search_text_parts = []
    for i, r in enumerate(results[:10], 1):
        source_badge = " [权威来源]" if r.is_legal_source else ""
        search_text_parts.append(
            f"[{i}] {r.title}{source_badge}\n"
            f"    来源: {r.url}\n"
            f"    摘要: {r.snippet}"
        )
    search_text = "\n\n".join(search_text_parts)

    system_prompt = """你是一位法律信息整理专家。请根据以下网络搜索结果，提取与用户法律问题相关的关键信息。

要求：
1. 优先引用权威法律来源（法院、政府、官方机构）的信息
2. 标注信息的来源和时效性
3. 如果搜索结果中有最新法规变化，请重点标注
4. 去除无关信息，聚焦法律问题
5. 不要编造搜索结果中没有的信息"""

    try:
        result = await llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"用户问题：{query}\n\n搜索结果：\n{search_text}"},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return result.get("content", "")
    except Exception as exc:
        logger.warning("Search summarization failed: %s", exc)
        # Fallback: concatenate snippets
        snippets = [r.snippet for r in results[:5] if r.snippet]
        return "\n".join(snippets)


# =============================================================================
# Legal-Specific Web Search
# =============================================================================

class LegalWebSearch:
    """法律专用联网搜索 — 跨权威源并行检索。

    Sources:
    - 国家法律法规数据库 (flk.npc.gov.cn)
    - 最高人民法院 (court.gov.cn)
    - 中国政府网法规库 (gov.cn)
    - 通用搜索引擎 (Bing/Baidu)
    """

    LEGAL_SOURCES = {
        "npc_laws": {
            "name": "国家法律法规数据库",
            "url": "https://flk.npc.gov.cn/api/search",
            "authority": 100,
        },
        "court": {
            "name": "最高人民法院",
            "url": "https://www.court.gov.cn",
            "authority": 95,
        },
        "gov_regulations": {
            "name": "国务院法规库",
            "url": "https://www.gov.cn/zhengce",
            "authority": 90,
        },
    }

    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout

    async def search(
        self,
        query: str,
        categories: list[str] | None = None,
        max_results: int = 20,
    ) -> list[SearchResult]:
        """
        跨权威法律源并行搜索。

        Args:
            query: 搜索关键词
            categories: 搜索类别 ["laws", "cases", "interpretations", "general"]
            max_results: 最大结果数

        Returns:
            去重+排序后的搜索结果列表
        """
        if categories is None:
            categories = ["laws", "cases", "general"]

        tasks = []

        if "laws" in categories:
            tasks.append(self._search_npc_laws(query))
            tasks.append(self._search_gov_regulations(query))

        if "cases" in categories:
            tasks.append(self._search_court_cases(query))

        if "general" in categories:
            engine = get_web_search_engine()
            tasks.append(engine.search(f"法律 {query}", max_results=10))

        # 并行执行所有搜索
        results_groups = await asyncio.gather(*tasks, return_exceptions=True)

        all_results: list[SearchResult] = []
        for group in results_groups:
            if isinstance(group, Exception):
                logger.debug("Search source failed: %s", group)
                continue
            if isinstance(group, list):
                all_results.extend(group)

        # 去重 (按URL)
        seen_urls: set[str] = set()
        unique_results: list[SearchResult] = []
        for r in all_results:
            url = r.url if hasattr(r, 'url') else r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_results.append(r)

        # 按权威度排序
        unique_results.sort(
            key=lambda r: (
                getattr(r, "authority", 50) if hasattr(r, "authority") else 50
            ),
            reverse=True,
        )

        return unique_results[:max_results]

    async def _search_npc_laws(self, query: str) -> list[SearchResult]:
        """搜索国家法律法规数据库"""
        try:
            encoded_q = quote_plus(query)
            url = f"https://flk.npc.gov.cn/api/search?q={encoded_q}&page=1&pageSize=10"
            async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json",
                })
                if resp.status_code != 200:
                    return []

                data = resp.json()
                results = []
                items = data.get("result", data.get("data", []))
                if isinstance(items, list):
                    for item in items[:10]:
                        title = item.get("title", item.get("name", ""))
                        content = item.get("content", item.get("summary", ""))
                        results.append(SearchResult(
                            title=title,
                            url=item.get("url", "https://flk.npc.gov.cn"),
                            snippet=content[:300] if content else title,
                            source="npc_laws",
                        ))
                return results
        except Exception as e:
            logger.debug("NPC laws search failed: %s", e)
            return []

    async def _search_gov_regulations(self, query: str) -> list[SearchResult]:
        """搜索国务院法规库"""
        try:
            encoded_q = quote_plus(query)
            url = f"https://www.gov.cn/zhengce/copyright/zuixin/index.htm?searchWord={encoded_q}"
            async with httpx.AsyncClient(timeout=self._timeout, verify=False, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    return []
                # 从HTML中提取结果 (简化)
                text = resp.text
                titles = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>([^<]{5,100})</a>', text)
                results = []
                for href, title in titles[:10]:
                    if "zhengce" in href or "policy" in href:
                        results.append(SearchResult(
                            title=title.strip(),
                            url=href if href.startswith("http") else f"https://www.gov.cn{href}",
                            snippet=title.strip(),
                            source="gov_regulations",
                        ))
                return results
        except Exception as e:
            logger.debug("Gov regulations search failed: %s", e)
            return []

    async def _search_court_cases(self, query: str) -> list[SearchResult]:
        """搜索最高法案例"""
        try:
            encoded_q = quote_plus(query)
            url = f"https://www.court.gov.cn/zixun/gengxin/{encoded_q}.html"
            async with httpx.AsyncClient(timeout=self._timeout, verify=False, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    return []
                text = resp.text
                titles = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>([^<]{8,150})</a>', text)
                results = []
                for href, title in titles[:10]:
                    title = title.strip()
                    if len(title) > 10:
                        results.append(SearchResult(
                            title=title,
                            url=href if href.startswith("http") else f"https://www.court.gov.cn{href}",
                            snippet=title,
                            source="court",
                        ))
                return results
        except Exception as e:
            logger.debug("Court cases search failed: %s", e)
            return []


# =============================================================================
# Singleton
# =============================================================================

_search_engine: WebSearchEngine | None = None
_legal_search: LegalWebSearch | None = None


def get_web_search_engine() -> WebSearchEngine:
    """Get the singleton WebSearchEngine instance."""
    global _search_engine
    if _search_engine is None:
        _search_engine = WebSearchEngine()
    return _search_engine


def get_legal_web_search() -> LegalWebSearch:
    """Get the singleton LegalWebSearch instance."""
    global _legal_search
    if _legal_search is None:
        _legal_search = LegalWebSearch()
    return _legal_search
