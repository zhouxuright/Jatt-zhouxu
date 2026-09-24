"""法律数据全量导入模块。

将国家法律法规数据库的全部法律法规（~22,000 部、~1,000,000+ 条文）
解析、去重、导入 PostgreSQL 和 Milvus 向量库。

使用方法：
    # 全量导入（所有阶段）
    python -m app.rag.mass_import

    # 仅解析
    python -m app.rag.mass_import --phase parse

    # 仅 PostgreSQL 导入
    python -m app.rag.mass_import --phase pg_import

    # 仅 Milvus 导入
    python -m app.rag.mass_import --phase milvus

    # 跳过 Milvus
    python -m app.rag.mass_import --skip-milvus

    # 从断点恢复
    python -m app.rag.mass_import --resume

    # 干跑模式（仅统计）
    python -m app.rag.mass_import --dry-run

    # 保留所有版本（不只是最新有效版本）
    python -m app.rag.mass_import --all-versions

    # GPU 加速
    python -m app.rag.mass_import --device cuda
"""

from app.rag.mass_import.cli import main

__all__ = ["main"]
