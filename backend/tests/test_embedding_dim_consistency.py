"""嵌入维度一致性 —— 防止"模型换了、维度没跟着换"的静默失败。

背景（2026-09-24 排查）：
- `model_registry.py` 硬编码 BAAI/bge-m3 生成**主检索语料**向量（1024 维），
  实测 Milvus 的 legal_articles / legal_cases 两个集合均为 dim=1024。
- 但 `config.MILVUS_DIMENSION` 默认 **1536**（OpenAI ada-002 的维度），
  且 `backend/.env` 也覆盖为 1536。
- `MILVUS_DIMENSION` 被 `enterprise_knowledge_base` 用于**建集合**，而写入
  该集合的向量由 `milvus_service._get_embeddings_sync()` 产生（bge-m3，1024）。
  两者不一致时：建集合成功，之后**每一次插入都因维度不匹配而失败**。

这类 bug 不会在导入时报错，只在运行时炸，因此用测试锁住。
"""
import pytest

from app.core.config import settings


# bge-m3 的实际维度 —— MilvusRAGService / ModelRegistry 写死使用该模型。
BGE_M3_DIMENSION = 1024
# EmbeddingService 默认模型（shibing624/text2vec-base-chinese）的维度，
# 服务对象是 user_documents / legal_knowledge 两个集合。
TEXT2VEC_DIMENSION = 768


def test_milvus_dimension_matches_bge_m3():
    """MILVUS_DIMENSION 必须等于主检索语料实际使用的 bge-m3 维度。

    若此处失败，说明有人改了嵌入模型或改了配置却没同步 —— 建出来的集合
    将无法接受实际写入的向量。
    """
    assert settings.MILVUS_DIMENSION == BGE_M3_DIMENSION, (
        f"MILVUS_DIMENSION={settings.MILVUS_DIMENSION}，但主检索语料由 "
        f"BAAI/bge-m3 生成 {BGE_M3_DIMENSION} 维向量。两者必须一致，"
        f"否则新建集合的每一次插入都会失败。"
    )


def test_embedding_service_dimension_matches_its_default_model():
    """EmbeddingService 的预设维度须与其默认模型一致。

    `vector_store._get_dimension()` 在模型加载**之前**就会取这个值来建集合，
    因此预设值必须是默认模型的真实维度，否则建出的集合维度就是错的。
    """
    from app.rag.embedding_service import EmbeddingService

    svc = EmbeddingService()
    assert svc.model_name == EmbeddingService.DEFAULT_MODEL
    assert svc.dimension == TEXT2VEC_DIMENSION, (
        f"EmbeddingService 预设维度 {svc.dimension} 与其默认模型 "
        f"{svc.model_name} 的实际维度 {TEXT2VEC_DIMENSION} 不符。"
    )


def test_configured_embedding_model_is_not_the_legacy_minilm():
    """配置项不得再回退到 all-MiniLM-L6-v2。

    该模型是 384 维的英文模型，中文法律场景不适用，且与任何实际集合的维度
    都不匹配。历史配置曾用它，导致"照 .env 判断维度"会得出错误结论。
    """
    assert "MiniLM" not in settings.EMBEDDING_MODEL, (
        f"EMBEDDING_MODEL 仍为 {settings.EMBEDDING_MODEL}（384 维英文模型，"
        f"中文法律场景不适用）。"
    )
