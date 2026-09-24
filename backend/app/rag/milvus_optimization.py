"""
Milvus 向量检索优化方案 - 支撑 1 亿+ 向量数据

当前架构：
- Milvus 2.4.8 (Standalone)
- BGE-M3 嵌入模型 (1024 维)
- 当前数据量：143 条向量

目标：
- 支撑 1 亿+ 向量数据
- 查询延迟 < 100ms
- 高并发查询 (> 1000 QPS)

优化策略：
"""

# =============================================================================
# 1. 分区策略 (Partition Strategy)
# =============================================================================

"""
按案件类型分区：
- 民事案件分区 (预计 6000 万+)
- 刑事案件分区 (预计 2000 万+)
- 行政案件分区 (预计 1000 万+)
- 其他案件分区 (预计 1000 万+)

优势：
- 查询时只扫描相关分区，减少 I/O
- 可以针对不同分区使用不同的索引参数
- 便于数据管理和维护
"""

PARTITION_STRATEGY = {
    "civil_cases": "case_type == '民事'",
    "criminal_cases": "case_type == '刑事'",
    "administrative_cases": "case_type == '行政'",
    "other_cases": "case_type not in ['民事', '刑事', '行政']",
}


# =============================================================================
# 2. 索引优化 (Index Optimization)
# =============================================================================

"""
推荐索引类型：HNSW (Hierarchical Navigable Small World)

参数配置：
- M: 16-32 (每个节点的连接数)
- efConstruction: 200-400 (构建时的搜索范围)
- ef: 64-128 (查询时的搜索范围)

性能预估 (1 亿向量)：
- 索引构建时间：约 2-4 小时
- 索引内存占用：约 40-60 GB
- 查询延迟：50-100ms
- 召回率：> 95%
"""

HNSW_INDEX_PARAMS = {
    "index_type": "HNSW",
    "metric_type": "COSINE",
    "params": {
        "M": 24,
        "efConstruction": 300,
    },
}

HNSW_SEARCH_PARAMS = {
    "metric_type": "COSINE",
    "params": {
        "ef": 96,
    },
}


# =============================================================================
# 3. 查询优化 (Query Optimization)
# =============================================================================

"""
优化策略：

1. 预过滤 (Pre-filtering)
   - 在向量搜索前先过滤数据
   - 使用标量索引加速过滤
   - 减少向量搜索的数据量

2. 批处理查询 (Batch Query)
   - 多个查询合并为一批
   - 减少网络往返次数
   - 提高吞吐量

3. 缓存热点查询 (Cache Hot Queries)
   - 使用 Redis 缓存常见查询结果
   - TTL: 1 小时
   - 缓存命中率预估：30-50%

4. 异步查询 (Async Query)
   - 使用 Milvus 的异步 API
   - 并发处理多个查询
   - 提高吞吐量
"""


# =============================================================================
# 4. 硬件配置建议 (Hardware Recommendations)
# =============================================================================

"""
生产环境配置 (支撑 1 亿向量)：

Milvus 节点：
- CPU: 32+ 核
- 内存: 128+ GB
- 存储: 1+ TB SSD (NVMe 推荐)
- 网络: 10 GbE

etcd 节点：
- CPU: 8+ 核
- 内存: 16+ GB
- 存储: 100+ GB SSD

MinIO 节点：
- 存储: 2+ TB SSD

部署模式：
- Milvus Cluster (分布式部署)
- 3 个 Query Node
- 2 个 Data Node
- 1 个 Index Node
"""


# =============================================================================
# 5. 监控和调优 (Monitoring & Tuning)
# =============================================================================

"""
关键监控指标：

1. 查询性能
   - 查询延迟 (P50, P95, P99)
   - 查询吞吐量 (QPS)
   - 召回率 (Recall@K)

2. 资源使用
   - CPU 使用率
   - 内存使用率
   - 磁盘 I/O
   - 网络带宽

3. 索引状态
   - 索引构建进度
   - 索引大小
   - 索引内存占用

调优策略：

1. 如果查询延迟高：
   - 增加 ef 参数 (提高召回率但增加延迟)
   - 减少 ef 参数 (降低延迟但降低召回率)
   - 增加 Query Node 数量

2. 如果内存不足：
   - 减少 M 参数
   - 使用 IVF_FLAT 索引 (内存占用更小)
   - 增加数据分区

3. 如果构建索引慢：
   - 增加 Index Node 数量
   - 减少 efConstruction 参数
   - 分批构建索引
"""


# =============================================================================
# 6. 实施路线图 (Implementation Roadmap)
# =============================================================================

"""
阶段 1: 当前优化 (1000 条 - 10 万条)
- [x] 使用 IVF_FLAT 索引
- [x] 基础分区策略
- [ ] 添加 Redis 缓存
- [ ] 监控和告警

阶段 2: 中期优化 (10 万条 - 1000 万条)
- [ ] 迁移到 HNSW 索引
- [ ] 实现预过滤
- [ ] 批处理查询
- [ ] 性能调优

阶段 3: 大规模优化 (1000 万条 - 1 亿条)
- [ ] 迁移到 Milvus Cluster
- [ ] 增加 Query Node
- [ ] 优化硬件配置
- [ ] 压力测试

阶段 4: 超大规模优化 (1 亿条+)
- [ ] 水平扩展
- [ ] 数据分片
- [ ] 多机房部署
- [ ] 灾备方案
"""


# =============================================================================
# 7. 成本估算 (Cost Estimation)
# =============================================================================

"""
1 亿向量数据的成本估算：

硬件成本 (自建)：
- Milvus 集群 (3 节点): ￥15,000/月
- etcd 集群 (3 节点): ￥3,000/月
- MinIO 存储: ￥2,000/月
- 总计: ￥20,000/月

云服务成本 (阿里云/腾讯云)：
- Milvus 托管服务: ￥25,000-35,000/月
- 包含运维和监控

人力成本：
- 运维工程师: 0.5 人
- 数据工程师: 0.5 人
- 总计: ￥30,000/月

总成本: ￥50,000-65,000/月
"""


# =============================================================================
# 8. 实施代码示例
# =============================================================================

async def optimize_milvus_for_scale():
    """优化 Milvus 以支撑大规模数据"""
    from pymilvus import connections, Collection, utility

    # 连接 Milvus
    connections.connect(host="localhost", port="19530")

    # 获取集合
    collection = Collection("legal_articles")

    # 1. 创建分区
    partitions = ["civil_cases", "criminal_cases", "administrative_cases", "other_cases"]
    for partition in partitions:
        if not utility.has_partition("legal_articles", partition):
            collection.create_partition(partition)
            print(f"创建分区: {partition}")

    # 2. 删除旧索引
    try:
        collection.drop_index()
        print("删除旧索引")
    except Exception as e:
        print(f"删除索引失败（可能不存在）: {e}")

    # 3. 创建 HNSW 索引
    collection.create_index(
        field_name="embedding",
        index_params=HNSW_INDEX_PARAMS,
    )
    print("创建 HNSW 索引")

    # 4. 加载集合
    collection.load()
    print("加载集合到内存")

    # 5. 测试查询性能
    import time
    import numpy as np

    # 生成随机查询向量
    query_embedding = np.random.random(1024).tolist()

    # 测试查询
    start = time.time()
    results = collection.search(
        data=[query_embedding],
        anns_field="embedding",
        param=HNSW_SEARCH_PARAMS,
        limit=10,
        output_fields=["law_name", "article_number"],
    )
    elapsed = time.time() - start

    print(f"查询延迟: {elapsed*1000:.2f}ms")
    print(f"召回结果数: {len(results[0])}")

    connections.disconnect("default")


if __name__ == "__main__":
    import asyncio
    asyncio.run(optimize_milvus_for_scale())
