"""
Enterprise Knowledge Base — Private knowledge management with multi-tenant isolation.

Enables organizations to:
1. Create isolated knowledge namespaces
2. Upload documents that are automatically vectorized
3. Search within their private knowledge base
4. Manage knowledge base permissions
5. Track document processing status

Architecture:
    Tenant (organization)
      └── KnowledgeNamespace (isolated collection)
            └── KnowledgeDocument (uploaded + vectorized)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import text

from app.core.config import settings

logger = logging.getLogger(__name__)


class EnterpriseKnowledgeBase:
    """Enterprise-grade knowledge base with multi-tenant isolation.

    Each tenant gets an isolated namespace in the vector database,
    ensuring complete data separation between organizations.
    """

    # Supported document types for knowledge base ingestion
    SUPPORTED_TYPES = {".pdf", ".docx", ".txt", ".md", ".html", ".csv", ".xlsx"}

    async def create_namespace(
        self,
        namespace: str,
        description: str = "",
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Create a new knowledge base namespace for a tenant.

        Args:
            namespace: Namespace name (e.g., "company_contracts").
            description: Human-readable description.
            tenant_id: Organization/tenant identifier.

        Returns:
            Namespace metadata.
        """
        collection_name = f"kb_{tenant_id}_{namespace}"

        try:
            from app.rag.milvus_service import get_milvus_service
            milvus = get_milvus_service()

            # Create collection in Milvus
            milvus.create_collection(
                collection_name=collection_name,
                dimension=settings.MILVUS_DIMENSION,
            )

            # Record namespace metadata in PostgreSQL
            from app.core.database import async_session_factory
            async with async_session_factory() as session:
                await session.execute(
                    text("""
                        INSERT INTO knowledge_namespaces (id, tenant_id, namespace, description, milvus_collection, created_at)
                        VALUES (:id, :tenant_id, :namespace, :description, :collection, NOW())
                        ON CONFLICT (tenant_id, namespace) DO UPDATE SET
                            description = :description,
                            updated_at = NOW()
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "tenant_id": tenant_id,
                        "namespace": namespace,
                        "description": description,
                        "collection": collection_name,
                    },
                )
                await session.commit()

            return {
                "success": True,
                "namespace": namespace,
                "tenant_id": tenant_id,
                "collection": collection_name,
                "description": description,
            }

        except Exception as exc:
            logger.error("Failed to create namespace: %s", exc)
            return {"success": False, "error": str(exc)}

    async def upload_document(
        self,
        namespace: str,
        filename: str,
        content: bytes,
        tenant_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Upload a document to the knowledge base.

        The document is parsed, chunked, embedded, and stored
        in the tenant's isolated vector collection.
        """
        collection_name = f"kb_{tenant_id}_{namespace}"

        # Validate file type
        ext = os.path.splitext(filename)[1].lower()
        if ext not in self.SUPPORTED_TYPES:
            return {"success": False, "error": f"Unsupported file type: {ext}"}

        # Parse document to text
        try:
            from app.rag.document_processor import DocumentProcessor
            processor = DocumentProcessor()

            # Write to temp file for processing
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            try:
                parsed = await processor.parse_file(tmp_path)
                doc_text = parsed.get("text", "")
            finally:
                os.unlink(tmp_path)

        except Exception as exc:
            return {"success": False, "error": f"Document parsing failed: {exc}"}

        if not doc_text.strip():
            return {"success": False, "error": "No text extracted from document"}

        # Chunk the document
        chunks = self._chunk_text(doc_text, chunk_size=500, overlap=50)

        # Generate embeddings
        try:
            from app.rag.embedding_service import get_embedding_service
            embedding_service = get_embedding_service()
            embeddings = await embedding_service.embed_batch(chunks)
        except Exception as exc:
            return {"success": False, "error": f"Embedding generation failed: {exc}"}

        # Store in Milvus
        try:
            from app.rag.milvus_service import get_milvus_service
            milvus = get_milvus_service()

            doc_id = str(uuid.uuid4())
            ids = [f"{doc_id}_{i}" for i in range(len(chunks))]

            milvus.insert(
                collection_name=collection_name,
                ids=ids,
                texts=chunks,
                embeddings=embeddings,
                metadatas=[
                    {
                        "doc_id": doc_id,
                        "filename": filename,
                        "chunk_index": i,
                        "tenant_id": tenant_id,
                        "namespace": namespace,
                        "uploaded_at": datetime.now().isoformat(),
                        **(metadata or {}),
                    }
                    for i in range(len(chunks))
                ],
            )

            return {
                "success": True,
                "doc_id": doc_id,
                "filename": filename,
                "chunks": len(chunks),
                "text_length": len(doc_text),
                "namespace": namespace,
            }

        except Exception as exc:
            return {"success": False, "error": f"Vector storage failed: {exc}"}

    async def search(
        self,
        namespace: str,
        query: str,
        tenant_id: str = "default",
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Search within a tenant's knowledge base namespace."""
        collection_name = f"kb_{tenant_id}_{namespace}"

        try:
            from app.rag.milvus_service import get_milvus_service
            milvus = get_milvus_service()

            results = milvus.search(
                collection_name=collection_name,
                query_text=query,
                top_k=top_k,
            )

            return {
                "success": True,
                "results": results,
                "total": len(results),
                "namespace": namespace,
                "query": query,
            }

        except Exception as exc:
            return {"success": False, "error": str(exc), "results": []}

    async def list_documents(
        self,
        namespace: str,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """List documents in a namespace."""
        try:
            from app.core.database import async_session_factory
            async with async_session_factory() as session:
                result = await session.execute(
                    text("""
                        SELECT id, tenant_id, namespace, description, milvus_collection, created_at, updated_at
                        FROM knowledge_namespaces
                        WHERE tenant_id = :tenant_id AND namespace = :namespace
                    """),
                    {"tenant_id": tenant_id, "namespace": namespace},
                )
                rows = result.fetchall()

            return {
                "success": True,
                "namespace": namespace,
                "tenant_id": tenant_id,
                "documents": [
                    {
                        "id": str(r[0]),
                        "namespace": r[2],
                        "description": r[3],
                        "collection": r[4],
                    }
                    for r in rows
                ],
            }

        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def delete_document(
        self,
        namespace: str,
        doc_id: str,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Delete a document from the knowledge base."""
        collection_name = f"kb_{tenant_id}_{namespace}"

        try:
            from app.rag.milvus_service import get_milvus_service
            milvus = get_milvus_service()

            # Delete by doc_id prefix
            milvus.delete_by_filter(
                collection_name=collection_name,
                filter_expr=f'doc_id == "{doc_id}"',
            )

            return {
                "success": True,
                "doc_id": doc_id,
                "namespace": namespace,
            }

        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _chunk_text(
        self,
        text: str,
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> list[str]:
        """Split text into overlapping chunks for embedding."""
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            if chunk.strip():
                chunks.append(chunk.strip())
            start = end - overlap

        return chunks


# =============================================================================
# Singleton
# =============================================================================

_kb: EnterpriseKnowledgeBase | None = None


def get_enterprise_knowledge_base() -> EnterpriseKnowledgeBase:
    global _kb
    if _kb is None:
        _kb = EnterpriseKnowledgeBase()
    return _kb
