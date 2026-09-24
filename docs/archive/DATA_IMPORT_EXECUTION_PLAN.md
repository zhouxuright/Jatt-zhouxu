# 数据导入与爬虫执行计划

**创建时间**: 2026-09-08
**目标**: 执行Phase 2数据规模扩充任务，将数据从1800万提升到商业化标准

## 📊 当前数据状态

### 已生成数据（待导入）
- **合成QA对**: 10,000,000 条 (12.5GB) - `backend/data/mass_expansion/synthetic_qa_pairs.jsonl`
- **合成案例**: 5,000,000 条 (3.6GB) - `backend/data/mass_expansion/synthetic_cases.jsonl`
- **条文解释**: 70 条 - `backend/data/mass_expansion/article_explanations.jsonl`
- **现有数据**: 18,037,949 条（含PostgreSQL 1.8M + Milvus 948K + 其他）

### 待爬取数据
- **裁判文书**: ~10,000,000 条（裁判文书网）
- **法律法规**: ~500,000 条（法律法规数据库）

## 🎯 执行任务

### 任务1: 合成数据导入到PostgreSQL ✅ 待执行

**脚本**: `backend/app/rag/mass_import/cli.py`
**命令**:
```bash
cd backend
python -m app.rag.mass_import --phase pg_import --resume
```

**说明**:
- 使用5阶段管道：PARSE → DEDUP → PG_IMPORT → EMBED → MILVUS
- 支持断点续传（checkpoint机制）
- 自动去重（ON CONFLICT DO NOTHING）
- 预计耗时：2-4小时（取决于数据量）

**注意事项**:
- 确保PostgreSQL服务运行中：`docker-compose up -d postgres`
- 检查数据库连接：`psql -U postgres -d legal_assistant`
- 监控导入进度：查看日志输出

---

### 任务2: 合成数据向量化导入到Milvus ⏳ 待执行

**前置条件**:
- Milvus服务运行中：`docker-compose --profile full up -d milvus`
- PostgreSQL导入完成
- 足够的磁盘空间（~50GB用于向量数据）

**命令**:
```bash
cd backend
python -m app.rag.mass_import --phase milvus --resume
```

**说明**:
- 使用BGE-M3模型生成1024维向量
- 支持GPU加速（`--device cuda`）
- 断点续传机制
- 预计耗时：8-12小时（CPU）或2-3小时（GPU）

**性能优化**:
- 批次大小：1000条/批
- 并发嵌入：4个worker
- 内存限制：调整`EMBEDDING_BATCH_SIZE`环境变量

---

### 任务3: 裁判文书网爬虫启动 ⏳ 待执行

**脚本**: `backend/app/crawlers/wenshu_crawler.py`

**执行前准备**:
1. 检查爬虫配置
   ```python
   # CrawlerConfig类
   MIN_DELAY = 2.0  # 请求间隔（秒）
   MAX_CONCURRENT = 3  # 并发数
   MAX_PAGES = 100  # 测试模式，生产环境改为10000+
   USE_PROXY = False  # 生产环境建议开启代理池
   ```

2. 确保依赖服务运行
   ```bash
   docker-compose up -d redis  # 代理池需要Redis
   ```

3. 配置代理（可选但推荐）
   ```bash
   # 在.env中配置
   WENSHU_PROXY_URL=http://proxy_pool:5010
   ```

**执行命令**:
```bash
cd backend
python -m app.crawlers.wenshu_crawler
```

**说明**:
- 遵守robots.txt，控制爬取频率
- 支持断点续传（checkpoint文件）
- 自动重试失败请求
- 数据保存到`crawler_data/`目录

**预计耗时**:
- 测试模式（100页）：30分钟
- 生产模式（10000页）：50-100小时（持续运行）

**法律合规提醒**:
- 仅用于法律研究和公共服务
- 遵守裁判文书网使用条款
- 不得用于商业用途或二次分发

---

### 任务4: 法律法规全量下载 ⏳ 待执行

**脚本**: `backend/app/crawlers/flk_crawler.py`

**执行命令**:
```bash
cd backend
python -m app.crawlers.flk_crawler --all
```

**说明**:
- 爬取法律法规数据库（flk.npc.gov.cn）
- 包括法律、行政法规、司法解释、部门规章等
- 支持增量更新（基于effective_date）
- 数据保存到`data/open_law/`目录

**预计耗时**: 2-4小时

---

### 任务5: HuggingFace开源数据集导入 ⏳ 待执行

**脚本**: `backend/app/rag/download_opensource_datasets.py`

**执行命令**:
```bash
cd backend
python -m app.rag.download_opensource_datasets
```

**数据集列表**:
- CAIL2018: 260万条刑事案件
- DISC-LawLLM: 50万条SFT样本
- InternLM-Law: 100万条法律数据
- Chinese-Law-SFT: 10万条问答对
- LaWGPT: 20万条法律知识
- Leven: 8116条刑事案例
- 司法考试: 3万条

**说明**:
- 需要HuggingFace Token（已在.env中配置）
- 自动下载到`data/phase2_datasets/`
- 下载后需执行导入管道

**预计耗时**: 下载4-6小时 + 导入2-3小时

---

### 任务6: Neo4j知识图谱填充 ⏳ 进行中

**脚本**: `backend/app/rag/legal_knowledge_graph.py`

**执行命令**:
```bash
cd backend
python -m app.rag.legal_knowledge_graph --seed
```

**说明**:
- 构建法条、案例、罪名、法院、律师之间的关系图谱
- 实体：法条、案例、罪名、法院、律师
- 关系：引用、参照、类似、上诉、推翻
- 需要Neo4j服务运行：`docker-compose --profile full up -d neo4j`

**预计耗时**: 1-2小时

---

## 📋 执行顺序建议

**阶段1（立即执行，本周）**:
1. ✅ 启动PostgreSQL和Redis服务
2. ✅ 执行任务1：合成数据导入PostgreSQL
3. ⏳ 执行任务4：法律法规全量下载
4. ⏳ 执行任务5：HuggingFace数据集下载

**阶段2（下周）**:
5. ⏳ 启动Milvus服务
6. ⏳ 执行任务2：向量化导入Milvus
7. ⏳ 执行任务3：启动裁判文书网爬虫（后台持续运行）
8. ⏳ 执行任务6：Neo4j知识图谱填充

**阶段3（持续进行）**:
9. 监控爬虫进度
10. 定期执行增量更新
11. 验证数据质量

---

## 🔧 依赖服务检查清单

执行前确保以下服务运行：

```bash
# 基础服务（必需）
docker-compose up -d postgres redis

# 向量数据库（任务2需要）
docker-compose --profile full up -d milvus etcd minio

# 知识图谱（任务6需要）
docker-compose --profile full up -d neo4j

# 代理池（爬虫推荐）
docker-compose up -d proxy_pool

# 检查所有服务状态
docker-compose ps
```

---

## 📊 预期成果

执行完成后：
- **PostgreSQL**: 30,000,000+ 条记录（含合成数据+开源数据集）
- **Milvus**: 10,000,000+ 向量（法条+案例+QA对）
- **裁判文书**: 1,000,000+ 条（持续爬取中）
- **法律法规**: 500,000+ 条（全量）
- **知识图谱**: 100,000+ 实体和关系

**商业化就绪度**: 从82%提升到90%+

---

## ⚠️ 风险与注意事项

1. **磁盘空间**: 确保至少100GB可用空间
2. **内存需求**: Milvus向量化需要16GB+内存
3. **网络带宽**: HuggingFace数据集下载需要稳定网络
4. **爬虫合规**: 遵守robots.txt，控制频率
5. **数据质量**: 合成数据质量参差不齐，需要后续清洗
6. **服务稳定性**: 长时间运行的任务需要监控和重试机制

---

## 📝 监控与验证

**导入进度监控**:
```bash
# PostgreSQL数据量
psql -U postgres -d legal_assistant -c "SELECT COUNT(*) FROM legal_articles;"

# Milvus数据量
curl http://localhost:19530/v1/vector/collections/legal_documents/statistics

# 爬虫进度
tail -f backend/crawler_data/crawler.log
```

**数据质量验证**:
- 随机抽样检查法条内容完整性
- 验证向量检索准确性
- 测试RAG回答质量

---

## 🎉 下一步

完成数据导入后，继续执行：
- **Phase 3**: 数据加密与安全加固
- **Phase 4**: 前端按钮全链路验证
- **Phase 5**: 商业化功能完善
