# Bright Data 代理 IP 配置指南

## 当前状态

代理 IP 已集成到爬虫系统中，但需要正确配置 Bright Data zone 才能使用。

## 问题说明

当前配置的 zone `serp_api2` 不支持裁判文书网（wenshu.court.gov.cn），需要使用以下方案之一：

### 方案 1：使用 Web Unlocker（推荐）

Web Unlocker 可以访问任意网站，包括有反爬虫保护的网站。

**配置步骤：**

1. 登录 Bright Data 后台：https://brightdata.com/
2. 进入 "Proxies & Tools" → "Web Unlocker"
3. 创建一个新的 zone（例如命名为 `web_unlocker_china`）
4. 修改爬虫配置：

```python
# backend/app/crawlers/wenshu_crawler.py
BRIGHTDATA_ZONE = "web_unlocker_china"  # 使用你创建的 zone 名称
```

### 方案 2：使用 Scraping Browser

Scraping Browser 可以模拟真实浏览器，绕过复杂的反爬虫机制。

**配置步骤：**

1. 在 Bright Data 后台创建 Scraping Browser zone
2. 修改爬虫配置使用 Scraping Browser API

### 方案 3：不使用代理（当前可用）

如果 Bright Data 配置不可用，爬虫会自动降级为直接请求模式。

**配置：**

```python
# backend/app/crawlers/wenshu_crawler.py
USE_PROXY = False  # 禁用代理
```

**注意：** 直接请求可能会被裁判文书网封禁 IP，建议：
- 控制爬取频率（2-5秒/请求）
- 使用随机 User-Agent
- 定期更换 IP（如果可能）

## 测试代理连接

```bash
cd backend
python scripts/test_proxy.py
```

预期输出：
- 如果代理配置正确：显示代理请求成功
- 如果代理配置错误：显示 zone not found，自动降级为直接请求

## 启动爬虫

### 不使用代理（当前可用）

```bash
# 禁用代理
python scripts/start_crawler.py --source wenshu --pages 10
```

### 使用代理（需要正确配置 zone）

```bash
# 启用代理（确保 BRIGHTDATA_ZONE 配置正确）
python scripts/start_crawler.py --source wenshu --continuous
```

## 成本估算

Bright Data Web Unlocker 定价（参考）：
- 每 1000 次请求：约 $5-10
- 爬取 1 亿条文书：约 $50,000-100,000

**建议：**
1. 先使用直接请求模式测试爬虫功能
2. 确认爬虫稳定后，再考虑使用代理
3. 根据实际爬取量评估代理成本

## 替代方案

如果 Bright Data 成本过高，可以考虑：

1. **自建代理池**
   - 使用开源代理软件（如 ProxyPool）
   - 成本：服务器费用 + 代理 IP 费用
   - 优点：可控性强，成本相对较低

2. **其他代理服务**
   - Oxylabs
   - Smartproxy
   - Luminati
   - 价格各不相同，需要对比测试

3. **分布式爬虫**
   - 在多台服务器上运行爬虫
   - 每台服务器使用不同的 IP
   - 降低被封风险

## 当前配置

```python
# backend/app/crawlers/wenshu_crawler.py

BRIGHTDATA_API_KEY = "177f5ba6-2198-4898-a907-8a2fcc41f48c"
BRIGHTDATA_API_URL = "https://api.brightdata.com/request"
BRIGHTDATA_ZONE = "serp_api2"  # 需要在后台创建此 zone
USE_PROXY = True  # 如果代理不可用，会自动降级为直接请求
```

## 下一步行动

1. ✅ 代理 IP 已集成到爬虫代码
2. ⏳ 在 Bright Data 后台创建正确的 zone
3. ⏳ 更新 zone 配置
4. ⏳ 测试代理连接
5. ⏳ 启动爬虫

## 联系支持

如果在配置过程中遇到问题：
- Bright Data 官方文档：https://docs.brightdata.com/
- Bright Data 客服支持：通过后台工单系统

---

**最后更新**: 2026-08-15
**状态**: 代理集成完成，需要配置正确的 zone
