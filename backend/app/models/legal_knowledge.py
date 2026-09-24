"""Legal knowledge database models.

Stores laws, articles, court cases, judicial interpretations,
and legal concepts for the RAG knowledge base.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import String, Text, Integer, Float, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import UUIDMixin, TimestampMixin


class Law(Base, UUIDMixin, TimestampMixin):
    """A law or regulation (e.g., 中华人民共和国民法典)."""

    __tablename__ = "laws"

    name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    short_name: Mapped[str] = mapped_column(String(256), nullable=True)
    law_type: Mapped[str] = mapped_column(
        String(64), nullable=False,
        doc="宪法/民法/刑法/行政法/经济法/社会法/诉讼法/商法",
    )
    category: Mapped[str] = mapped_column(String(128), nullable=True)
    effective_date: Mapped[str] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active",
        doc="active/amended/repealed",
    )
    issuing_authority: Mapped[str] = mapped_column(String(256), nullable=True)
    abstract: Mapped[str] = mapped_column(Text, nullable=True)

    articles: Mapped[list["LegalArticle"]] = relationship(
        "LegalArticle", back_populates="law", lazy="noload",
    )

    def __repr__(self) -> str:
        return f"<Law(name={self.name!r})>"


class LegalArticle(Base, UUIDMixin, TimestampMixin):
    """A specific article within a law."""

    __tablename__ = "legal_articles"
    __table_args__ = (
        Index("ix_articles_law_number", "law_id", "article_number"),
    )

    law_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("laws.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    article_number: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chapter: Mapped[str] = mapped_column(String(128), nullable=True)
    section: Mapped[str] = mapped_column(String(128), nullable=True)
    effective_status: Mapped[str] = mapped_column(String(32), default="active")
    tags: Mapped[str] = mapped_column(String(512), nullable=True)

    law: Mapped["Law"] = relationship("Law", back_populates="articles")

    def __repr__(self) -> str:
        return f"<LegalArticle(law={self.law_id}, num={self.article_number!r})>"


class CourtCase(Base, UUIDMixin, TimestampMixin):
    """A court case / judgment."""

    __tablename__ = "court_cases"
    __table_args__ = (
        Index("ix_cases_cause_type", "cause_of_action"),
        Index("ix_cases_court_date", "court_name", "decision_date"),
        # pg_trgm GIN 索引：加速案例检索的 LIKE '%关键词%' 子串匹配
        # （title/tags/cause_of_action 为窄字段，避免 summary 等巨型 Text 全表扫描）
        Index("ix_cases_title_trgm", "title", postgresql_using="gin",
              postgresql_ops={"title": "gin_trgm_ops"}),
        Index("ix_cases_tags_trgm", "tags", postgresql_using="gin",
              postgresql_ops={"tags": "gin_trgm_ops"}),
        Index("ix_cases_cause_trgm", "cause_of_action", postgresql_using="gin",
              postgresql_ops={"cause_of_action": "gin_trgm_ops"}),
    )

    case_number: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    court_name: Mapped[str] = mapped_column(String(256), nullable=True, index=True)
    case_type: Mapped[str] = mapped_column(
        String(64), nullable=True,
        doc="民事/刑事/行政/知识产权/劳动争议",
    )
    cause_of_action: Mapped[str] = mapped_column(String(256), nullable=True, index=True)
    decision_date: Mapped[str] = mapped_column(String(32), nullable=True)
    parties: Mapped[str] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    full_text: Mapped[str] = mapped_column(Text, nullable=True)
    key_points: Mapped[str] = mapped_column(Text, nullable=True)
    referenced_laws: Mapped[str] = mapped_column(Text, nullable=True)
    judgment_result: Mapped[str] = mapped_column(Text, nullable=True)
    tags: Mapped[str] = mapped_column(String(512), nullable=True)
    doc_count: Mapped[int] = mapped_column(Integer, default=0)

    def __repr__(self) -> str:
        return f"<CourtCase(number={self.case_number!r})>"


class JudicialInterpretation(Base, UUIDMixin, TimestampMixin):
    """A judicial interpretation (司法解释)."""

    __tablename__ = "judicial_interpretations"

    name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    doc_number: Mapped[str] = mapped_column(String(128), nullable=True)
    issuing_court: Mapped[str] = mapped_column(String(256), nullable=True)
    effective_date: Mapped[str] = mapped_column(String(32), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=True)
    related_law: Mapped[str] = mapped_column(String(512), nullable=True)
    tags: Mapped[str] = mapped_column(String(512), nullable=True)

    def __repr__(self) -> str:
        return f"<JudicialInterpretation(name={self.name!r})>"


class LegalQAPair(Base, UUIDMixin, TimestampMixin):
    """法律问答对 — 来自 laws.json 和 expanded_datasets 生成的 QA 数据。"""

    __tablename__ = "legal_qa_pairs"
    __table_args__ = (
        Index("ix_qa_pairs_law_name", "law_name"),
        Index("ix_qa_pairs_source", "source"),
    )

    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    law_name: Mapped[str] = mapped_column(String(512), nullable=True, index=True)
    article_number: Mapped[str] = mapped_column(String(64), nullable=True)
    law_type: Mapped[str] = mapped_column(String(64), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(128), nullable=True)

    def __repr__(self) -> str:
        return f"<LegalQAPair(law={self.law_name!r}, source={self.source!r})>"


class LegalKnowledgeEntry(Base, UUIDMixin, TimestampMixin):
    """法律知识条目 — 法律元信息、章节结构等。"""

    __tablename__ = "legal_knowledge_entries"
    __table_args__ = (
        Index("ix_knowledge_entries_type", "entry_type"),
        Index("ix_knowledge_entries_law_name", "law_name"),
        Index("ix_knowledge_entries_source", "source"),
    )

    entry_type: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    law_name: Mapped[str] = mapped_column(String(512), nullable=True, index=True)
    law_type: Mapped[str] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<LegalKnowledgeEntry(type={self.entry_type!r}, law={self.law_name!r})>"


class LegalCrossReference(Base, UUIDMixin, TimestampMixin):
    """法律交叉引用 — 法律之间的引用关系。"""

    __tablename__ = "legal_cross_references"
    __table_args__ = (
        Index("ix_cross_refs_law_name", "law_name"),
        Index("ix_cross_refs_source", "source"),
    )

    law_name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    referenced_laws: Mapped[str] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=True)
    ref_type: Mapped[str] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(128), nullable=True)

    def __repr__(self) -> str:
        return f"<LegalCrossReference(law={self.law_name!r})>"


class LegalConcept(Base, UUIDMixin, TimestampMixin):
    """A legal concept or principle."""

    __tablename__ = "legal_concepts"

    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=True)
    related_articles: Mapped[str] = mapped_column(Text, nullable=True)
    related_concepts: Mapped[str] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<LegalConcept(name={self.name!r})>"
