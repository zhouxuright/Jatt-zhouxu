"""RAG (Retrieval-Augmented Generation) module for the Legal Intelligent Assistance System.

This module provides:
- Vector store management (Milvus/ChromaDB)
- Document processing and chunking
- Embedding generation and caching
- Knowledge graph construction and querying
- Hybrid retrieval with result fusion
"""

from app.rag.vector_store import VectorStoreManager
from app.rag.document_processor import DocumentProcessor
from app.rag.embedding_service import EmbeddingService
from app.rag.knowledge_graph import KnowledgeGraphManager
from app.rag.retriever import HybridRetriever

__all__ = [
    "VectorStoreManager",
    "DocumentProcessor",
    "EmbeddingService",
    "KnowledgeGraphManager",
    "HybridRetriever",
]