"""Legal knowledge graph manager using Neo4j with in-memory fallback.

Builds and queries a legal domain knowledge graph connecting laws, articles,
cases, crimes, and courts through defined relationships.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class Entity:
    """A node in the legal knowledge graph."""

    entity_type: str
    name: str
    properties: dict[str, Any] = field(default_factory=dict)
    entity_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class Relationship:
    """A directed edge between two entities in the knowledge graph."""

    source_id: str
    target_id: str
    rel_type: str
    properties: dict[str, Any] = field(default_factory=dict)


# Relationship types for the legal domain
RELATIONSHIP_TYPES = {
    "CITES": "One legal document cites another",
    "REFERS_TO": "A document refers to a law or article",
    "SIMILAR_TO": "Two cases have similar facts or rulings",
    "APPEALED_FROM": "A case was appealed from a lower court",
    "OVERRULES": "A higher court or newer law overrules a prior decision",
    "APPLIES": "A law or article applies to a case",
    "DEFINES": "A law defines a legal concept",
    "RELATED_TO": "General relationship between entities",
}


class InMemoryGraph:
    """Simple in-memory graph store used as fallback when Neo4j is unavailable."""

    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self.relationships: list[Relationship] = []
        self._index_by_type: dict[str, list[str]] = {}
        self._index_by_name: dict[str, str] = {}

    def add_entity(self, entity: Entity) -> str:
        self.entities[entity.entity_id] = entity
        self._index_by_type.setdefault(entity.entity_type, []).append(entity.entity_id)
        self._index_by_name[entity.name] = entity.entity_id
        return entity.entity_id

    def add_relationship(self, rel: Relationship) -> None:
        self.relationships.append(rel)

    def get_entity(self, entity_id: str) -> Entity | None:
        return self.entities.get(entity_id)

    def find_by_type(self, entity_type: str) -> list[Entity]:
        ids = self._index_by_type.get(entity_type, [])
        return [self.entities[eid] for eid in ids if eid in self.entities]

    def find_by_name(self, name: str) -> Entity | None:
        eid = self._index_by_name.get(name)
        if eid:
            return self.entities.get(eid)
        return None

    def get_related(self, entity_id: str, rel_type: str | None = None) -> list[tuple[Entity, Relationship]]:
        results: list[tuple[Entity, Relationship]] = []
        for rel in self.relationships:
            if rel_type and rel.rel_type != rel_type:
                continue
            if rel.source_id == entity_id:
                target = self.entities.get(rel.target_id)
                if target:
                    results.append((target, rel))
            elif rel.target_id == entity_id:
                source = self.entities.get(rel.source_id)
                if source:
                    results.append((source, rel))
        return results

    def clear(self) -> None:
        self.entities.clear()
        self.relationships.clear()
        self._index_by_type.clear()
        self._index_by_name.clear()


class KnowledgeGraphManager:
    """Manages the legal knowledge graph with Neo4j (primary) and in-memory (fallback).

    Entity types:
    - Law: A statute or regulation
    - Article: A specific article/section within a law
    - Case: A court case or judgment
    - Crime: A criminal offense type
    - Court: A court or tribunal
    - LegalConcept: An abstract legal concept or principle

    Attributes:
        backend: "neo4j" or "memory".
        _driver: Neo4j driver instance (if connected).
        _memory_graph: InMemoryGraph instance (fallback).
    """

    def __init__(self) -> None:
        """Initialize the knowledge graph manager."""
        self._driver: Any = None
        self._memory_graph = InMemoryGraph()
        self._backend: str | None = None
        self._schema_built: bool = False

    @property
    def backend(self) -> str:
        """Return the active backend name."""
        if self._backend is None:
            self._backend = self._detect_backend()
        return self._backend

    def _detect_backend(self) -> str:
        """Detect Neo4j availability; fall back to in-memory graph."""
        if self._try_neo4j_connection():
            logger.info("Using Neo4j as knowledge graph backend")
            return "neo4j"
        logger.info("Neo4j unavailable, using in-memory knowledge graph")
        return "memory"

    def _try_neo4j_connection(self) -> bool:
        """Attempt to connect to Neo4j."""
        try:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            )
            with driver.session() as session:
                session.run("RETURN 1")
            driver.close()
            return True
        except ImportError:
            logger.debug("neo4j driver not installed")
            return False
        except Exception as exc:
            logger.debug("Neo4j connection failed: %s", exc)
            return False

    def _get_neo4j_driver(self) -> Any:
        """Get or create Neo4j driver."""
        if self._driver is None:
            from neo4j import GraphDatabase

            self._driver = GraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            )
        return self._driver

    async def build_schema(self) -> None:
        """Build the legal knowledge graph schema (constraints and indexes).

        Creates uniqueness constraints and indexes for entity types
        in Neo4j, or initializes the in-memory graph structure.
        """
        if self._schema_built:
            return

        if self.backend == "neo4j":
            driver = self._get_neo4j_driver()
            constraints = [
                "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Law) REQUIRE n.name IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Article) REQUIRE n.article_id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Case) REQUIRE n.case_id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Court) REQUIRE n.name IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Crime) REQUIRE n.name IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (n:LegalConcept) REQUIRE n.name IS UNIQUE",
            ]
            with driver.session() as session:
                for constraint in constraints:
                    try:
                        session.run(constraint)
                    except Exception as exc:
                        logger.debug("Constraint may already exist: %s", exc)

        self._schema_built = True
        logger.info("Knowledge graph schema built (backend=%s)", self.backend)

    async def create_entity(
        self,
        entity_type: str,
        name: str,
        properties: dict[str, Any] | None = None,
    ) -> str:
        """Create a new entity in the knowledge graph.

        Args:
            entity_type: Type of entity (Law, Article, Case, Court, Crime, LegalConcept).
            name: Display name of the entity.
            properties: Additional properties for the entity.

        Returns:
            The entity ID.
        """
        entity = Entity(
            entity_type=entity_type,
            name=name,
            properties=properties or {},
        )

        if self.backend == "neo4j":
            driver = self._get_neo4j_driver()
            prop_dict = {"name": name, "entity_id": entity.entity_id, **(properties or {})}
            prop_json = json.dumps(prop_dict, ensure_ascii=False)

            with driver.session() as session:
                session.run(
                    f"MERGE (n:{entity_type} {{entity_id: $entity_id}}) "
                    "SET n = apoc.convert.fromJsonMap($props)",
                    entity_id=entity.entity_id,
                    props=prop_json,
                )
        else:
            self._memory_graph.add_entity(entity)

        logger.debug("Created entity: %s '%s' (%s)", entity_type, name, entity.entity_id)
        return entity.entity_id

    async def create_relationship(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Create a relationship between two entities.

        Args:
            source_id: ID of the source entity.
            target_id: ID of the target entity.
            rel_type: Type of relationship (CITES, REFERS_TO, SIMILAR_TO, etc.).
            properties: Additional properties for the relationship.
        """
        if rel_type not in RELATIONSHIP_TYPES:
            logger.warning("Unknown relationship type: %s. Using as-is.", rel_type)

        rel = Relationship(
            source_id=source_id,
            target_id=target_id,
            rel_type=rel_type,
            properties=properties or {},
        )

        if self.backend == "neo4j":
            driver = self._get_neo4j_driver()
            with driver.session() as session:
                session.run(
                    f"MATCH (a {{entity_id: $source_id}}) "
                    f"MATCH (b {{entity_id: $target_id}}) "
                    f"MERGE (a)-[r:{rel_type}]->(b) "
                    "SET r = $props",
                    source_id=source_id,
                    target_id=target_id,
                    props=properties or {},
                )
        else:
            self._memory_graph.add_relationship(rel)

        logger.debug("Created relationship: %s -[%s]-> %s", source_id, rel_type, target_id)

    async def query_related_laws(
        self,
        case_id: str | None = None,
        article_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Query laws related to a given case or article.

        Args:
            case_id: Optional case ID to find related laws.
            article_id: Optional article ID to find related laws.
            limit: Maximum number of results.

        Returns:
            List of related law entities with relationship info.
        """
        if self.backend == "neo4j":
            return await self._neo4j_query_related(entity_id=case_id or article_id or "",
                                                     target_type="Law", limit=limit)
        else:
            return await self._memory_query_related(entity_id=case_id or article_id or "",
                                                      target_type="Law", limit=limit)

    async def query_related_cases(
        self,
        law_id: str | None = None,
        article_id: str | None = None,
        crime_name: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Query cases related to a law, article, or crime.

        Args:
            law_id: Optional law ID to find related cases.
            article_id: Optional article ID to find related cases.
            crime_name: Optional crime name to find related cases.
            limit: Maximum number of results.

        Returns:
            List of related case entities with relationship info.
        """
        entity_id = law_id or article_id or ""
        if not entity_id and crime_name:
            # Search by crime name
            if self.backend == "neo4j":
                driver = self._get_neo4j_driver()
                with driver.session() as session:
                    result = session.run(
                        "MATCH (c:Crime {name: $name})-[r]-(ca:Case) "
                        "RETURN ca, r LIMIT $limit",
                        name=crime_name, limit=limit,
                    )
                    return [self._neo4j_record_to_dict(record) for record in result]
            else:
                crime = self._memory_graph.find_by_name(crime_name)
                if crime:
                    related = self._memory_graph.get_related(crime.entity_id)
                    return [
                        {"entity": {"name": e.name, "type": e.entity_type, "id": e.entity_id},
                         "relationship": r.rel_type}
                        for e, r in related if e.entity_type == "Case"
                    ][:limit]
                return []

        if self.backend == "neo4j":
            return await self._neo4j_query_related(entity_id=entity_id, target_type="Case", limit=limit)
        else:
            return await self._memory_query_related(entity_id=entity_id, target_type="Case", limit=limit)

    async def _neo4j_query_related(
        self,
        entity_id: str,
        target_type: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Query related entities of a specific type from Neo4j."""
        if not entity_id:
            return []

        driver = self._get_neo4j_driver()
        with driver.session() as session:
            result = session.run(
                f"MATCH (a {{entity_id: $entity_id}})-[r]-(b:{target_type}) "
                "RETURN b, type(r) as rel_type LIMIT $limit",
                entity_id=entity_id, limit=limit,
            )
            return [self._neo4j_record_to_dict(record) for record in result]

    def _neo4j_record_to_dict(self, record: Any) -> dict[str, Any]:
        """Convert a Neo4j record to a dictionary."""
        try:
            b_node = record["b"]
            rel_type = record.get("rel_type", "")
            return {
                "entity": dict(b_node) if hasattr(b_node, "items") else b_node,
                "relationship": rel_type,
            }
        except Exception:
            return {"entity": {}, "relationship": ""}

    async def _memory_query_related(
        self,
        entity_id: str,
        target_type: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Query related entities of a specific type from in-memory graph."""
        if not entity_id:
            return []

        related = self._memory_graph.get_related(entity_id)
        results = []
        for entity, rel in related:
            if entity.entity_type == target_type:
                results.append({
                    "entity": {
                        "name": entity.name,
                        "type": entity.entity_type,
                        "id": entity.entity_id,
                        "properties": entity.properties,
                    },
                    "relationship": rel.rel_type,
                })
                if len(results) >= limit:
                    break
        return results

    async def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        """Get an entity by ID.

        Args:
            entity_id: The entity ID.

        Returns:
            Entity dict or None if not found.
        """
        if self.backend == "neo4j":
            driver = self._get_neo4j_driver()
            with driver.session() as session:
                result = session.run(
                    "MATCH (n {entity_id: $entity_id}) RETURN n",
                    entity_id=entity_id,
                )
                record = result.single()
                if record:
                    node = record["n"]
                    return dict(node) if hasattr(node, "items") else node
                return None
        else:
            entity = self._memory_graph.get_entity(entity_id)
            if entity:
                return {
                    "name": entity.name,
                    "type": entity.entity_type,
                    "id": entity.entity_id,
                    "properties": entity.properties,
                }
            return None

    async def search_entities(
        self,
        entity_type: str | None = None,
        keyword: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search for entities by type and keyword.

        Args:
            entity_type: Optional entity type filter.
            keyword: Search keyword (case-insensitive partial match).
            limit: Maximum number of results.

        Returns:
            List of matching entity dicts.
        """
        if self.backend == "neo4j":
            driver = self._get_neo4j_driver()
            type_clause = f":{entity_type}" if entity_type else ""
            query = (
                f"MATCH (n{type_clause}) "
                "WHERE n.name CONTAINS $keyword "
                "RETURN n LIMIT $limit"
            )
            with driver.session() as session:
                result = session.run(query, keyword=keyword, limit=limit)
                return [dict(record["n"]) if hasattr(record["n"], "items") else record["n"]
                        for record in result]
        else:
            entities = self._memory_graph.find_by_type(entity_type) if entity_type else list(self._memory_graph.entities.values())
            if keyword:
                kw_lower = keyword.lower()
                entities = [e for e in entities if kw_lower in e.name.lower()]
            return [
                {"name": e.name, "type": e.entity_type, "id": e.entity_id, "properties": e.properties}
                for e in entities[:limit]
            ]

    def get_stats(self) -> dict[str, Any]:
        """Return statistics about the knowledge graph.

        Returns:
            Dict with backend, entity counts, and relationship counts.
        """
        if self.backend == "neo4j":
            try:
                driver = self._get_neo4j_driver()
                with driver.session() as session:
                    result = session.run(
                        "MATCH (n) RETURN labels(n)[0] as type, count(n) as cnt "
                        "UNION ALL "
                        "MATCH ()-[r]->() RETURN type(r) as type, count(r) as cnt"
                    )
                    stats: dict[str, Any] = {"backend": "neo4j", "entities": {}, "relationships": {}}
                    for record in result:
                        stats["entities"][record["type"]] = record["cnt"]
                    return stats
            except Exception:
                pass

        stats = {"backend": "memory", "entities": {}, "relationships": {}}
        type_counts: dict[str, int] = {}
        for entity in self._memory_graph.entities.values():
            type_counts[entity.entity_type] = type_counts.get(entity.entity_type, 0) + 1
        stats["entities"] = type_counts

        rel_counts: dict[str, int] = {}
        for rel in self._memory_graph.relationships:
            rel_counts[rel.rel_type] = rel_counts.get(rel.rel_type, 0) + 1
        stats["relationships"] = rel_counts
        return stats