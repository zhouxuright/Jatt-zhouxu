"""Pydantic schemas for knowledge graph API responses."""

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    """A single node in the knowledge graph."""
    id: str = Field(..., description="Unique node identifier (e.g., 'law:uuid')")
    type: str = Field(..., description="Node type: law, article, case, concept")
    label: str = Field(..., description="Display label for the node")
    metadata: dict = Field(default_factory=dict, description="Additional node metadata")


class GraphEdge(BaseModel):
    """A directed edge between two graph nodes."""
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    relation: str = Field(..., description="Relationship type: CONTAINS, REFERENCES, CITES, etc.")


class GraphData(BaseModel):
    """Full graph data for visualization."""
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class RelatedArticle(BaseModel):
    """A related article found through graph traversal."""
    article_id: str = Field(default="", description="Article database ID")
    law_name: str = Field(default="", description="Full name of the law")
    article_number: str = Field(default="", description="Article number")
    content: str = Field(default="", description="Article content preview")
    relation: str = Field(default="", description="Relationship type connecting to the source")
    path: list[str] = Field(default_factory=list, description="Traversal path of relations")


class RelatedArticlesResponse(BaseModel):
    """Response for related articles query."""
    article_id: str = Field(default="", description="The queried article ID")
    primary_articles: list[dict] = Field(default_factory=list, description="The source article(s)")
    related_articles: list[RelatedArticle] = Field(default_factory=list, description="Related articles found via graph")
    total_related: int = Field(default=0, description="Total related articles found")


class LawFamilyMember(BaseModel):
    """A law in the family tree."""
    law_name: str = Field(default="", description="Name of the law")
    law_type: str = Field(default="", description="Law category type")
    effective_date: str = Field(default="", description="Effective date")
    status: str = Field(default="", description="Status: active/amended/repealed")
    relationship: str = Field(default="", description="Relationship to the queried law")


class LawFamilyResponse(BaseModel):
    """Response for law family query."""
    law_name: str = Field(default="", description="The queried law name")
    amendments: list[LawFamilyMember] = Field(default_factory=list, description="Amendments of this law")
    parent_laws: list[LawFamilyMember] = Field(default_factory=list, description="Parent/base laws")
    child_regulations: list[LawFamilyMember] = Field(default_factory=list, description="Child regulations")
    all_related: list[LawFamilyMember] = Field(default_factory=list, description="All related laws")


class CitationChainResponse(BaseModel):
    """Response for citation chain query."""
    from_law: str = Field(default="", description="Starting law")
    to_law: str = Field(default="", description="Target law")
    chain: list[str] = Field(default_factory=list, description="Ordered chain of law/article names")
    chain_length: int = Field(default=0, description="Number of steps in the chain")
    path_found: bool = Field(default=False, description="Whether a path was found")


class GraphStatsResponse(BaseModel):
    """Statistics about the knowledge graph."""
    node_count: int = Field(default=0, description="Total number of nodes")
    edge_count: int = Field(default=0, description="Total number of edges")
    law_count: int = Field(default=0, description="Number of law nodes")
    article_count: int = Field(default=0, description="Number of article nodes")
    case_count: int = Field(default=0, description="Number of case nodes")
    concept_count: int = Field(default=0, description="Number of concept nodes")
    nodes_by_type: dict[str, int] = Field(default_factory=dict, description="Node counts by type")
    edges_by_type: dict[str, int] = Field(default_factory=dict, description="Edge counts by relation type")


class BuildGraphResponse(BaseModel):
    """Response after building/rebuilding the knowledge graph."""
    status: str = Field(default="success", description="Build status")
    nodes: int = Field(default=0, description="Total nodes created")
    edges: int = Field(default=0, description="Total edges created")
    laws: int = Field(default=0, description="Law nodes created")
    articles: int = Field(default=0, description="Article nodes created")
    cases: int = Field(default=0, description="Case nodes created")
    concepts: int = Field(default=0, description="Concept nodes created")
    cross_references: int = Field(default=0, description="Cross-reference edges detected")
    amendments: int = Field(default=0, description="Amendment edges detected")
