"""Proxy pool service for web crawling.

Connects to the open-source proxy_pool service to get rotating IP proxies.
Falls back to direct connection if proxy pool is unavailable.
"""

import logging
import os
import random
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Proxy pool API (proxy_pool by jhao104)
PROXY_POOL_URL = os.environ.get("PROXY_POOL_URL", "http://proxy_pool:5010")
PROXY_POOL_ENABLED = os.environ.get("PROXY_POOL_ENABLED", "false").lower() == "true"


class ProxyService:
    """Manages proxy pool connections for web crawlers."""

    def __init__(self):
        self._proxy_pool_url = PROXY_POOL_URL
        self._enabled = PROXY_POOL_ENABLED
        self._cached_proxies: list[str] = []
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def get_proxy(self) -> Optional[str]:
        """Get a random proxy from the pool.

        Returns proxy URL like 'http://1.2.3.4:8080' or None if unavailable.
        """
        if not self._enabled:
            return None

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                # Get a random proxy
                resp = await client.get(f"{self._proxy_pool_url}/get/")
                if resp.status_code == 200:
                    data = resp.json()
                    proxy = data.get("proxy")
                    if proxy:
                        return f"http://{proxy}"
        except Exception as e:
            logger.debug("Failed to get proxy from pool: %s", e)

        return None

    async def get_proxies(self, count: int = 10) -> list[str]:
        """Get multiple proxies from the pool."""
        if not self._enabled:
            return []

        proxies = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self._proxy_pool_url}/get_all/")
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data:
                        proxy = item.get("proxy")
                        if proxy:
                            proxies.append(f"http://{proxy}")
        except Exception as e:
            logger.debug("Failed to get proxies: %s", e)

        return proxies[:count] if proxies else []

    async def delete_proxy(self, proxy: str) -> None:
        """Remove a proxy from the pool (if it's not working)."""
        if not self._enabled:
            return

        proxy_addr = proxy.replace("http://", "").replace("https://", "")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.get(f"{self._proxy_pool_url}/delete/", params={"proxy": proxy_addr})
        except Exception as e:
            logger.debug("Failed to delete proxy: %s", e)

    async def get_pool_status(self) -> dict:
        """Get proxy pool status."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._proxy_pool_url}/get_all/")
                if resp.status_code == 200:
                    proxies = resp.json()
                    return {
                        "enabled": True,
                        "pool_size": len(proxies),
                        "url": self._proxy_pool_url,
                    }
        except Exception:
            pass

        return {"enabled": self._enabled, "pool_size": 0, "url": self._proxy_pool_url}

    async def get_crawl_client(self, timeout: float = 30) -> httpx.AsyncClient:
        """Get an httpx client configured with a proxy."""
        proxy = await self.get_proxy()

        if proxy:
            logger.info("Using proxy: %s", proxy)
            return httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                proxy=proxy,
                headers={
                    "User-Agent": _random_ua(),
                },
            )

        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": _random_ua(),
            },
        )


def _random_ua() -> str:
    """Return a random User-Agent string."""
    agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    ]
    return random.choice(agents)


# Singleton
_proxy_service: Optional[ProxyService] = None

def get_proxy_service() -> ProxyService:
    global _proxy_service
    if _proxy_service is None:
        _proxy_service = ProxyService()
    return _proxy_service
