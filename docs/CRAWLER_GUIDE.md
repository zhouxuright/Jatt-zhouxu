# 法律知识库爬虫使用指南

## 概述

本系统提供了一套完整的法律知识库爬虫和数据导入工具，用于从公开数据源爬取法律条文和案例，并导入到 PostgreSQL 和 Milvus 向量数据库中。

## 快速开始

### 1. 测试爬虫功能

```bash
cd backend
python scripts/test_crawler.py
```

这个脚本会测试爬虫的各项功能，包括：
- 反爬虫工具
- 断点续传
- 文本解析
- 数据清洗
- 向量嵌入
- 关键词提取

### 2. 启动爬虫

#### 单次爬取（测试用）

```bash
python scripts/start_crawler.py --source wenshu --pages 10
```

这会爬取裁判文书网的前 10 页数据，并保存到 `./crawler_data` 目录。

#### 持续爬取（生产用）

```bash
python scripts/start_crawler.py --source wenshu --continuous
```

这会持续爬取裁判文书网数据，直到手动停止（Ctrl+C）。

### 3. 处理爬取的数据

```bash
python scripts/start_crawler.py --process ./crawler_data
```

这会对爬取的原始数据进行清洗、结构化、关键词提取和向量嵌入，处理后的数据保存到 `./crawler_data_processed` 目录。

### 4. 导入数据到数据库

```bash
python scripts/start_crawler.py --import ./crawler_data_processed
```

这会将处理后的数据批量导入到：
- PostgreSQL（结构化数据）
- Milvus（向量数据）

## 完整工作流程

### 第一步：爬取数据

```bash
# 爬取 100 页裁判文书
python scripts/start_crawler.py --source wenshu --pages 100
```

数据会保存到 `./crawler_data/` 目录，每个裁判文书一个 JSON 文件。

### 第二步：处理数据

```bash
# 处理爬取的数据
python scripts/start_crawler.py --process ./crawler_data
```

处理后的数据保存到 `./crawler_data_processed/` 目录。

### 第三步：导入数据库

```bash
# 导入到 PostgreSQL 和 Milvus
python scripts/start_crawler.py --import ./crawler_data_processed
```

## 数据源

### 裁判文书网 (wenshu.court.gov.cn)

- **数据量**: 1.3 亿+ 裁判文书
- **数据类型**: 判决书、裁定书、调解书
- **爬取难度**: 中等（需要反爬虫处理）
- **实现状态**: ✅ 已完成

### 其他数据源（待实现）

- 法律法规信息网 (pkulaw.com) - 100 万+ 法律法规
- 国家知识产权局 (cnipa.gov.cn) - 5000 万+ 专利
- 市场监管行政处罚文书网 - 1000 万+ 行政处罚案例

## 配置说明

### 爬虫配置

编辑 `backend/app/crawlers/wenshu_crawler.py` 中的 `CrawlerConfig` 类：

```python
class CrawlerConfig:
    # 请求间隔（秒）
    MIN_DELAY = 2.0
    MAX_DELAY = 5.0

    # 并发请求数
    MAX_CONCURRENT = 3

    # 最大爬取页数
    MAX_PAGES = 100

    # 数据存储目录
    DATA_DIR = "./crawler_data"
```

### 代理 IP 配置（生产环境）

生产环境建议配置代理 IP 池，避免被封：

```python
# 在 CrawlerConfig 中添加
PROXY_POOL = [
    "http://proxy1:port",
    "http://proxy2:port",
    # ...
]
```

## 数据格式

### 原始数据格式（JSON）

```json
{
  "doc_id": "abc123",
  "title": "张某诉李某合同纠纷案",
  "case_number": "(2023)京01民终12345号",
  "court_name": "北京市第一中级人民法院",
  "case_type": "民事",
  "cause_of_action": "合同纠纷",
  "decision_date": "2023-06-15",
  "parties": "原告：张某\n被告：李某",
  "summary": "原告与被告于2022年签订购销合同...",
  "full_text": "完整的裁判文书内容...",
  "key_points": "依法成立的合同受法律保护...",
  "referenced_laws": "《民法典》第五百七十七条",
  "judgment_result": "被告应于本判决生效之日起十日内支付原告货款10万元。",
  "tags": "合同纠纷,民法典,违约责任",
  "crawl_time": "2026-08-15T20:00:00"
}
```

### 数据库模型

#### PostgreSQL

- `laws` - 法律基本信息
- `legal_articles` - 法律条文
- `court_cases` - 裁判文书
- `judicial_interpretations` - 司法解释
- `legal_concepts` - 法律概念

#### Milvus

- `legal_articles` - 法律条文向量（1024 维）
- `court_cases` - 裁判文书向量（1024 维）

## 性能优化

### 爬虫性能

- **请求间隔**: 2-5 秒（避免被封）
- **并发数**: 3 个并发请求
- **断点续传**: 支持中断后继续
- **去重机制**: 使用 Bloom Filter 去重

### 数据库性能

- **批量导入**: 每批 100 条记录
- **向量索引**: 使用 HNSW 索引
- **分区策略**: 按案件类型分区

### 预估性能

- **爬取速度**: 约 1000 条/小时（受反爬虫限制）
- **处理速度**: 约 5000 条/小时
- **导入速度**: 约 10000 条/小时

## 监控和日志

### 日志级别

```bash
# 设置日志级别为 INFO
export LOG_LEVEL=INFO

# 启动爬虫
python scripts/start_crawler.py --source wenshu --continuous
```

### 统计信息

爬虫会输出以下统计信息：

```
进度: 1000 条 | 错误: 5
```

- `进度`: 已爬取的数据条数
- `错误`: 爬取失败的条数

## 故障排查

### 问题 1: 爬取速度慢

**原因**: 请求间隔太长或并发数太低

**解决**: 调整 `CrawlerConfig` 中的 `MIN_DELAY`、`MAX_DELAY` 和 `MAX_CONCURRENT`

### 问题 2: 被封 IP

**原因**: 请求频率太高

**解决**: 
1. 增加请求间隔
2. 配置代理 IP 池
3. 降低并发数

### 问题 3: 数据导入失败

**原因**: 数据库连接问题或数据格式错误

**解决**:
1. 检查数据库连接
2. 查看错误日志
3. 验证数据格式

## 法律合规

### 数据来源

- 仅爬取公开数据
- 遵守各网站的 robots.txt
- 不爬取需要登录的数据

### 数据使用

- 仅用于法律研究和公共服务
- 不进行商业用途
- 保护个人隐私信息

### 免责声明

本工具仅用于学习和研究目的，使用者需自行承担使用本工具的法律风险。

## 相关文件

- `backend/app/crawlers/wenshu_crawler.py` - 裁判文书网爬虫
- `backend/app/crawlers/document_processor.py` - 数据处理器
- `backend/scripts/batch_import.py` - 批量导入工具
- `backend/scripts/start_crawler.py` - 爬虫启动脚本
- `backend/scripts/test_crawler.py` - 爬虫测试脚本
- `backend/app/rag/milvus_optimization.py` - Milvus 优化方案
- `docs/DATA_EXPANSION_PLAN.md` - 数据扩充详细计划
- `docs/KNOWLEDGE_BASE_SUMMARY.md` - 知识库扩充总结

## 联系和支持

如有问题或建议，请提交 Issue 或联系项目团队。

---

**最后更新**: 2026-08-15
**版本**: v1.0.0
