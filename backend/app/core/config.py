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
    DEEPSEEK_MODEL: str = "deepseek-chat"

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