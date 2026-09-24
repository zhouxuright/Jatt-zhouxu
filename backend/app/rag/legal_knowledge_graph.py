"""Enhanced legal knowledge graph builder -- extracts relationships from PostgreSQL.

Builds an in-memory graph connecting laws, articles, cases, and legal concepts
through cross-references, citations, keyword matching, and amendment detection.
"""

import logging
import re
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class LegalKnowledgeGraphBuilder:
    """Build and query a knowledge graph from legal data in PostgreSQL."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        # In-memory graph: node_id -> {type, label, metadata, edges: [(target_id, relation)]}
        self._graph: dict[str, dict[str, Any]] = {}
        # Index: law_name -> node_id (for laws)
        self._law_index: dict[str, str] = {}
        # Index: (law_name, article_number) -> node_id
        self._article_index: dict[tuple[str, str], str] = {}

    async def build_from_database(self) -> dict[str, int]:
        """Extract entities and relationships from PostgreSQL.

        Entities:
        - Laws (node type: 'law')
        - Legal Articles (node type: 'article')
        - Court Cases (node type: 'case')
        - Legal Concepts (node type: 'concept')

        Relationships:
        - law CONTAINS article
        - article REFERENCES article (cross-references found in content)
        - case CITES law / article
        - article RELATED_TO concept (keyword matching)
        - law AMENDS law (same name, different dates)

        Returns:
            Dict with counts of nodes and edges added.
        """
        self._graph.clear()
        self._law_index.clear()
        self._article_index.clear()

        async with self._session_factory() as session:
            # 1. Load laws
            law_count = await self._load_laws(session)

            # 2. Load articles and create CONTAINS edges
            article_count = await self._load_articles(session)

            # 3. Load court cases and create CITES edges
            case_count = await self._load_cases(session)

            # 4. Load legal concepts and create RELATED_TO edges
            concept_count = await self._load_concepts(session)

            # 5. Detect cross-references between articles
            cross_ref_count = self._find_cross_references()

            # 6. Detect amendment relationships between laws
            amendment_count = self._detect_amendments()

        stats = {
            "nodes": len(self._graph),
            "edges": sum(len(node["edges"]) for node in self._graph.values()),
            "laws": law_count,
            "articles": article_count,
            "cases": case_count,
            "concepts": concept_count,
            "cross_references": cross_ref_count,
            "amendments": amendment_count,
        }
        logger.info("Knowledge graph built: %s", stats)
        return stats

    async def _load_laws(self, session: AsyncSession) -> int:
        """Load laws as graph nodes."""
        from app.models.legal_knowledge import Law

        result = await session.execute(
            select(Law.id, Law.name, Law.law_type, Law.effective_date, Law.status, Law.category)
        )
        count = 0
        for row in result.all():
            law_id, name, law_type, eff_date, status, category = row
            node_id = f"law:{law_id}"
            self._graph[node_id] = {
                "type": "law",
                "label": name,
                "metadata": {
                    "id": law_id,
                    "law_type": law_type or "",
                    "effective_date": eff_date or "",
                    "status": status or "active",
                    "category": category or "",
                },
                "edges": [],
            }
            self._law_index[name] = node_id
            count += 1
        return count

    async def _load_articles(self, session: AsyncSession) -> int:
        """Load articles as graph nodes with CONTAINS edges from their parent law."""
        from app.models.legal_knowledge import Law, LegalArticle

        result = await session.execute(
            select(
                LegalArticle.id,
                LegalArticle.article_number,
                LegalArticle.title,
                LegalArticle.content,
                LegalArticle.chapter,
                LegalArticle.tags,
                LegalArticle.law_id,
                Law.name,
            ).join(Law, LegalArticle.law_id == Law.id)
        )
        count = 0
        for row in result.all():
            art_id, art_num, title, content, chapter, tags, law_id, law_name = row
            node_id = f"article:{art_id}"
            self._graph[node_id] = {
                "type": "article",
                "label": f"{law_name} {art_num}",
                "metadata": {
                    "id": art_id,
                    "article_number": art_num,
                    "title": title or "",
                    "content": content or "",
                    "chapter": chapter or "",
                    "tags": tags or "",
                    "law_id": law_id,
                    "law_name": law_name,
                },
                "edges": [],
            }
            self._article_index[(law_name, art_num)] = node_id

            # Law CONTAINS article
            law_node_id = self._law_index.get(law_name)
            if law_node_id:
                self._graph[law_node_id]["edges"].append((node_id, "CONTAINS"))

            count += 1
        return count

    async def _load_cases(self, session: AsyncSession) -> int:
        """Load court cases as graph nodes with CITES edges."""
        from app.models.legal_knowledge import CourtCase

        result = await session.execute(
            select(CourtCase.id, CourtCase.case_number, CourtCase.title,
                   CourtCase.case_type, CourtCase.referenced_laws, CourtCase.tags)
        )
        count = 0
        for row in result.all():
            case_id, case_number, title, case_type, ref_laws, tags = row
            node_id = f"case:{case_id}"
            self._graph[node_id] = {
                "type": "case",
                "label": title or case_number,
                "metadata": {
                    "id": case_id,
                    "case_number": case_number,
                    "case_type": case_type or "",
                    "referenced_laws": ref_laws or "",
                    "tags": tags or "",
                },
                "edges": [],
            }

            # Parse referenced_laws to create CITES edges
            if ref_laws:
                # ref_laws is typically comma-separated law names or 《XX法》 references
                law_names = re.findall(r'《([^》]+)》', ref_laws)
                if not law_names:
                    law_names = [n.strip() for n in ref_laws.split(',') if n.strip()]

                for law_name in law_names:
                    law_node_id = self._law_index.get(law_name)
                    if law_node_id:
                        self._graph[node_id]["edges"].append((law_node_id, "CITES"))

                    # Also try to find referenced articles: 《XX法》第X条
                    art_refs = re.findall(rf'《{re.escape(law_name)}》第([\d一二三四五六七八九十百千]+)条', ref_laws)
                    for art_num in art_refs:
                        art_key = (law_name, f"第{art_num}条")
                        art_node_id = self._article_index.get(art_key)
                        if art_node_id:
                            self._graph[node_id]["edges"].append((art_node_id, "CITES"))

            count += 1
        return count

    async def _load_concepts(self, session: AsyncSession) -> int:
        """Load legal concepts as graph nodes with RELATED_TO edges from articles."""
        from app.models.legal_knowledge import LegalConcept

        result = await session.execute(
            select(LegalConcept.id, LegalConcept.name, LegalConcept.definition,
                   LegalConcept.category, LegalConcept.related_articles)
        )
        count = 0
        concept_keywords: dict[str, str] = {}
        for row in result.all():
            concept_id, name, definition, category, related_arts = row
            node_id = f"concept:{concept_id}"
            self._graph[node_id] = {
                "type": "concept",
                "label": name,
                "metadata": {
                    "id": concept_id,
                    "definition": definition or "",
                    "category": category or "",
                    "related_articles": related_arts or "",
                },
                "edges": [],
            }
            concept_keywords[name] = node_id
            count += 1

        # Match articles to concepts via keyword overlap (article tags + content)
        for node_id, node in self._graph.items():
            if node["type"] != "article":
                continue
            tags = node["metadata"].get("tags", "")
            content = node["metadata"].get("content", "")[:500]  # first 500 chars
            combined = tags + " " + content
            for concept_name, concept_node_id in concept_keywords.items():
                if concept_name in combined:
                    self._graph[concept_node_id]["edges"].append((node_id, "RELATED_TO"))

        return count

    def _find_cross_references(self) -> int:
        """Find cross-references between articles by scanning content for patterns.

        Patterns:
        - 《XX法》第X条 (references to other laws)
        - 依照本法第X条 / 依照前条 / 依照第X条 (self-references within same law)
        - 参见第X条 (see also references)

        Returns:
            Number of cross-reference edges added.
        """
        count = 0
        # Chinese number to arabic mapping for matching
        cn_nums = {
            '一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
            '六': '6', '七': '7', '八': '8', '九': '9', '十': '10',
            '十一': '11', '十二': '12', '十三': '13', '十四': '14', '十五': '15',
            '十六': '16', '十七': '17', '十八': '18', '十九': '19', '二十': '20',
            '百': '100',
        }

        for node_id, node in list(self._graph.items()):
            if node["type"] != "article":
                continue
            content = node["metadata"].get("content", "")
            law_name = node["metadata"].get("law_name", "")

            # Pattern 1: 《XX法》第X条 -- references to other laws
            ext_refs = re.findall(r'《([^》]+)》第([\d一二三四五六七八九十百千]+)条', content)
            for ref_law, ref_num in ext_refs:
                ref_art_key = (ref_law, f"第{ref_num}条")
                target_id = self._article_index.get(ref_art_key)
                if target_id and target_id != node_id:
                    self._graph[node_id]["edges"].append((target_id, "REFERENCES"))
                    count += 1

            # Pattern 2: 依照本法第X条 / 依照第X条 -- self-references
            self_refs = re.findall(r'(?:依照|根据|按照)(?:本法|本法)?第([\d一二三四五六七八九十百千]+)条', content)
            for ref_num in self_refs:
                ref_art_key = (law_name, f"第{ref_num}条")
                target_id = self._article_index.get(ref_art_key)
                if target_id and target_id != node_id:
                    self._graph[node_id]["edges"].append((target_id, "REFERENCES"))
                    count += 1

            # Pattern 3: 参见第X条 -- see also
            see_refs = re.findall(r'参见第([\d一二三四五六七八九十百千]+)条', content)
            for ref_num in see_refs:
                ref_art_key = (law_name, f"第{ref_num}条")
                target_id = self._article_index.get(ref_art_key)
                if target_id and target_id != node_id:
                    self._graph[node_id]["edges"].append((target_id, "REFERENCES"))
                    count += 1

        return count

    def _detect_amendments(self) -> int:
        """Detect amendment relationships between laws (same base name, different dates).

        Returns:
            Number of AMENDS edges added.
        """
        count = 0
        # Group laws by base name (strip common amendment suffixes)
        law_groups: dict[str, list[str]] = defaultdict(list)
        for name, node_id in self._law_index.items():
            base = re.sub(r'（.*?修正案.*?）', '', name).strip()
            law_groups[base].append(node_id)

        for base_name, node_ids in law_groups.items():
            if len(node_ids) < 2:
                continue
            # Sort by effective_date; newer laws amend older ones
            dated = []
            for nid in node_ids:
                d = self._graph[nid]["metadata"].get("effective_date", "")
                dated.append((d, nid))
            dated.sort(key=lambda x: x[0])
            # Each subsequent law amends the previous
            for i in range(1, len(dated)):
                older_id = dated[i - 1][1]
                newer_id = dated[i][1]
                self._graph[newer_id]["edges"].append((older_id, "AMENDS"))
                count += 1

        return count

    def add_cross_references(self) -> int:
        """Public method to re-scan and add cross-references.

        Useful after adding new articles to the graph without a full rebuild.

        Returns:
            Number of cross-reference edges added.
        """
        return self._find_cross_references()

    def find_related_articles(self, article_id: str, depth: int = 2) -> list[dict]:
        """Find related articles through graph traversal.

        Starting from the given article, traverse REFERENCES and RELATED_TO
        edges up to `depth` levels deep.

        Args:
            article_id: The article database ID (UUID).
            depth: Maximum traversal depth.

        Returns:
            List of dicts with article info and relationship path.
        """
        node_id = f"article:{article_id}"
        if node_id not in self._graph:
            return []

        visited: set[str] = set()
        results: list[dict] = []
        frontier: list[tuple[str, list[str]]] = [(node_id, [])]

        for _ in range(depth):
            next_frontier: list[tuple[str, list[str]]] = []
            for current, path in frontier:
                if current in visited:
                    continue
                visited.add(current)

                node = self._graph.get(current)
                if not node:
                    continue

                for target, relation in node["edges"]:
                    if target in visited:
                        continue
                    if relation in ("REFERENCES", "RELATED_TO", "CONTAINS"):
                        target_node = self._graph.get(target)
                        if not target_node:
                            continue

                        if target_node["type"] == "article":
                            results.append({
                                "article_id": target_node["metadata"]["id"],
                                "law_name": target_node["metadata"].get("law_name", ""),
                                "article_number": target_node["metadata"].get("article_number", ""),
                                "content": target_node["metadata"].get("content", "")[:300],
                                "relation": relation,
                                "path": path + [relation],
                            })
                            next_frontier.append((target, path + [relation]))
                        elif target_node["type"] == "concept":
                            # Follow concept to its related articles
                            for sub_target, sub_rel in target_node["edges"]:
                                if sub_rel == "RELATED_TO" and sub_target not in visited:
                                    sub_node = self._graph.get(sub_target)
                                    if sub_node and sub_node["type"] == "article":
                                        results.append({
                                            "article_id": sub_node["metadata"]["id"],
                                            "law_name": sub_node["metadata"].get("law_name", ""),
                                            "article_number": sub_node["metadata"].get("article_number", ""),
                                            "content": sub_node["metadata"].get("content", "")[:300],
                                            "relation": f"{relation}->concept:{sub_node['metadata']['id']}->RELATED_TO",
                                            "path": path + [relation, "RELATED_TO"],
                                        })
                                        next_frontier.append((sub_target, path + [relation, "RELATED_TO"]))

            frontier = next_frontier

        # Deduplicate by article_id
        seen: set[str] = set()
        deduped: list[dict] = []
        for r in results:
            aid = r["article_id"]
            if aid not in seen:
                seen.add(aid)
                deduped.append(r)

        return deduped

    def find_citation_chain(self, from_law: str, to_law: str) -> list[str]:
        """Find the citation chain between two laws using BFS.

        Traverses REFERENCES, CITES, and CONTAINS edges to find a path
        from one law to another.

        Args:
            from_law: Name of the starting law.
            to_law: Name of the target law.

        Returns:
            List of law/article names forming the chain, or empty if no path found.
        """
        from_node = self._law_index.get(from_law)
        to_node = self._law_index.get(to_law)
        if not from_node or not to_node:
            return []
        if from_node == to_node:
            return [from_law]

        # BFS over law and article nodes
        visited: set[str] = set()
        queue: list[tuple[str, list[str]]] = [(from_node, [from_law])]
        visited.add(from_node)

        while queue:
            current, path = queue.pop(0)
            node = self._graph.get(current)
            if not node:
                continue

            for target, relation in node["edges"]:
                if target in visited:
                    continue
                target_node = self._graph.get(target)
                if not target_node:
                    continue

                label = target_node["label"]
                new_path = path + [label]

                if target == to_node:
                    return new_path

                # Only traverse through articles and laws
                if target_node["type"] in ("article", "law"):
                    visited.add(target)
                    queue.append((target, new_path))

            # Also check reverse edges (incoming)
            for other_id, other_node in self._graph.items():
                if other_id in visited:
                    continue
                for t, r in other_node["edges"]:
                    if t == current:
                        if other_node["type"] in ("article", "law"):
                            visited.add(other_id)
                            queue.append((other_id, path + [other_node["label"]]))

        return []

    def get_law_family(self, law_name: str) -> list[dict]:
        """Get all related laws (amendments, parent laws, child regulations).

        Args:
            law_name: Name of the law.

        Returns:
            List of dicts with law info and relationship type.
        """
        law_node_id = self._law_index.get(law_name)
        if not law_node_id:
            return []

        family: list[dict] = []

        # Direct edges from this law
        node = self._graph.get(law_node_id)
        if not node:
            return []

        for target, relation in node["edges"]:
            target_node = self._graph.get(target)
            if not target_node:
                continue
            if target_node["type"] == "law":
                family.append({
                    "law_name": target_node["label"],
                    "law_type": target_node["metadata"].get("law_type", ""),
                    "effective_date": target_node["metadata"].get("effective_date", ""),
                    "status": target_node["metadata"].get("status", ""),
                    "relationship": relation,
                })

        # Check reverse edges (other laws that cite or amend this one)
        for other_id, other_node in self._graph.items():
            if other_node["type"] != "law" or other_id == law_node_id:
                continue
            for target, relation in other_node["edges"]:
                if target == law_node_id:
                    family.append({
                        "law_name": other_node["label"],
                        "law_type": other_node["metadata"].get("law_type", ""),
                        "effective_date": other_node["metadata"].get("effective_date", ""),
                        "status": other_node["metadata"].get("status", ""),
                        "relationship": f"reverse:{relation}",
                    })

        return family

    def get_related_context(self, article_ids: list[str]) -> list[dict]:
        """Get graph neighbors for a batch of articles.

        Returns related articles, concepts, and laws connected to the given articles.

        Args:
            article_ids: List of article database IDs.

        Returns:
            List of dicts with neighbor info.
        """
        all_neighbors: dict[str, dict] = {}

        for aid in article_ids:
            node_id = f"article:{aid}"
            node = self._graph.get(node_id)
            if not node:
                continue

            for target, relation in node["edges"]:
                target_node = self._graph.get(target)
                if not target_node or target in all_neighbors:
                    continue

                neighbor_info = {
                    "id": target_node["metadata"].get("id", target),
                    "type": target_node["type"],
                    "label": target_node["label"],
                    "relation": relation,
                }
                if target_node["type"] == "article":
                    neighbor_info["content_preview"] = target_node["metadata"].get("content", "")[:200]
                elif target_node["type"] == "concept":
                    neighbor_info["definition"] = target_node["metadata"].get("definition", "")[:200]

                all_neighbors[target] = neighbor_info

        return list(all_neighbors.values())

    def export_graph_data(self) -> dict:
        """Export graph as JSON for frontend visualization.

        Returns:
            Dict with 'nodes' and 'edges' arrays.
        """
        nodes: list[dict] = []
        edges: list[dict] = []

        for node_id, node in self._graph.items():
            node_data = {
                "id": node_id,
                "type": node["type"],
                "label": node["label"],
            }
            # Include relevant metadata based on type
            meta = node["metadata"]
            if node["type"] == "law":
                node_data["metadata"] = {
                    "law_type": meta.get("law_type", ""),
                    "status": meta.get("status", ""),
                    "effective_date": meta.get("effective_date", ""),
                }
            elif node["type"] == "article":
                node_data["metadata"] = {
                    "law_name": meta.get("law_name", ""),
                    "article_number": meta.get("article_number", ""),
                    "chapter": meta.get("chapter", ""),
                }
            elif node["type"] == "case":
                node_data["metadata"] = {
                    "case_number": meta.get("case_number", ""),
                    "case_type": meta.get("case_type", ""),
                }
            elif node["type"] == "concept":
                node_data["metadata"] = {
                    "definition": meta.get("definition", "")[:200],
                    "category": meta.get("category", ""),
                }
            nodes.append(node_data)

            for target, relation in node["edges"]:
                edges.append({
                    "source": node_id,
                    "target": target,
                    "relation": relation,
                })

        return {"nodes": nodes, "edges": edges}

    def get_stats(self) -> dict[str, Any]:
        """Return statistics about the graph.

        Returns:
            Dict with node counts by type, edge counts by type, etc.
        """
        type_counts: dict[str, int] = defaultdict(int)
        edge_counts: dict[str, int] = defaultdict(int)
        total_edges = 0

        for node in self._graph.values():
            type_counts[node["type"]] += 1
            for _, relation in node["edges"]:
                edge_counts[relation] += 1
                total_edges += 1

        return {
            "node_count": len(self._graph),
            "edge_count": total_edges,
            "law_count": type_counts.get("law", 0),
            "article_count": type_counts.get("article", 0),
            "case_count": type_counts.get("case", 0),
            "concept_count": type_counts.get("concept", 0),
            "nodes_by_type": dict(type_counts),
            "edges_by_type": dict(edge_counts),
        }
