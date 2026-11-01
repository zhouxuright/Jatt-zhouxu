"""Application configuration loaded from environment variables and .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with sensible defaults for development."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent.parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    APP_NAME: str = "Legal Intelligent Assistance System"
    APP_VERSION: str = "0.1.0"
    APP_ENV: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-to-a-secure-random-string"

    # ------------------------------------------------------------------
    # Server
    # ------------------------------------------------------------------
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # ------------------------------------------------------------------
    # PostgreSQL
    # ------------------------------------------------------------------
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/legal_assistant"
    )
    DATABASE_POOL_SIZE: int = 50
    DATABASE_MAX_OVERFLOW: int = 100
    DATABASE_ECHO: bool = False

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_MAX_CONNECTIONS: int = 10

    # ------------------------------------------------------------------
    # Milvus (Vector Database)
    # ------------------------------------------------------------------
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530
    MILVUS_COLLECTION_NAME: str = "legal_documents"
    # 向量维度必须与实际嵌入模型一致。此前默认 1536（OpenAI ada-002 的维度），
    # 但实际嵌入模型是 BAAI/bge-m3（1024 维，见 model_registry.py），
    # 用它建集合后插入 1024 维向量会直接失败（实测 legal_articles / legal_cases
    # 两个集合均为 dim=1024）。
    MILVUS_DIMENSION: int = 1024

    # ------------------------------------------------------------------
    # ChromaDB (fallback / local vector store)
    # ------------------------------------------------------------------
    CHROMA_PERSIST_DIRECTORY: str = "./chroma_data"

    # ------------------------------------------------------------------
    # LLM: DeepSeek
    # ------------------------------------------------------------------
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_API_BASE: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-flash"

    # ------------------------------------------------------------------
    # LLM: OpenAI
    # ------------------------------------------------------------------
    OPENAI_API_KEY: str = ""
    OPENAI_API_BASE: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o"

    # ------------------------------------------------------------------
    # LLM: Default provider
    # ------------------------------------------------------------------
    LLM_PROVIDER: Literal["deepseek", "openai"] = "deepseek"

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------
    # ⚠️ 注意：本项**不控制**主检索语料（legal_articles / legal_cases）的嵌入模型。
    # 那些集合的向量由 `services/model_registry.py` 中的 ModelRegistry 生成，
    # 模型硬编码为 BAAI/bge-m3（1024 维），不读本配置。
    #
    # 本项仅供 EmbeddingService（`rag/embedding_service.py`）使用，它服务于
    # 用户上传文档路径（user_documents / legal_knowledge 两个集合，实测均为
    # 768 维）。此处默认值原先误写为 all-MiniLM-L6-v2（384 维），与实际写入
    # 的 768 维不符，已更正为 EmbeddingService.DEFAULT_MODEL 的真实取值。
    EMBEDDING_MODEL: str = "shibing624/text2vec-base-chinese"
    EMBEDDING_DEVICE: str = "cpu"

    # ------------------------------------------------------------------
    # JWT
    # ------------------------------------------------------------------
    JWT_SECRET_KEY: str = "change-me-jwt-secret-at-least-32-chars"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ------------------------------------------------------------------
    # Neo4j (Knowledge Graph)
    # ------------------------------------------------------------------
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "password"

    # ------------------------------------------------------------------
    # Elasticsearch
    # ------------------------------------------------------------------
    ELASTICSEARCH_HOST: str = "http://localhost:9200"
    ELASTICSEARCH_INDEX_PREFIX: str = "legal_"

    # ------------------------------------------------------------------
    # OCR (scanned documents)
    # ------------------------------------------------------------------
    OCR_ENABLED: bool = True
    OCR_ENGINE: str = "auto"  # auto / paddle / tesseract

    # ------------------------------------------------------------------
    # Voice input (speech-to-text)
    # ------------------------------------------------------------------
    VOICE_ENABLED: bool = True
    VOICE_MODEL_SIZE: str = "base"  # tiny / base / small / medium / large
    MAX_AUDIO_FILE_SIZE_MB: int = 10
    WHISPER_MODEL_DIR: str = "/app/models/whisper"  # writable cache for downloaded Whisper weights

    # ------------------------------------------------------------------
    # File Upload
    # ------------------------------------------------------------------
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 50

    # 合同审查可接受的**正文字符数**上限（文档级，非聊天级）。
    # 聊天输入沿用 ContentSafetyFilter.MAX_INPUT_LENGTH（10,000）；合同/法条全文
    # 远超此值（一部法规动辄上万字），必须单独放宽，否则会被"内容安全检查"误拒。
    MAX_CONTRACT_REVIEW_CHARS: int = 50000

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    # Maximum API request body size in MB (excludes file-upload endpoints).
    MAX_REQUEST_BODY_SIZE: int = 5

    # MIME types accepted for file uploads.
    ALLOWED_UPLOAD_MIME_TYPES: list[str] = [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "application/msword",
        "text/markdown",
        "text/x-markdown",
    ]

    # File extensions that are always rejected, regardless of MIME type.
    BLOCKED_FILE_EXTENSIONS: list[str] = [
        ".exe", ".bat", ".cmd", ".com", ".sh", ".ps1", ".psm1",
        ".msi", ".dll", ".sys", ".vbs", ".js", ".ws", ".wsf",
        ".scr", ".pif", ".hta", ".cpl",
    ]

    # ------------------------------------------------------------------
    # Rate Limiting
    # ------------------------------------------------------------------
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = 60

    # ------------------------------------------------------------------
    # RAG retrieval tuning
    # ------------------------------------------------------------------
    # 原先这些参数硬编码在 advanced_retriever.py 里（top_k*3 / RRF k=60 /
    # 图谱加权 1.2），导致"调优"只能改代码。外置后可配合 scripts/rag_eval.py
    # 做 A/B 回归，改配置即生效。
    RAG_CANDIDATE_MULTIPLIER: int = 3
    RAG_RRF_K: int = 60
    RAG_GRAPH_BOOST: float = 1.2
    RAG_DEFAULT_TOP_K: int = 5
    # CPU 环境下 cross-encoder 重排是主要延迟来源，可临时关闭做对比实验。
    RAG_RERANK_ENABLED: bool = True
    # 送进 cross-encoder 的候选上限。融合后候选通常有 20~40 条，
    # 全量送重排会让 CPU 上的重排耗时线性放大；截断到头部 N 条
    # 既有足够区分度，又把重排成本压到常数级。
    RAG_RERANK_MAX_CANDIDATES: int = 8
    # 单进程内同时执行的检索条数上限。检索会触发 BGE-M3 编码 + BM25 + Milvus
    # 调用，多个原生调用重叠时会直接段错误（Segmentation fault）而不是 OOM。
    # 默认 1 = 严格串行，全部检索在专用单线程上执行，这是唯一稳定方案。
    RAG_MAX_CONCURRENT_RETRIEVALS: int = 1

    # ------------------------------------------------------------------
    # Semantic cache (LLM 语义缓存)
    # ------------------------------------------------------------------
    SEMANTIC_CACHE_ENABLED: bool = True
    SEMANTIC_CACHE_SIMILARITY_THRESHOLD: float = 0.92
    SEMANTIC_CACHE_TTL_SECONDS: int = 3600

    # ------------------------------------------------------------------
    # LLM token cost governance / latency budget
    # ------------------------------------------------------------------
    # 输入预算（估算 token）。超预算时按"保留系统提示 + 最近 N 轮"裁剪，
    # 避免把整段合同/长历史全部喂给模型（这是 token 成本的最大来源）。
    LLM_MAX_INPUT_TOKENS: int = 6000
    # 裁剪时始终保留的最近消息条数（不含 system）。
    LLM_CONTEXT_KEEP_RECENT: int = 8
    # 单次请求的延迟预算（秒），作为 httpx 单请求超时；外部 provider 抖动
    # 时快速失败并切下一个 provider，而不是把请求拖到 120s。
    LLM_LATENCY_BUDGET_SECONDS: float = 90.0

    # 精确匹配缓存：同样的 messages+参数在 TTL 内直接复用，命中即 0 token。
    # 只对非流式调用生效，且不缓存超长上下文（命中率低、key 成本高）。
    LLM_EXACT_CACHE_ENABLED: bool = True
    LLM_EXACT_CACHE_TTL_SECONDS: int = 300
    LLM_EXACT_CACHE_MAX_INPUT_TOKENS: int = 2000

    # 任务分档：将低价值/格式化任务路由到更便宜的模型。
    # 置空表示不覆盖，继续使用 provider 默认模型。
    LLM_FAST_MODEL: str = ""
    LLM_DEEP_MODEL: str = ""

    # 流式调用请求 usage 统计（DeepSeek/OpenAI 兼容接口支持），
    # 让流式路径也能进成本账本。
    LLM_STREAM_INCLUDE_USAGE: bool = True

    # 成本账本：按天写入 Redis（进程重启不清零）。
    LLM_COST_LEDGER_ENABLED: bool = True

    # ------------------------------------------------------------------
    # Retrieval-side caches (降低重复编码/重复检索的延迟)
    # ------------------------------------------------------------------
    # 单条 query embedding 的进程内 LRU 大小。BGE-M3 单条 CPU 编码是
    # 1-3s 级开销，同 query 复用时直接命中可省掉这段延迟。
    RAG_QUERY_EMBEDDING_CACHE_SIZE: int = 1024
    # BM25 查询结果 LRU 大小。
    RAG_BM25_CACHE_SIZE: int = 128

    # ------------------------------------------------------------------
    # Claim-level grounding（主张级引用绑定）
    # ------------------------------------------------------------------
    # 对含法条引用/检索片段的主张句做证据支撑校验；关闭后只保留
    # 法条号存在性校验（citation_verifier）。
    GROUNDING_ENABLED: bool = True
    # 单次校验的最大主张数（控制数据库查询与 CPU 开销）。
    GROUNDING_MAX_CLAIMS: int = 10
    # 主张二元组被证据覆盖比例 >= 该值判定为"有支撑"。
    GROUNDING_SUPPORT_THRESHOLD: float = 0.30
    # 介于 weak 与 support 之间判定为"弱支撑"。
    GROUNDING_WEAK_THRESHOLD: float = 0.12
    # 低于该长度的句子不视为需要证据的主张。
    GROUNDING_MIN_CLAIM_CHARS: int = 12

    # ------------------------------------------------------------------
    # Performance observability & generic response cache
    # ------------------------------------------------------------------
    # 单请求耗时超过该值记为 SLOW_REQUEST（用于定位 P95 尾巴）
    SLOW_REQUEST_THRESHOLD_MS: int = 1000
    RESPONSE_CACHE_ENABLED: bool = True
    # 通用 GET 响应缓存 TTL（秒）。写操作会通过代数(gen)整体失效。
    RESPONSE_CACHE_TTL_SECONDS: int = 60
    # 单条响应超过该字节数不缓存（避免大列表把 Redis 打爆）
    RESPONSE_CACHE_MAX_BODY_BYTES: int = 256 * 1024
    # 不缓存的前缀：流式、鉴权、强实时、导出类接口
    RESPONSE_CACHE_EXCLUDE_PREFIXES: list[str] = [
        "/api/v1/auth",
        "/api/v1/chat",
        "/api/v1/search",
        "/api/v1/tasks",
        "/api/v1/deep-think",
        "/api/v1/deep_think",
        "/api/v1/admin/audit-logs",
        "/api/v1/knowledge",
        "/api/v1/collaboration",
    ]

    # ------------------------------------------------------------------
    # Mass Import
    # ------------------------------------------------------------------
    IMPORT_CHECKPOINT_DIR: str = "data/import_checkpoints"
    IMPORT_BATCH_SIZE_PG: int = 200
    IMPORT_BATCH_SIZE_MILVUS: int = 500
    IMPORT_EMBED_BATCH: int = 64

    # ------------------------------------------------------------------
    # Data Privacy & Encryption
    # ------------------------------------------------------------------
    ENCRYPTION_KEY: str = ""
    DATA_RETENTION_DAYS: int = 365
    ACCOUNT_DELETION_GRACE_PERIOD_DAYS: int = 30

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # ------------------------------------------------------------------
    # Proxy Pool (IP代理池)
    # ------------------------------------------------------------------
    PROXY_POOL_ENABLED: bool = False
    PROXY_POOL_URL: str = "http://proxy_pool:5010"

    # ------------------------------------------------------------------
    # Regulation Monitor (P1.5 合规风险动态跟踪)
    # ------------------------------------------------------------------
    REGULATION_MONITOR_ENABLED: bool = True
    REGULATION_MONITOR_INTERVAL_HOURS: float = 24.0
    REGULATION_MONITOR_DAYS_BACK: int = 7

    # ------------------------------------------------------------------
    # HuggingFace
    # ------------------------------------------------------------------
    HF_TOKEN: str = ""

    # ------------------------------------------------------------------
    # Data Expansion
    # ------------------------------------------------------------------
    DATA_EXPANSION_ENABLED: bool = True

    # ------------------------------------------------------------------
    # MCP Tool Calling
    # ------------------------------------------------------------------
    MCP_TOOLS_ENABLED: bool = True
    ENTERPRISE_API_BASE: str = ""
    ENTERPRISE_API_KEY: str = ""

    # ------------------------------------------------------------------
    # Web Search
    # ------------------------------------------------------------------
    SEARCH_ENGINE: str = "bing"  # bing / serper / baidu / sogou
    BING_SEARCH_API_KEY: str = ""
    SERPER_API_KEY: str = ""
    BAIDU_SEARCH_API_KEY: str = ""

    # ------------------------------------------------------------------
    # Deep Thinking / Reasoning
    # ------------------------------------------------------------------
    DEEP_THINKING_ENABLED: bool = True
    REASONING_MODEL: str = "deepseek-reasoner"

    # ------------------------------------------------------------------
    # Skills System
    # ------------------------------------------------------------------
    SKILLS_ENABLED: bool = True

    # ------------------------------------------------------------------
    # Voice Output (TTS)
    # ------------------------------------------------------------------
    TTS_ENABLED: bool = True
    TTS_ENGINE: str = "edge"  # edge / azure / iflytek
    TTS_VOICE: str = "zh-CN-XiaoxiaoNeural"

    # ------------------------------------------------------------------
    # Qwen / GLM LLM providers
    # ------------------------------------------------------------------
    QWEN_API_KEY: str = ""
    QWEN_API_BASE: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_MODEL: str = "qwen-max"
    GLM_API_KEY: str = ""
    GLM_API_BASE: str = "https://open.bigmodel.cn/api/paas/v4"
    GLM_MODEL: str = "glm-4"


@lru_cache()
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()


settings = get_settings()