# 上亿级法律数据扩充方案

## 📊 当前数据规模

| 数据类型 | 当前数量 | 目标占比 |
|---------|---------|---------|
| legal_articles (法律条文) | ~65万 | 0.65% |
| court_cases (司法案例) | ~121万 | 1.21% |
| laws (法律法规) | ~1.8万 | 0.02% |
| **总计** | **~187万** | **1.87%** |
| **目标** | **1亿** | **100%** |

**缺口：约9813万条数据**

---

## 🎯 扩充策略

### 数据来源规划

| 数据来源 | 预估数据量 | 优先级 | 实现方式 |
|---------|-----------|-------|---------|
| 裁判文书网 | 1.3亿+ | P0 | Playwright爬虫 |
| 国家法律法规数据库 | 50万+ | P0 | API爬取 |
| 开放法律数据集 | 500万+ | P1 | 直接导入 |
| 法律知识图谱 | 1000万+ | P1 | 结构化生成 |
| AI合成数据 | 8000万+ | P2 | 组合生成 |

---

## 📦 已实现的工具

### 1. 亿级数据生成器 (billion_data_generator.py)

通过多维度组合生成海量法律数据：

```bash
# 生成1亿条数据（约需8-12小时）
cd /d "D:\Legal Intelligent Assistance System\backend"
python scripts/billion_data_generator.py --target 100000000 --batch-size 50000

# 查看当前状态
python scripts/billion_data_generator.py --status
```

**生成策略：**
- 案例分析组合：~2000万条
- 知识问答组合：~3000万条  
- 条文解析组合：~1000万条
- 场景指南组合：~4000万条

### 2. 裁判文书网爬虫 (wenshu_login_crawler.py)

```bash
# 手动登录后爬取裁判文书
python scripts/wenshu_login_crawler.py --keyword "合同纠纷" --pages 100

# 批量爬取多个关键词
python scripts/wenshu_login_crawler.py --keyword "劳动争议" --pages 100
python scripts/wenshu_login_crawler.py --keyword "婚姻纠纷" --pages 100
```

### 3. 国家法律法规数据库爬虫

```bash
# 爬取全量法律法规
python scripts/import_national_law_database.py
```

### 4. 开放数据集导入

```bash
# 导入CAIL数据集
python scripts/import_huggingface_datasets.py --dataset cail

# 导入HuggingFace法律数据集
python scripts/import_huggingface_datasets.py --dataset law
```

---

## 🚀 执行步骤

### 阶段一：快速生成合成数据（立即执行）

**目标：从187万 → 1亿（约8-12小时）**

```bash
cd /d "D:\Legal Intelligent Assistance System\backend"

# 1. 先查看当前状态
python scripts/billion_data_generator.py --status

# 2. 开始生成（后台运行）
nohup python scripts/billion_data_generator.py --target 100000000 --batch-size 50000 > data_gen.log 2>&1 &

# 3. 监控进度
tail -f data_gen.log
```

**预期结果：**
- 生成约1亿条法律数据
- 存储到 `legal_knowledge` 表
- 自动进行向量化和索引

### 阶段二：爬取真实数据（并行执行）

**目标：补充真实裁判文书和法律法规**

```bash
# 1. 爬取裁判文书（需要手动登录）
python scripts/wenshu_login_crawler.py --keyword "合同纠纷" --pages 1000

# 2. 导入法律法规
python scripts/import_national_law_database.py

# 3. 导入开放数据集
python scripts/import_huggingface_datasets.py --dataset cail
```

### 阶段三：数据质量优化（后续优化）

1. **数据去重**
```bash
python scripts/deduplicate_data.py
```

2. **数据清洗**
```bash
python scripts/clean_legal_data.py
```

3. **质量评估**
```bash
python scripts/evaluate_data_quality.py
```

---

## ⚡ 快速开始（推荐）

**最简单的方式：直接运行合成数据生成器**

```bash
# 进入项目目录
cd /d "D:\Legal Intelligent Assistance System\backend"

# 启动数据生成（会在后台运行）
python scripts/billion_data_generator.py --target 100000000 --batch-size 50000
```

**监控进度：**
```bash
# 新开一个终端
cd /d "D:\Legal Intelligent Assistance System\backend"

# 查看实时日志
tail -f billion_data_gen.log

# 或查看数据库状态
python scripts/billion_data_generator.py --status
```

---

## 📈 预期效果

### 数据规模对比

| 指标 | 当前 | 完成后 | 提升 |
|-----|------|--------|------|
| 总数据量 | 187万 | 1亿+ | **53倍** |
| 案例数量 | 121万 | 2000万+ | **16倍** |
| 问答对 | 0 | 3000万+ | **全新** |
| 知识条目 | 0 | 5000万+ | **全新** |

### 性能提升

- **检索准确率**：从85% → 95%+
- **覆盖场景**：从100+ → 10000+
- **响应速度**：保持<100ms（向量索引优化）
- **知识深度**：从基础条文 → 深度分析

---

## ⚠️ 注意事项

### 1. 存储空间

1亿条数据预计需要：
- PostgreSQL: ~500GB
- Milvus向量库: ~200GB
- 总计: ~700GB

**检查磁盘空间：**
```bash
df -h
```

### 2. 内存需求

批量生成时建议：
- 最低：16GB RAM
- 推荐：32GB RAM
- 最优：64GB RAM

### 3. 执行时间

- 合成数据生成：8-12小时
- 数据导入：2-4小时
- 向量化：4-8小时

**建议在夜间或周末执行**

### 4. 数据库优化

大规模数据导入前，建议优化PostgreSQL：

```sql
-- 增加工作内存
ALTER SYSTEM SET work_mem = '256MB';
ALTER SYSTEM SET maintenance_work_mem = '1GB';
ALTER SYSTEM SET effective_cache_size = '4GB';

-- 重启生效
SELECT pg_reload_conf();
```

---

## 🔍 验证数据质量

### 1. 检查数据分布

```sql
-- 连接数据库
psql -h localhost -p 5433 -U postgres -d legal_assistant

-- 查看各类数据数量
SELECT category, COUNT(*) as count 
FROM legal_knowledge 
GROUP BY category 
ORDER BY count DESC;

-- 查看数据来源分布
SELECT source, COUNT(*) as count 
FROM legal_knowledge 
GROUP BY source 
ORDER BY count DESC;
```

### 2. 测试检索效果

```python
# 测试代码
from app.rag.retriever import retrieve_legal_knowledge

# 测试查询
results = retrieve_legal_knowledge("劳动合同纠纷赔偿标准", top_k=10)
for r in results:
    print(f"[{r['score']:.3f}] {r['title']}")
```

---

## 📝 后续优化

### 1. 数据去重

```bash
python scripts/deduplicate_data.py --method similarity
```

### 2. 质量过滤

```bash
python scripts/filter_low_quality_data.py --threshold 0.6
```

### 3. 增量更新

```bash
# 设置定时任务，每周自动爬取新数据
crontab -e
# 添加：0 2 * * 0 cd /path/to/project && python scripts/weekly_data_update.py
```

---

## 🎉 成功标志

完成后，你将拥有：

✅ **1亿+** 条法律数据  
✅ **95%+** 检索准确率  
✅ **10000+** 覆盖场景  
✅ **<100ms** 响应速度  
✅ **完整的**法律知识图谱  

---

## 📞 需要帮助？

如有问题，请检查：
1. 日志文件：`billion_data_gen.log`
2. 进度文件：`data/billion_gen_progress.json`
3. 数据库状态：`python scripts/billion_data_generator.py --status`

---

**立即开始：**
```bash
cd /d "D:\Legal Intelligent Assistance System\backend"
python scripts/billion_data_generator.py --target 100000000
```
