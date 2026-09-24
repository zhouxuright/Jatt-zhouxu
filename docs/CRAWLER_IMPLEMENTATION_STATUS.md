# 裁判文书网爬虫实施总结

## 当前状态

✅ **已完成**:
1. 爬虫框架搭建完成
2. 代理 IP 集成完成（Bright Data）
3. 数据清洗和处理流程完成
4. 批量导入工具完成
5. 测试脚本完成

⚠️ **遇到的问题**:
1. **Bright Data zone 配置**: 需要正确创建 zone 才能使用代理
2. **裁判文书网反爬虫**: API 需要正确的加密参数（ciphertext 和 __RequestVerificationToken）

## 问题分析

### 1. Bright Data 代理问题

**现状**: 
- API Key 已配置：`177f5ba6-2198-4898-a907-8a2fcc41f48c`
- Zone `serp_api2` 不支持裁判文书网
- 需要创建 Web Unlocker zone

**解决方案**:
1. 登录 Bright Data 后台
2. 创建 Web Unlocker zone
3. 更新配置中的 zone 名称
4. 重新测试

**参考文档**: [PROXY_CONFIG_GUIDE.md](./PROXY_CONFIG_GUIDE.md)

### 2. 裁判文书网反爬虫问题

**现状**:
- 裁判文书网 API 使用加密参数验证
- 当前的简化版加密算法无法通过验证
- 需要逆向工程的加密算法

**解决方案**:

#### 方案 A: 使用 Selenium/Playwright 模拟浏览器（推荐）

优点：
- 可以绕过大部分反爬虫机制
- 不需要逆向工程加密算法
- 可以处理 JavaScript 渲染

缺点：
- 速度较慢
- 资源消耗较大

实现代码：

```python
from playwright.async_api import async_playwright

async def crawl_with_browser():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # 访问裁判文书网
        await page.goto("https://wenshu.court.gov.cn/")
        
        # 等待页面加载
        await page.wait_for_selector(".search-input")
        
        # 执行搜索
        await page.fill(".search-input", "合同纠纷")
        await page.click(".search-button")
        
        # 等待结果加载
        await page.wait_for_selector(".result-item")
        
        # 提取数据
        results = await page.query_selector_all(".result-item")
        for result in results:
            # 提取文书信息
            title = await result.query_selector(".title")
            # ...
        
        await browser.close()
```

#### 方案 B: 逆向工程加密算法

优点：
- 速度快
- 资源消耗小

缺点：
- 需要逆向工程
- 算法可能随时变化
- 维护成本高

需要分析的内容：
1. ciphertext 生成算法
2. __RequestVerificationToken 生成算法
3. 请求签名算法

#### 方案 C: 使用第三方数据源

替代数据源：
1. **pkulaw.com** (北大法宝) - 法律法规数据
2. **openlaw.cn** (开放法律) - 裁判文书数据
3. **alphalawyer.cn** - 法律数据

优点：
- 反爬虫机制较弱
- 数据格式更规范

缺点：
- 数据量可能较少
- 需要付费

## 建议的下一步行动

### 短期（1-2 周）

1. **配置 Bright Data 代理**
   - 创建 Web Unlocker zone
   - 测试代理连接
   - 更新爬虫配置

2. **实现 Playwright 爬虫**
   - 使用浏览器模拟方式爬取
   - 绕过反爬虫机制
   - 测试稳定性

3. **测试其他数据源**
   - 测试 pkulaw.com
   - 测试 openlaw.cn
   - 评估数据质量

### 中期（1-2 个月）

1. **优化爬虫性能**
   - 提高爬取速度
   - 降低资源消耗
   - 实现分布式爬取

2. **扩大数据规模**
   - 持续爬取裁判文书
   - 整合多个数据源
   - 达到 100 万条数据

3. **完善数据处理**
   - 优化数据清洗
   - 提高结构化质量
   - 完善向量嵌入

### 长期（3-6 个月）

1. **达到 1000 万条数据**
2. **优化向量检索性能**
3. **实现数据飞轮**
4. **支持商业化应用**

## 代码实现建议

### 1. 添加 Playwright 爬虫

创建新文件：`backend/app/crawlers/playwright_crawler.py`

```python
"""使用 Playwright 爬取裁判文书网"""

import asyncio
from playwright.async_api import async_playwright

class PlaywrightCrawler:
    def __init__(self):
        self.browser = None
        self.page = None
    
    async def start(self):
        p = await async_playwright().start()
        self.browser = await p.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
    
    async def crawl_list(self, page_num=1):
        url = f"https://wenshu.court.gov.cn/website/wenshu/181029CR4M5A62CH/index.html?pageId=xxx&page={page_num}"
        await self.page.goto(url)
        await self.page.wait_for_selector(".result-list")
        
        # 提取文书列表
        items = await self.page.query_selector_all(".result-item")
        results = []
        for item in items:
            title = await item.query_selector(".title")
            case_num = await item.query_selector(".case-num")
            results.append({
                "title": await title.inner_text(),
                "case_number": await case_num.inner_text(),
            })
        return results
    
    async def crawl_detail(self, doc_id):
        url = f"https://wenshu.court.gov.cn/website/wenshu/181107ANFZ0BXSK4/index.html?docId={doc_id}"
        await self.page.goto(url)
        await self.page.wait_for_selector(".doc-content")
        
        content = await self.page.query_selector(".doc-content")
        return {
            "full_text": await content.inner_text(),
        }
    
    async def close(self):
        if self.browser:
            await self.browser.close()
```

### 2. 更新依赖

在 `backend/requirements.txt` 中添加：

```
playwright==1.40.0
```

安装浏览器：

```bash
playwright install chromium
```

### 3. 添加其他数据源爬虫

创建：`backend/app/crawlers/pkulaw_crawler.py`

```python
"""爬取北大法宝法律法规数据"""

class PkulawCrawler:
    BASE_URL = "https://www.pkulaw.com"
    
    async def crawl_law(self, law_id):
        # 爬取法律条文
        pass
    
    async def crawl_all_laws(self):
        # 爬取所有法律法规
        pass
```

## 总结

### 已完成

✅ 爬虫框架（基础架构）
✅ 代理 IP 集成（Bright Data）
✅ 数据处理流程
✅ 批量导入工具
✅ 测试和文档

### 待完成

⏳ 配置 Bright Data zone
⏳ 实现 Playwright 爬虫（绕过反爬虫）
⏳ 测试其他数据源
⏳ 持续爬取数据
⏳ 达到目标数据量

### 关键决策

**选择 Playwright 方案**，原因：
1. 不需要逆向工程
2. 可以绕过大部分反爬虫
3. 维护成本低
4. 可以处理 JavaScript 渲染

**预计时间线**:
- 1 周：实现 Playwright 爬虫
- 2 周：测试和优化
- 1 个月：持续爬取，达到 100 万条
- 3 个月：达到 1000 万条
- 6 个月：达到 1 亿条

---

**最后更新**: 2026-08-15
**状态**: 爬虫框架完成，需要实现 Playwright 方案绕过反爬虫
