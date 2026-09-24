"""
Skills Assembly System -- Composable skill packages for legal workflows.

Skills are reusable, parameterized legal workflow templates that combine
multiple tools and agents into higher-level operations.

Architecture:
    SkillDefinition (YAML/JSON)
      └── SkillEngine (orchestrates execution)
            ├── skill_labor_dispute (劳动争议处理技能包)
            ├── skill_contract_analysis (合同分析技能包)
            ├── skill_ip_protection (知识产权保护技能包)
            ├── skill_criminal_defense (刑事辩护辅助技能包)
            ├── skill_enterprise_compliance (企业合规审查技能包)
            ├── skill_litigation_preparation (诉讼准备技能包)
            ├── skill_debt_recovery (债务追讨技能包)
            ├── skill_labor_dispute_guide (劳动争议处理指南)
            ├── skill_contract_dispute_guide (合同纠纷处理指南)
            ├── skill_marriage_family_guide (婚姻家事法律指南)
            ├── skill_ip_protection_guide (知识产权保护指南)
            ├── skill_corporate_compliance_guide (企业合规审查指南)
            ├── skill_debt_credit_guide (债权债务处理指南)
            ├── skill_real_estate_dispute_guide (房产纠纷处理指南)
            ├── skill_traffic_accident_guide (交通事故理赔指南)
            ├── skill_criminal_defense_guide (刑事辩护辅助指南)
            ├── skill_administrative_litigation_guide (行政诉讼指南)
            ├── skill_consumer_rights_guide (消费者权益维权指南)
            └── skill_corporate_legal_daily_guide (公司法务日常指南)
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

import yaml

logger = logging.getLogger(__name__)


# =============================================================================
# Skill Definition Types
# =============================================================================

@dataclass
class SkillStep:
    """A single step in a skill workflow."""
    id: str
    name: str
    type: str  # "tool" | "agent" | "llm" | "condition" | "transform"
    config: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class SkillDefinition:
    """Complete definition of a skill package."""
    id: str
    name: str
    description: str
    category: str  # labor / contract / ip / criminal / compliance / litigation / general
    version: str = "1.0.0"
    author: str = "system"
    tags: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    steps: list[SkillStep] = field(default_factory=list)
    estimated_time_seconds: int = 30
    difficulty: str = "medium"  # easy / medium / complex


# =============================================================================
# Skill Engine
# =============================================================================

class SkillEngine:
    """Orchestrates skill execution.

    Manages skill registration, validation, and step-by-step execution
    with dependency resolution and error handling.
    """

    _instance: Optional[SkillEngine] = None

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}
        self._execution_history: list[dict[str, Any]] = []
        self._register_builtin_skills()

    @classmethod
    def get_instance(cls) -> SkillEngine:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # -------------------------------------------------------------------------
    # Registration
    # -------------------------------------------------------------------------

    def register_skill(self, skill: SkillDefinition) -> None:
        """Register a skill definition."""
        self._skills[skill.id] = skill
        logger.info("Registered skill: %s (%s)", skill.name, skill.category)

    def register_from_yaml(self, yaml_content: str) -> SkillDefinition:
        """Register a skill from YAML definition."""
        data = yaml.safe_load(yaml_content)
        steps = []
        for step_data in data.get("steps", []):
            steps.append(SkillStep(
                id=step_data.get("id", f"step_{len(steps)}"),
                name=step_data.get("name", ""),
                type=step_data.get("type", "llm"),
                config=step_data.get("config", {}),
                depends_on=step_data.get("depends_on", []),
                description=step_data.get("description", ""),
            ))

        skill = SkillDefinition(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            category=data.get("category", "general"),
            version=data.get("version", "1.0.0"),
            author=data.get("author", "system"),
            tags=data.get("tags", []),
            input_schema=data.get("input_schema", {}),
            output_schema=data.get("output_schema", {}),
            steps=steps,
            estimated_time_seconds=data.get("estimated_time_seconds", 30),
            difficulty=data.get("difficulty", "medium"),
        )
        self.register_skill(skill)
        return skill

    def _register_builtin_skills(self) -> None:
        """Register all built-in skill packages."""
        for skill_def in get_builtin_skills():
            self.register_skill(skill_def)

    # -------------------------------------------------------------------------
    # Discovery
    # -------------------------------------------------------------------------

    def list_skills(self, category: str | None = None) -> list[dict[str, Any]]:
        """List all available skills."""
        skills = self._skills.values()
        if category:
            skills = [s for s in skills if s.category == category]
        return [self._skill_to_dict(s) for s in skills]

    def get_skill(self, skill_id: str) -> SkillDefinition | None:
        """Get a skill definition by ID."""
        return self._skills.get(skill_id)

    def list_categories(self) -> list[str]:
        """List all skill categories."""
        return sorted(set(s.category for s in self._skills.values()))

    def search_skills(self, query: str) -> list[dict[str, Any]]:
        """Search skills by keyword in name, description, or tags."""
        query_lower = query.lower()
        results = []
        for skill in self._skills.values():
            if (query_lower in skill.name.lower() or
                    query_lower in skill.description.lower() or
                    any(query_lower in tag.lower() for tag in skill.tags)):
                results.append(self._skill_to_dict(skill))
        return results

    # -------------------------------------------------------------------------
    # Execution
    # -------------------------------------------------------------------------

    async def execute_skill(
        self,
        skill_id: str,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a skill with the given input data.

        Steps are executed in dependency order. Each step's output
        is stored in the execution context and made available to
        subsequent steps.
        """
        skill = self.get_skill(skill_id)
        if not skill:
            return {"success": False, "error": f"Skill '{skill_id}' not found"}

        start_time = time.time()
        context: dict[str, Any] = {"input": input_data, "results": {}}

        logger.info("Executing skill: %s with input keys: %s",
                     skill_id, list(input_data.keys()))

        try:
            # Execute steps in order (respecting dependencies)
            ordered_steps = self._resolve_step_order(skill.steps)
            step_results: dict[str, Any] = {}

            for step in ordered_steps:
                # Check dependencies
                for dep in step.depends_on:
                    if dep not in step_results:
                        return {
                            "success": False,
                            "error": f"Step '{step.id}' depends on '{dep}' which hasn't executed",
                        }

                # Execute the step
                step_result = await self._execute_step(step, context)
                step_results[step.id] = step_result
                context["results"][step.id] = step_result

                if not step_result.get("success", True):
                    logger.warning("Step '%s' failed in skill '%s': %s",
                                   step.id, skill_id, step_result.get("error"))
                    # Continue with other steps unless marked as critical
                    if step.config.get("critical", True):
                        return {
                            "success": False,
                            "error": f"Step '{step.name}' failed: {step_result.get('error')}",
                            "step_id": step.id,
                            "partial_results": step_results,
                        }

            # Build final output
            elapsed = time.time() - start_time
            output = {
                "success": True,
                "skill_id": skill_id,
                "skill_name": skill.name,
                "steps_executed": len(ordered_steps),
                "step_results": step_results,
                "elapsed_seconds": round(elapsed, 2),
                "final_summary": self._build_summary(skill, step_results),
            }

            # Log execution
            self._execution_history.append({
                "skill_id": skill_id,
                "timestamp": time.time(),
                "elapsed": elapsed,
                "success": True,
                "input_keys": list(input_data.keys()),
            })

            return output

        except Exception as exc:
            elapsed = time.time() - start_time
            logger.error("Skill execution failed: %s - %s", skill_id, exc)
            return {
                "success": False,
                "error": str(exc),
                "skill_id": skill_id,
                "elapsed_seconds": round(elapsed, 2),
            }

    async def execute_skill_stream(
        self,
        skill_id: str,
        input_data: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a skill with streaming progress updates."""
        skill = self.get_skill(skill_id)
        if not skill:
            yield {"type": "error", "error": f"Skill '{skill_id}' not found"}
            return

        yield {
            "type": "skill_start",
            "skill_id": skill_id,
            "skill_name": skill.name,
            "total_steps": len(skill.steps),
        }

        context: dict[str, Any] = {"input": input_data, "results": {}}
        ordered_steps = self._resolve_step_order(skill.steps)
        step_results: dict[str, Any] = {}

        for i, step in enumerate(ordered_steps):
            yield {
                "type": "step_start",
                "step_id": step.id,
                "step_name": step.name,
                "step_index": i + 1,
                "total_steps": len(ordered_steps),
            }

            try:
                step_result = await self._execute_step(step, context)
                step_results[step.id] = step_result
                context["results"][step.id] = step_result

                yield {
                    "type": "step_complete",
                    "step_id": step.id,
                    "step_name": step.name,
                    "result_preview": self._preview_result(step_result),
                }
            except Exception as exc:
                yield {
                    "type": "step_error",
                    "step_id": step.id,
                    "step_name": step.name,
                    "error": str(exc),
                }

        yield {
            "type": "skill_complete",
            "skill_id": skill_id,
            "final_summary": self._build_summary(skill, step_results),
            "step_results": step_results,
        }

    # -------------------------------------------------------------------------
    # Internal execution helpers
    # -------------------------------------------------------------------------

    async def _execute_step(
        self, step: SkillStep, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a single skill step."""
        if step.type == "tool":
            return await self._execute_tool_step(step, context)
        elif step.type == "agent":
            return await self._execute_agent_step(step, context)
        elif step.type == "llm":
            return await self._execute_llm_step(step, context)
        elif step.type == "condition":
            return self._execute_condition_step(step, context)
        elif step.type == "transform":
            return self._execute_transform_step(step, context)
        else:
            return {"success": False, "error": f"Unknown step type: {step.type}"}

    async def _execute_tool_step(
        self, step: SkillStep, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a tool call step."""
        from app.mcp import get_tool_registry_sync
        registry = get_tool_registry_sync()

        tool_name = step.config.get("tool", "")
        # Resolve parameter templates with context
        params = self._resolve_templates(step.config.get("parameters", {}), context)

        result = await registry.execute_tool(tool_name, **params)
        return result.to_dict()

    async def _execute_agent_step(
        self, step: SkillStep, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute an agent step."""
        agent_type = step.config.get("agent", "")
        input_template = step.config.get("input", {})
        input_data = self._resolve_templates(input_template, context)

        if agent_type == "legal_consult":
            from app.agents.legal_consult_agent import create_legal_consult_agent
            agent = create_legal_consult_agent()
            result = await agent.run(input_data)
        elif agent_type == "contract_review":
            from app.agents.contract_review_agent import create_contract_review_agent
            agent = create_contract_review_agent()
            result = await agent.run(input_data)
        elif agent_type == "document_gen":
            from app.agents.document_gen_agent import create_document_gen_agent
            agent = create_document_gen_agent()
            result = await agent.run(input_data)
        elif agent_type == "law_retrieval":
            from app.agents.law_retrieval_agent import create_law_retrieval_agent
            agent = create_law_retrieval_agent()
            result = await agent.run(input_data)
        elif agent_type == "collaboration":
            from app.agents.collaboration import create_multi_agent_collaborator
            collaborator = create_multi_agent_collaborator()
            result = await collaborator.run(
                query=input_data.get("query", ""),
                context=context.get("results", {}),
            )
        else:
            return {"success": False, "error": f"Unknown agent type: {agent_type}"}

        return result

    async def _execute_llm_step(
        self, step: SkillStep, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute an LLM prompt step."""
        from app.services.llm_service import get_raw_llm_service
        llm = get_raw_llm_service()

        prompt_template = step.config.get("prompt", "")
        prompt = self._resolve_template_string(prompt_template, context)
        system_prompt = step.config.get("system_prompt", "你是一位专业的法律助手。")
        temperature = step.config.get("temperature", 0.3)
        max_tokens = step.config.get("max_tokens", 4096)

        try:
            result = await llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return {
                "success": True,
                "content": result.get("content", ""),
                "tokens": result.get("tokens", {}),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _execute_condition_step(
        self, step: SkillStep, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a conditional branch step."""
        condition = step.config.get("condition", "")
        # Simple condition evaluation based on context
        try:
            # Resolve template variables in condition
            resolved = self._resolve_template_string(condition, context)
            # Evaluate simple conditions
            result = bool(resolved) and resolved.lower() not in ("false", "0", "none", "")
            return {"success": True, "result": result}
        except Exception:
            return {"success": True, "result": False}

    def _execute_transform_step(
        self, step: SkillStep, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a data transformation step."""
        transform_type = step.config.get("transform", "extract")
        source = step.config.get("source", "")
        source_data = self._resolve_template_string(source, context) if source else context

        if transform_type == "extract":
            # Extract specific fields
            fields = step.config.get("fields", [])
            extracted = {}
            if isinstance(source_data, dict):
                for f in fields:
                    extracted[f] = source_data.get(f)
            return {"success": True, "extracted": extracted}
        elif transform_type == "format":
            # Format output using template
            template = step.config.get("template", "")
            formatted = self._resolve_template_string(template, context)
            return {"success": True, "formatted": formatted}
        else:
            return {"success": True, "data": source_data}

    # -------------------------------------------------------------------------
    # Template resolution
    # -------------------------------------------------------------------------

    def _resolve_templates(
        self, obj: Any, context: dict[str, Any]
    ) -> Any:
        """Recursively resolve template strings in a nested structure."""
        if isinstance(obj, str):
            return self._resolve_template_string(obj, context)
        elif isinstance(obj, dict):
            return {k: self._resolve_templates(v, context) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_templates(item, context) for item in obj]
        return obj

    def _resolve_template_string(
        self, template: str, context: dict[str, Any]
    ) -> str:
        """Resolve {context.key} and {input.key} placeholders."""
        import re
        pattern = r'\{(?:context|input|results)\.([^}]+)\}'

        def replacer(match: re.Match) -> str:
            key_path = match.group(1)
            parts = key_path.split(".")
            value = context
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part, "")
                else:
                    return ""
            return str(value) if value is not None else ""

        return re.sub(pattern, replacer, template)

    def _resolve_step_order(self, steps: list[SkillStep]) -> list[SkillStep]:
        """Resolve step execution order based on dependencies (topological sort)."""
        if not steps:
            return []

        # Simple topological sort
        visited: set[str] = set()
        ordered: list[SkillStep] = []
        step_map = {s.id: s for s in steps}

        def visit(step: SkillStep) -> None:
            if step.id in visited:
                return
            visited.add(step.id)
            for dep_id in step.depends_on:
                if dep_id in step_map:
                    visit(step_map[dep_id])
            ordered.append(step)

        for step in steps:
            visit(step)

        return ordered

    def _build_summary(
        self, skill: SkillDefinition, step_results: dict[str, Any]
    ) -> str:
        """Build a human-readable summary of skill execution results."""
        parts = [f"## {skill.name} 执行结果\n"]

        for step_id, result in step_results.items():
            if isinstance(result, dict):
                content = result.get("content", "") or result.get("final_output", "")
                if content:
                    # Find the step name
                    step_name = step_id
                    for s in skill.steps:
                        if s.id == step_id:
                            step_name = s.name
                            break
                    parts.append(f"### {step_name}\n{content[:2000]}\n")

        return "\n".join(parts) if len(parts) > 1 else "技能执行完成"

    def _preview_result(self, result: dict[str, Any]) -> str:
        """Create a short preview of a step result."""
        content = result.get("content", "") or result.get("data", "")
        if isinstance(content, str):
            return content[:200]
        elif isinstance(content, (dict, list)):
            return json.dumps(content, ensure_ascii=False)[:200]
        return str(content)[:200]

    def _skill_to_dict(self, skill: SkillDefinition) -> dict[str, Any]:
        """Convert skill definition to dict."""
        return {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "category": skill.category,
            "version": skill.version,
            "author": skill.author,
            "tags": skill.tags,
            "steps_count": len(skill.steps),
            "estimated_time_seconds": skill.estimated_time_seconds,
            "difficulty": skill.difficulty,
            "input_schema": skill.input_schema,
        }

    def get_stats(self) -> dict[str, Any]:
        """Get engine statistics."""
        return {
            "total_skills": len(self._skills),
            "categories": self.list_categories(),
            "total_executions": len(self._execution_history),
            "successful_executions": sum(
                1 for e in self._execution_history if e.get("success")
            ),
        }


# =============================================================================
# Built-in Skill Definitions
# =============================================================================

def get_builtin_skills() -> list[SkillDefinition]:
    """Return all built-in skill definitions."""
    return [
        # Original skills
        _labor_dispute_skill(),
        _contract_analysis_skill(),
        _ip_protection_skill(),
        _criminal_defense_skill(),
        _enterprise_compliance_skill(),
        _litigation_preparation_skill(),
        _debt_recovery_skill(),
        # New comprehensive guide skills
        _labor_dispute_guide_skill(),
        _contract_dispute_guide_skill(),
        _marriage_family_guide_skill(),
        _ip_protection_guide_skill(),
        _corporate_compliance_guide_skill(),
        _debt_credit_guide_skill(),
        _real_estate_dispute_guide_skill(),
        _traffic_accident_guide_skill(),
        _criminal_defense_guide_skill(),
        _administrative_litigation_guide_skill(),
        _consumer_rights_guide_skill(),
        _corporate_legal_daily_guide_skill(),
    ]


def _labor_dispute_skill() -> SkillDefinition:
    """劳动争议处理技能包"""
    return SkillDefinition(
        id="labor_dispute",
        name="劳动争议处理技能包",
        description="全面的劳动争议处理方案：包括案情分析、法律检索、证据清单生成、仲裁申请书起草",
        category="labor",
        version="1.0.0",
        tags=["劳动争议", "劳动仲裁", "工资纠纷", "解雇赔偿", "工伤"],
        input_schema={
            "type": "object",
            "properties": {
                "dispute_description": {"type": "string", "description": "劳动争议描述"},
                "employee_info": {"type": "object", "description": "劳动者信息"},
                "employer_info": {"type": "object", "description": "用人单位信息"},
                "dispute_type": {"type": "string", "enum": ["工资纠纷", "违法解雇", "工伤赔偿", "社保纠纷", "竞业限制", "其他"]},
            },
            "required": ["dispute_description"],
        },
        steps=[
            SkillStep(
                id="case_analysis",
                name="案情分析",
                type="llm",
                description="分析劳动争议案情，提取关键要素",
                config={
                    "system_prompt": "你是一位专业的劳动法律师，擅长处理各类劳动争议案件。",
                    "prompt": "请分析以下劳动争议案件，提取关键法律要素：\n\n案情描述：{input.dispute_description}\n\n请分析：\n1. 争议类型和性质\n2. 关键时间节点（入职日期、争议发生日期、仲裁时效等）\n3. 双方权利义务关系\n4. 核心争议焦点\n5. 适用的法律法规\n6. 初步法律意见",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="law_search",
                name="相关法律检索",
                type="tool",
                description="检索劳动争议相关法律法规",
                depends_on=["case_analysis"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "{input.dispute_description}",
                        "category": "social",
                        "page_size": 10,
                    },
                },
            ),
            SkillStep(
                id="case_search",
                name="类案检索",
                type="tool",
                description="检索类似劳动争议案例",
                depends_on=["case_analysis"],
                config={
                    "tool": "wenshu_search",
                    "parameters": {
                        "keyword": "{input.dispute_description}",
                        "case_type": "民事",
                        "page_size": 5,
                    },
                },
            ),
            SkillStep(
                id="compensation_calc",
                name="赔偿金计算",
                type="llm",
                description="计算可能的赔偿金额",
                depends_on=["case_analysis"],
                config={
                    "system_prompt": "你是劳动法赔偿计算专家。",
                    "prompt": "基于以下案情分析结果，计算可能的赔偿金额：\n\n{results.case_analysis}\n\n请计算：\n1. 经济补偿金（如适用）\n2. 赔偿金（如适用）\n3. 工资差额\n4. 其他可主张金额\n\n引用《劳动合同法》相关条款。",
                    "temperature": 0.2,
                },
            ),
            SkillStep(
                id="evidence_list",
                name="证据清单生成",
                type="llm",
                description="生成证据清单",
                depends_on=["case_analysis", "law_search"],
                config={
                    "system_prompt": "你是劳动争议证据整理专家。",
                    "prompt": "基于以下案情和法律依据，列出需要准备的证据清单：\n\n案情分析：{results.case_analysis}\n法律依据：{results.law_search}\n\n请列出：\n1. 必要证据（必须提供）\n2. 辅助证据（建议提供）\n3. 证据收集建议\n4. 注意事项",
                    "temperature": 0.2,
                },
            ),
            SkillStep(
                id="summary_report",
                name="综合分析报告",
                type="llm",
                description="生成综合分析报告",
                depends_on=["case_analysis", "law_search", "case_search", "compensation_calc", "evidence_list"],
                config={
                    "system_prompt": "你是专业的劳动法律师，请综合以上分析结果生成完整的劳动争议处理报告。",
                    "prompt": "请综合以下各项分析，生成一份完整的劳动争议处理报告：\n\n案情分析：{results.case_analysis}\n法律依据：{results.law_search}\n类案参考：{results.case_search}\n赔偿计算：{results.compensation_calc}\n证据清单：{results.evidence_list}\n\n报告结构：\n1. 案情概述\n2. 法律分析\n3. 类案参考\n4. 赔偿计算\n5. 证据准备\n6. 处理建议\n7. 风险提示",
                    "temperature": 0.3,
                    "max_tokens": 4096,
                },
            ),
        ],
        estimated_time_seconds=60,
        difficulty="medium",
    )


def _contract_analysis_skill() -> SkillDefinition:
    """合同分析技能包"""
    return SkillDefinition(
        id="contract_analysis",
        name="合同分析技能包",
        description="全面合同分析：风险识别、条款对比、合规检查、修改建议生成",
        category="contract",
        version="1.0.0",
        tags=["合同审查", "风险分析", "条款对比", "合规检查"],
        input_schema={
            "type": "object",
            "properties": {
                "contract_text": {"type": "string", "description": "合同文本"},
                "review_focus": {"type": "string", "description": "审查重点"},
                "contract_type": {"type": "string", "description": "合同类型"},
            },
            "required": ["contract_text"],
        },
        steps=[
            SkillStep(
                id="risk_analysis",
                name="风险识别",
                type="llm",
                config={
                    "system_prompt": "你是资深合同审查律师，精通合同法和各类合同的风险点。",
                    "prompt": "请对以下合同进行全面风险审查：\n\n{input.contract_text}\n\n审查重点：{input.review_focus}\n\n请从以下维度分析：\n1. 违约责任条款\n2. 知识产权条款\n3. 保密条款\n4. 竞业限制条款\n5. 管辖权条款\n6. 付款条款\n7. 终止条款\n8. 免责条款\n\n对每个风险点标注等级（高/中/低）。",
                    "temperature": 0.2,
                },
            ),
            SkillStep(
                id="compliance_check",
                name="合规检查",
                type="tool",
                depends_on=["risk_analysis"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "{input.contract_text}",
                        "category": "civil",
                        "page_size": 10,
                    },
                },
            ),
            SkillStep(
                id="modification_suggestions",
                name="修改建议生成",
                type="llm",
                depends_on=["risk_analysis", "compliance_check"],
                config={
                    "system_prompt": "你是合同修改建议专家。",
                    "prompt": "基于以下风险分析结果和法律依据，生成具体的合同修改建议：\n\n风险分析：{results.risk_analysis}\n相关法律：{results.compliance_check}\n\n请提供：\n1. 必须修改的条款（附修改后文本）\n2. 建议增加的条款\n3. 建议删除的条款\n4. 谈判策略建议",
                    "temperature": 0.3,
                },
            ),
        ],
        estimated_time_seconds=45,
        difficulty="medium",
    )


def _ip_protection_skill() -> SkillDefinition:
    """知识产权保护技能包"""
    return SkillDefinition(
        id="ip_protection",
        name="知识产权保护技能包",
        description="知识产权侵权分析、维权方案、证据保全建议",
        category="ip",
        version="1.0.0",
        tags=["知识产权", "专利", "商标", "著作权", "侵权"],
        input_schema={
            "type": "object",
            "properties": {
                "ip_type": {"type": "string", "enum": ["专利", "商标", "著作权", "商业秘密", "其他"]},
                "description": {"type": "string", "description": "知识产权情况描述"},
                "infringement_description": {"type": "string", "description": "侵权情况描述"},
            },
            "required": ["description"],
        },
        steps=[
            SkillStep(
                id="ip_analysis",
                name="知识产权分析",
                type="llm",
                config={
                    "system_prompt": "你是知识产权法专家。",
                    "prompt": "请分析以下知识产权情况：\n\n类型：{input.ip_type}\n情况描述：{input.description}\n侵权情况：{input.infringement_description}\n\n分析：\n1. 权利归属和保护范围\n2. 侵权行为认定\n3. 可能的抗辩事由\n4. 维权策略建议",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="ip_law_search",
                name="相关法律检索",
                type="tool",
                depends_on=["ip_analysis"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "知识产权 侵权",
                        "page_size": 10,
                    },
                },
            ),
        ],
        estimated_time_seconds=40,
        difficulty="medium",
    )


def _criminal_defense_skill() -> SkillDefinition:
    """刑事辩护辅助技能包"""
    return SkillDefinition(
        id="criminal_defense",
        name="刑事辩护辅助技能包",
        description="刑事案件分析、辩护策略、量刑参考",
        category="criminal",
        version="1.0.0",
        tags=["刑事辩护", "量刑", "取保候审", "无罪辩护"],
        input_schema={
            "type": "object",
            "properties": {
                "case_description": {"type": "string", "description": "案件描述"},
                "charge": {"type": "string", "description": "指控罪名"},
                "stage": {"type": "string", "enum": ["侦查阶段", "审查起诉", "一审", "二审", "再审"]},
            },
            "required": ["case_description"],
        },
        steps=[
            SkillStep(
                id="case_analysis",
                name="案情分析",
                type="llm",
                config={
                    "system_prompt": "你是刑事辩护律师。请基于事实和法律进行客观分析。注意：你的分析仅供参考，不构成正式辩护意见。",
                    "prompt": "请分析以下刑事案件：\n\n案情描述：{input.case_description}\n指控罪名：{input.charge}\n诉讼阶段：{input.stage}\n\n请分析：\n1. 案件基本事实\n2. 可能的辩护方向\n3. 量刑情节分析\n4. 程序性权利保障\n5. 法律建议",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="criminal_law_search",
                name="相关法条检索",
                type="tool",
                depends_on=["case_analysis"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "{input.case_description}",
                        "category": "criminal",
                        "page_size": 10,
                    },
                },
            ),
        ],
        estimated_time_seconds=40,
        difficulty="complex",
    )


def _enterprise_compliance_skill() -> SkillDefinition:
    """企业合规审查技能包"""
    return SkillDefinition(
        id="enterprise_compliance",
        name="企业合规审查技能包",
        description="企业合规风险评估、制度审查、整改建议",
        category="compliance",
        version="1.0.0",
        tags=["企业合规", "风险评估", "制度建设", "整改"],
        input_schema={
            "type": "object",
            "properties": {
                "industry": {"type": "string", "description": "所属行业"},
                "compliance_area": {"type": "string", "enum": ["数据隐私", "反垄断", "劳动用工", "环境保护", "反腐败", "安全生产", "全面合规"]},
                "description": {"type": "string", "description": "合规情况描述"},
            },
            "required": ["description"],
        },
        steps=[
            SkillStep(
                id="compliance_assessment",
                name="合规风险评估",
                type="llm",
                config={
                    "system_prompt": "你是企业合规专家。",
                    "prompt": "请对以下企业合规情况进行风险评估：\n\n行业：{input.industry}\n合规领域：{input.compliance_area}\n情况描述：{input.description}\n\n请评估：\n1. 主要合规风险点\n2. 风险等级\n3. 相关法律法规要求\n4. 整改建议\n5. 合规制度建设建议",
                    "temperature": 0.3,
                },
            ),
        ],
        estimated_time_seconds=30,
        difficulty="medium",
    )


def _litigation_preparation_skill() -> SkillDefinition:
    """诉讼准备技能包"""
    return SkillDefinition(
        id="litigation_preparation",
        name="诉讼准备技能包",
        description="诉讼全流程准备：管辖分析、诉状起草、证据整理、庭审提纲",
        category="litigation",
        version="1.0.0",
        tags=["诉讼", "起诉状", "证据", "庭审", "管辖"],
        input_schema={
            "type": "object",
            "properties": {
                "case_description": {"type": "string", "description": "案件描述"},
                "claim_amount": {"type": "number", "description": "诉讼标的额"},
                "plaintiff_info": {"type": "object", "description": "原告信息"},
                "defendant_info": {"type": "object", "description": "被告信息"},
            },
            "required": ["case_description"],
        },
        steps=[
            SkillStep(
                id="jurisdiction_analysis",
                name="管辖分析",
                type="llm",
                config={
                    "system_prompt": "你是诉讼程序专家。",
                    "prompt": "分析以下案件的管辖问题：\n\n案件描述：{input.case_description}\n标的额：{input.claim_amount}\n\n请分析：\n1. 管辖法院\n2. 管辖依据\n3. 是否存在管辖权异议风险",
                    "temperature": 0.2,
                },
            ),
            SkillStep(
                id="fee_calc",
                name="诉讼费用计算",
                type="tool",
                depends_on=[],
                config={
                    "tool": "legal_calculator",
                    "parameters": {
                        "calc_type": "litigation_fee",
                        "params": {"amount": "{input.claim_amount}"},
                    },
                },
            ),
            SkillStep(
                id="evidence_prep",
                name="证据整理",
                type="llm",
                depends_on=["jurisdiction_analysis"],
                config={
                    "system_prompt": "你是诉讼证据专家。",
                    "prompt": "基于以下案件信息，整理证据清单：\n\n案件描述：{input.case_description}\n管辖分析：{results.jurisdiction_analysis}\n\n请列出：\n1. 证据目录（编号、名称、证明目的）\n2. 证据收集建议\n3. 举证注意事项",
                    "temperature": 0.2,
                },
            ),
        ],
        estimated_time_seconds=45,
        difficulty="medium",
    )


def _debt_recovery_skill() -> SkillDefinition:
    """债务追讨技能包"""
    return SkillDefinition(
        id="debt_recovery",
        name="债务追讨技能包",
        description="债务追讨方案：催收函起草、诉讼时效分析、财产保全建议",
        category="general",
        version="1.0.0",
        tags=["债务追讨", "催收", "诉讼时效", "财产保全"],
        input_schema={
            "type": "object",
            "properties": {
                "debt_description": {"type": "string", "description": "债务情况描述"},
                "debt_amount": {"type": "number", "description": "债务金额"},
                "debt_date": {"type": "string", "description": "债务发生日期"},
                "debtor_info": {"type": "object", "description": "债务人信息"},
            },
            "required": ["debt_description"],
        },
        steps=[
            SkillStep(
                id="debt_analysis",
                name="债务分析",
                type="llm",
                config={
                    "system_prompt": "你是债务追讨法律专家。",
                    "prompt": "请分析以下债务情况：\n\n债务描述：{input.debt_description}\n金额：{input.debt_amount}\n发生日期：{input.debt_date}\n\n请分析：\n1. 债权有效性\n2. 诉讼时效状态\n3. 追讨方案\n4. 法律风险提示",
                    "temperature": 0.3,
                },
            ),
        ],
        estimated_time_seconds=30,
        difficulty="easy",
    )


# =============================================================================
# Comprehensive Legal Guide Skills (12 New Skills)
# =============================================================================


def _labor_dispute_guide_skill() -> SkillDefinition:
    """劳动争议处理指南 - Full workflow skill pack"""
    return SkillDefinition(
        id="labor_dispute_guide",
        name="劳动争议处理指南",
        description="完整的劳动争议处理全流程指南：从案件受理、法律检索、类案匹配、赔偿计算到文书生成与复核",
        category="labor",
        version="2.0.0",
        tags=["劳动争议", "劳动仲裁", "工资纠纷", "解雇赔偿", "工伤赔偿", "社保纠纷", "竞业限制"],
        input_schema={
            "type": "object",
            "properties": {
                "dispute_description": {"type": "string", "description": "劳动争议详细描述"},
                "employee_info": {
                    "type": "object",
                    "description": "劳动者信息",
                    "properties": {
                        "name": {"type": "string"},
                        "entry_date": {"type": "string", "description": "入职日期"},
                        "monthly_salary": {"type": "number", "description": "月工资"},
                        "work_years": {"type": "number", "description": "工作年限"},
                        "contract_type": {"type": "string", "description": "合同类型"},
                    },
                },
                "employer_info": {
                    "type": "object",
                    "description": "用人单位信息",
                    "properties": {
                        "company_name": {"type": "string"},
                        "industry": {"type": "string"},
                        "location": {"type": "string"},
                    },
                },
                "dispute_type": {
                    "type": "string",
                    "enum": ["工资纠纷", "违法解雇", "工伤赔偿", "社保纠纷", "竞业限制", "加班费纠纷", "未签合同双倍工资", "其他"],
                },
                "claim_amount": {"type": "number", "description": "诉求金额"},
            },
            "required": ["dispute_description"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "case_analysis": {"type": "object"},
                "legal_basis": {"type": "array"},
                "similar_cases": {"type": "array"},
                "compensation_detail": {"type": "object"},
                "evidence_list": {"type": "array"},
                "arbitration_application": {"type": "string"},
                "risk_assessment": {"type": "object"},
            },
        },
        steps=[
            SkillStep(
                id="lg_intake",
                name="案件受理与信息提取",
                type="llm",
                description="分析劳动争议案情，提取关键法律要素和时间节点",
                config={
                    "system_prompt": "你是一位经验丰富的劳动法律师，擅长从当事人描述中提取关键法律要素。请严格按照法律要素框架进行分析。",
                    "prompt": "请对以下劳动争议进行全面的案件受理分析：\n\n争议描述：{input.dispute_description}\n争议类型：{input.dispute_type}\n劳动者信息：{input.employee_info}\n用人单位信息：{input.employer_info}\n\n请提取并分析：\n1. 劳动关系存续期间及关键时间节点\n2. 争议焦点和法律关系定性\n3. 双方权利义务关系\n4. 仲裁时效分析（是否超过1年时效）\n5. 适用的法律法规清单\n6. 初步胜诉率评估",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="lg_law_search",
                name="法律法规检索",
                type="tool",
                description="检索劳动争议相关法律法规和司法解释",
                depends_on=["lg_intake"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "{input.dispute_description}",
                        "category": "social",
                        "page_size": 15,
                    },
                },
            ),
            SkillStep(
                id="lg_case_match",
                name="类案匹配与检索",
                type="tool",
                description="从裁判文书网检索同类案件",
                depends_on=["lg_intake"],
                config={
                    "tool": "wenshu_search",
                    "parameters": {
                        "keyword": "{input.dispute_description}",
                        "case_type": "民事",
                        "page_size": 10,
                    },
                },
            ),
            SkillStep(
                id="lg_compensation_calc",
                name="赔偿金精确计算",
                type="llm",
                description="根据案情计算各类赔偿金和补偿金",
                depends_on=["lg_intake", "lg_law_search"],
                config={
                    "system_prompt": "你是劳动法赔偿计算专家，精通各类经济补偿金、赔偿金的计算方法。请引用《劳动合同法》第47条、第87条等规定进行精确计算。",
                    "prompt": "基于以下案件信息，精确计算各项赔偿/补偿金额：\n\n案件分析：{results.lg_intake}\n法律依据：{results.lg_law_search}\n诉求金额：{input.claim_amount}\n\n请计算：\n1. 经济补偿金（N或N+1，依据工作年限和月工资）\n2. 违法解除赔偿金（2N）\n3. 未签劳动合同双倍工资差额\n4. 拖欠工资及25%经济补偿金\n5. 加班费（工作日1.5倍/休息日2倍/法定节3倍）\n6. 工伤待遇（如适用）\n7. 年终奖/未休年假工资\n8. 合计可主张金额\n\n请列出每项计算公式和法律依据。",
                    "temperature": 0.1,
                },
            ),
            SkillStep(
                id="lg_evidence_gen",
                name="证据清单与保全建议",
                type="llm",
                description="生成证据清单和证据保全建议",
                depends_on=["lg_intake", "lg_law_search", "lg_case_match"],
                config={
                    "system_prompt": "你是劳动争议证据整理专家，熟悉劳动仲裁的证据规则。",
                    "prompt": "基于以下案情和法律依据，生成完整的证据准备方案：\n\n案件分析：{results.lg_intake}\n法律依据：{results.lg_law_search}\n类案参考：{results.lg_case_match}\n\n请提供：\n1. 核心证据清单（编号、证据名称、证明目的、证据形式）\n2. 举证责任分配分析\n3. 证据收集途径和建议\n4. 电子证据保全建议\n5. 证人证言准备建议\n6. 用人单位可能提交的证据及应对策略",
                    "temperature": 0.2,
                },
            ),
            SkillStep(
                id="lg_document_draft",
                name="法律文书起草",
                type="agent",
                description="起草劳动仲裁申请书或相关法律文书",
                depends_on=["lg_intake", "lg_law_search", "lg_compensation_calc", "lg_evidence_gen"],
                config={
                    "agent": "document_gen",
                    "input": {
                        "document_type": "arbitration_application",
                        "case_info": "{results.lg_intake}",
                        "legal_basis": "{results.lg_law_search}",
                        "claims": "{results.lg_compensation_calc}",
                        "evidence": "{results.lg_evidence_gen}",
                    },
                },
            ),
            SkillStep(
                id="lg_review",
                name="文书复核与风险评估",
                type="agent",
                description="对生成的法律文书进行复核，评估诉讼风险",
                depends_on=["lg_document_draft"],
                config={
                    "agent": "contract_review",
                    "input": {
                        "document_text": "{results.lg_document_draft}",
                        "review_focus": "法律文书格式规范性、诉讼请求完整性、法律依据准确性、事实陈述一致性",
                    },
                },
            ),
            SkillStep(
                id="lg_final_report",
                name="综合处理报告",
                type="llm",
                description="生成完整的劳动争议处理指南报告",
                depends_on=["lg_intake", "lg_law_search", "lg_case_match", "lg_compensation_calc", "lg_evidence_gen", "lg_document_draft", "lg_review"],
                config={
                    "system_prompt": "你是资深劳动法律师，请综合所有分析结果，生成一份专业、完整的劳动争议处理指南报告。",
                    "prompt": "请综合以下全部分析结果，生成完整的劳动争议处理指南报告：\n\n案件受理分析：{results.lg_intake}\n法律依据：{results.lg_law_search}\n类案参考：{results.lg_case_match}\n赔偿计算：{results.lg_compensation_calc}\n证据清单：{results.lg_evidence_gen}\n法律文书：{results.lg_document_draft}\n复核结果：{results.lg_review}\n\n报告结构：\n一、案件概述与争议焦点\n二、法律关系分析\n三、适用法律法规\n四、类案裁判规则参考\n五、赔偿金额计算明细\n六、证据准备方案\n七、仲裁申请书\n八、风险评估与应对策略\n九、处理流程与时间节点\n十、律师建议",
                    "temperature": 0.3,
                    "max_tokens": 8192,
                },
            ),
        ],
        estimated_time_seconds=120,
        difficulty="complex",
    )


def _contract_dispute_guide_skill() -> SkillDefinition:
    """合同纠纷处理指南"""
    return SkillDefinition(
        id="contract_dispute_guide",
        name="合同纠纷处理指南",
        description="合同纠纷全流程处理：合同效力分析、违约认定、损失计算、救济方案、文书生成",
        category="contract",
        version="2.0.0",
        tags=["合同纠纷", "违约责任", "合同解除", "损害赔偿", "合同效力", "缔约过失"],
        input_schema={
            "type": "object",
            "properties": {
                "contract_text": {"type": "string", "description": "合同文本"},
                "dispute_description": {"type": "string", "description": "纠纷描述"},
                "contract_type": {"type": "string", "enum": ["买卖合同", "租赁合同", "借款合同", "承揽合同", "建设工程合同", "技术服务合同", "劳动合同", "合伙协议", "其他"]},
                "breach_party": {"type": "string", "enum": ["甲方违约", "乙方违约", "双方违约"], "description": "违约方"},
                "dispute_amount": {"type": "number", "description": "争议金额"},
            },
            "required": ["dispute_description"],
        },
        steps=[
            SkillStep(
                id="cd_contract_analysis",
                name="合同效力与条款分析",
                type="llm",
                description="分析合同效力状态和各条款的法律含义",
                config={
                    "system_prompt": "你是合同法专家，精通《民法典》合同编。请对合同的效力和各条款进行专业分析。",
                    "prompt": "请对以下合同纠纷进行全面分析：\n\n合同文本：{input.contract_text}\n纠纷描述：{input.dispute_description}\n合同类型：{input.contract_type}\n违约方：{input.breach_party}\n\n请分析：\n1. 合同效力状态（有效/无效/可撤销/效力待定）\n2. 合同核心条款解读\n3. 违约行为认定\n4. 违约责任的约定与实际\n5. 合同解除条件是否成就\n6. 管辖权条款分析\n7. 争议解决方式建议",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="cd_law_search",
                name="合同相关法律检索",
                type="tool",
                description="检索合同纠纷相关法律法规",
                depends_on=["cd_contract_analysis"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "{input.dispute_description}",
                        "category": "civil",
                        "page_size": 15,
                    },
                },
            ),
            SkillStep(
                id="cd_case_search",
                name="类案检索",
                type="tool",
                description="检索同类合同纠纷裁判案例",
                depends_on=["cd_contract_analysis"],
                config={
                    "tool": "wenshu_search",
                    "parameters": {
                        "keyword": "{input.dispute_description}",
                        "case_type": "民事",
                        "page_size": 8,
                    },
                },
            ),
            SkillStep(
                id="cd_breach_assessment",
                name="违约责任与损失评估",
                type="llm",
                description="评估违约责任和经济损失",
                depends_on=["cd_contract_analysis", "cd_law_search"],
                config={
                    "system_prompt": "你是合同损害赔偿计算专家。请根据《民法典》合同编相关规定评估违约责任和损失。",
                    "prompt": "基于以下分析，评估违约责任和经济损失：\n\n合同分析：{results.cd_contract_analysis}\n法律依据：{results.cd_law_search}\n争议金额：{input.dispute_amount}\n\n请评估：\n1. 违约行为类型及严重程度\n2. 违约金约定是否合理\n3. 实际损失计算（直接损失+可得利益损失）\n4. 减损规则适用分析\n5. 过失相抵分析\n6. 损害赔偿总额计算\n7. 是否存在不可抗力或情势变更",
                    "temperature": 0.2,
                },
            ),
            SkillStep(
                id="cd_remedy_plan",
                name="救济方案制定",
                type="llm",
                description="制定合同纠纷救济方案",
                depends_on=["cd_contract_analysis", "cd_breach_assessment"],
                config={
                    "system_prompt": "你是合同纠纷解决策略专家。",
                    "prompt": "基于以下分析，制定最优救济方案：\n\n合同分析：{results.cd_contract_analysis}\n损失评估：{results.cd_breach_assessment}\n\n请制定：\n1. 协商和解方案（底线与让步空间）\n2. 调解方案建议\n3. 仲裁/诉讼方案\n4. 财产保全建议\n5. 继续履行/解除合同的选择分析\n6. 各方案的时间成本和费用对比\n7. 执行风险评估",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="cd_document_draft",
                name="法律文书起草",
                type="agent",
                description="起草催告函、起诉状或答辩状",
                depends_on=["cd_contract_analysis", "cd_law_search", "cd_breach_assessment", "cd_remedy_plan"],
                config={
                    "agent": "document_gen",
                    "input": {
                        "document_type": "complaint",
                        "case_info": "{results.cd_contract_analysis}",
                        "legal_basis": "{results.cd_law_search}",
                        "claims": "{results.cd_breach_assessment}",
                        "strategy": "{results.cd_remedy_plan}",
                    },
                },
            ),
            SkillStep(
                id="cd_final_report",
                name="综合处理报告",
                type="llm",
                description="生成合同纠纷处理完整报告",
                depends_on=["cd_contract_analysis", "cd_law_search", "cd_case_search", "cd_breach_assessment", "cd_remedy_plan", "cd_document_draft"],
                config={
                    "system_prompt": "你是资深合同纠纷律师，请生成完整的合同纠纷处理指南。",
                    "prompt": "请综合以下分析生成合同纠纷处理完整报告：\n\n合同分析：{results.cd_contract_analysis}\n法律依据：{results.cd_law_search}\n类案参考：{results.cd_case_search}\n损失评估：{results.cd_breach_assessment}\n救济方案：{results.cd_remedy_plan}\n法律文书：{results.cd_document_draft}\n\n报告结构：\n一、合同基本情况\n二、合同效力分析\n三、违约行为认定\n四、损失计算明细\n五、类案裁判参考\n六、救济方案对比\n七、法律文书\n八、行动建议与时间表",
                    "temperature": 0.3,
                    "max_tokens": 8192,
                },
            ),
        ],
        estimated_time_seconds=120,
        difficulty="complex",
    )


def _marriage_family_guide_skill() -> SkillDefinition:
    """婚姻家事法律指南"""
    return SkillDefinition(
        id="marriage_family_guide",
        name="婚姻家事法律指南",
        description="婚姻家事法律全流程指南：离婚方案、财产分割、子女抚养、家暴保护、继承纠纷",
        category="family",
        version="1.0.0",
        tags=["离婚", "财产分割", "子女抚养", "家庭暴力", "继承", "婚前协议", "抚养费"],
        input_schema={
            "type": "object",
            "properties": {
                "case_type": {"type": "string", "enum": ["协议离婚", "诉讼离婚", "财产分割", "子女抚养", "家庭暴力", "继承纠纷", "婚前/婚内协议"], "description": "案件类型"},
                "case_description": {"type": "string", "description": "案情描述"},
                "marriage_duration": {"type": "string", "description": "婚姻存续期间"},
                "children_info": {"type": "array", "description": "子女信息"},
                "property_info": {"type": "object", "description": "财产信息"},
                "has_domestic_violence": {"type": "boolean", "description": "是否存在家庭暴力"},
            },
            "required": ["case_description"],
        },
        steps=[
            SkillStep(
                id="mf_intake",
                name="家事案件受理分析",
                type="llm",
                description="分析家事案件基本情况，识别核心法律问题",
                config={
                    "system_prompt": "你是婚姻家庭法律专家，请以温和专业的态度分析家事案件。注意保护当事人隐私和权益。",
                    "prompt": "请分析以下婚姻家事案件：\n\n案件类型：{input.case_type}\n案情描述：{input.case_description}\n婚姻存续期间：{input.marriage_duration}\n子女信息：{input.children_info}\n财产信息：{input.property_info}\n是否存在家暴：{input.has_domestic_violence}\n\n请分析：\n1. 案件性质和法律关系\n2. 核心争议焦点\n3. 适用的法律法规（《民法典》婚姻家庭编等）\n4. 当事人权益保护要点\n5. 是否需要紧急保护措施（如家暴情况下的人身安全保护令）\n6. 案件处理难度评估",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="mf_law_search",
                name="家事法律检索",
                type="tool",
                description="检索婚姻家庭相关法律法规",
                depends_on=["mf_intake"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "{input.case_description}",
                        "category": "civil",
                        "page_size": 10,
                    },
                },
            ),
            SkillStep(
                id="mf_case_search",
                name="家事类案检索",
                type="tool",
                description="检索同类家事案件裁判",
                depends_on=["mf_intake"],
                config={
                    "tool": "wenshu_search",
                    "parameters": {
                        "keyword": "{input.case_description}",
                        "case_type": "民事",
                        "page_size": 8,
                    },
                },
            ),
            SkillStep(
                id="mf_property_analysis",
                name="财产分割方案",
                type="llm",
                description="分析夫妻共同财产和个人财产，制定分割方案",
                depends_on=["mf_intake", "mf_law_search"],
                config={
                    "system_prompt": "你是婚姻财产分割专家，精通《民法典》关于夫妻共同财产和个人财产的界定规则。",
                    "prompt": "基于以下信息，分析财产分割方案：\n\n案件分析：{results.mf_intake}\n法律依据：{results.mf_law_search}\n财产信息：{input.property_info}\n\n请分析：\n1. 夫妻共同财产范围认定\n2. 个人财产范围认定\n3. 房产分割方案（婚前/婚后购买的不同处理）\n4. 车辆、存款、投资等分割\n5. 公司股权/合伙企业份额处理\n6. 债务分担方案\n7. 家务劳动补偿分析\n8. 离婚损害赔偿（如有过错方）\n9. 经济帮助（如一方生活困难）",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="mf_custody_plan",
                name="子女抚养方案",
                type="condition",
                description="制定子女抚养权归属和抚养费方案",
                depends_on=["mf_intake"],
                config={
                    "condition": "{input.children_info}",
                },
            ),
            SkillStep(
                id="mf_custody_detail",
                name="抚养权详细分析",
                type="llm",
                description="详细分析子女抚养权问题",
                depends_on=["mf_custody_plan"],
                config={
                    "system_prompt": "你是子女抚养权问题专家，始终将子女最佳利益放在首位。",
                    "prompt": "基于以下信息，分析子女抚养权问题：\n\n案件分析：{results.mf_intake}\n子女信息：{input.children_info}\n\n请分析：\n1. 抚养权归属建议（考虑子女年龄、双方条件）\n2. 抚养费计算标准（月收入的20%-30%）\n3. 探望权方案建议\n4. 抚养费变更情形\n5. 子女姓氏等争议处理\n6. 2岁以上8岁以下子女的意愿考量",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="mf_document_draft",
                name="家事法律文书起草",
                type="agent",
                description="起草离婚协议书或起诉状",
                depends_on=["mf_intake", "mf_law_search", "mf_property_analysis", "mf_custody_detail"],
                config={
                    "agent": "document_gen",
                    "input": {
                        "document_type": "divorce_agreement_or_complaint",
                        "case_info": "{results.mf_intake}",
                        "legal_basis": "{results.mf_law_search}",
                        "property_plan": "{results.mf_property_analysis}",
                        "custody_plan": "{results.mf_custody_detail}",
                    },
                },
            ),
            SkillStep(
                id="mf_final_report",
                name="家事处理综合报告",
                type="llm",
                description="生成婚姻家事案件处理完整报告",
                depends_on=["mf_intake", "mf_law_search", "mf_case_search", "mf_property_analysis", "mf_custody_detail", "mf_document_draft"],
                config={
                    "system_prompt": "你是资深婚姻家事律师，请生成完整处理报告。",
                    "prompt": "请综合以下分析生成婚姻家事案件处理报告：\n\n案件分析：{results.mf_intake}\n法律依据：{results.mf_law_search}\n类案参考：{results.mf_case_search}\n财产分割：{results.mf_property_analysis}\n子女抚养：{results.mf_custody_detail}\n法律文书：{results.mf_document_draft}\n\n报告结构：\n一、案件概述\n二、法律分析\n三、财产分割方案\n四、子女抚养方案\n五、类案参考\n六、法律文书\n七、处理流程建议\n八、注意事项与风险提示",
                    "temperature": 0.3,
                    "max_tokens": 8192,
                },
            ),
        ],
        estimated_time_seconds=120,
        difficulty="complex",
    )


def _ip_protection_guide_skill() -> SkillDefinition:
    """知识产权保护指南"""
    return SkillDefinition(
        id="ip_protection_guide",
        name="知识产权保护指南",
        description="知识产权全链条保护：权利确认、侵权分析、证据保全、维权方案、赔偿计算",
        category="ip",
        version="2.0.0",
        tags=["知识产权", "专利侵权", "商标侵权", "著作权", "商业秘密", "不正当竞争", "维权"],
        input_schema={
            "type": "object",
            "properties": {
                "ip_type": {"type": "string", "enum": ["发明专利", "实用新型专利", "外观设计专利", "商标", "著作权", "软件著作权", "商业秘密", "集成电路布图设计"], "description": "知识产权类型"},
                "rights_description": {"type": "string", "description": "权利状况描述"},
                "infringement_description": {"type": "string", "description": "侵权行为描述"},
                "ip_registration_info": {"type": "object", "description": "知识产权登记信息"},
                "estimated_loss": {"type": "number", "description": "预估损失金额"},
            },
            "required": ["ip_type", "rights_description", "infringement_description"],
        },
        steps=[
            SkillStep(
                id="ip_rights_analysis",
                name="权利状态分析",
                type="llm",
                description="分析知识产权权利状态和保护范围",
                config={
                    "system_prompt": "你是知识产权法专家，精通各类知识产权的权利界定和保护范围分析。",
                    "prompt": "请分析以下知识产权的权利状态：\n\n类型：{input.ip_type}\n权利描述：{input.rights_description}\n登记信息：{input.ip_registration_info}\n\n请分析：\n1. 权利有效性确认\n2. 保护范围界定\n3. 权利期限和维持状态\n4. 权利归属分析\n5. 可能的权利瑕疵或限制\n6. 相关司法解释适用",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="ip_infringement_analysis",
                name="侵权行为分析",
                type="llm",
                description="分析侵权行为的构成要件",
                depends_on=["ip_rights_analysis"],
                config={
                    "system_prompt": "你是知识产权侵权分析专家，精通各类知识产权侵权判定规则。",
                    "prompt": "请分析以下知识产权侵权情况：\n\n权利分析：{results.ip_rights_analysis}\n侵权描述：{input.infringement_description}\n\n请分析：\n1. 侵权行为类型（直接侵权/间接侵权）\n2. 侵权构成要件分析\n3. 等同原则适用分析（专利案件）\n4. 现有技术/在先使用抗辩可能性\n5. 合法来源抗辩可能性\n6. 侵权持续时间和影响范围\n7. 初步侵权结论",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="ip_law_search",
                name="知识产权法律检索",
                type="tool",
                description="检索知识产权相关法律法规",
                depends_on=["ip_rights_analysis"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "{input.ip_type} 侵权",
                        "page_size": 15,
                    },
                },
            ),
            SkillStep(
                id="ip_case_search",
                name="知识产权类案检索",
                type="tool",
                description="检索同类知识产权案件",
                depends_on=["ip_infringement_analysis"],
                config={
                    "tool": "wenshu_search",
                    "parameters": {
                        "keyword": "{input.infringement_description}",
                        "case_type": "民事",
                        "page_size": 8,
                    },
                },
            ),
            SkillStep(
                id="ip_evidence_preservation",
                name="证据保全方案",
                type="llm",
                description="制定证据保全和公证方案",
                depends_on=["ip_infringement_analysis", "ip_law_search"],
                config={
                    "system_prompt": "你是知识产权维权证据专家。",
                    "prompt": "基于以下分析，制定证据保全方案：\n\n权利分析：{results.ip_rights_analysis}\n侵权分析：{results.ip_infringement_analysis}\n法律依据：{results.ip_law_search}\n\n请制定：\n1. 侵权证据收集方案\n2. 公证保全建议（网页公证、购买公证等）\n3. 证据保全申请建议\n4. 电子证据固定方案\n5. 损失证据收集\n6. 侵权获利证据收集\n7. 诉前禁令申请条件分析",
                    "temperature": 0.2,
                },
            ),
            SkillStep(
                id="ip_damages_calc",
                name="损害赔偿计算",
                type="llm",
                description="计算知识产权侵权损害赔偿",
                depends_on=["ip_infringement_analysis", "ip_evidence_preservation"],
                config={
                    "system_prompt": "你是知识产权损害赔偿计算专家。",
                    "prompt": "基于以下信息，计算侵权损害赔偿：\n\n侵权分析：{results.ip_infringement_analysis}\n证据方案：{results.ip_evidence_preservation}\n预估损失：{input.estimated_loss}\n\n请计算：\n1. 权利人实际损失\n2. 侵权人违法所得\n3. 许可费倍数的合理倍数\n4. 法定赔偿（专利1-500万/商标500万以下/著作权500万以下）\n5. 惩罚性赔偿适用条件分析（故意侵权）\n6. 合理开支（律师费、公证费等）\n7. 赔偿总额建议",
                    "temperature": 0.2,
                },
            ),
            SkillStep(
                id="ip_enforcement_plan",
                name="维权方案制定",
                type="llm",
                description="制定知识产权维权策略",
                depends_on=["ip_infringement_analysis", "ip_damages_calc"],
                config={
                    "system_prompt": "你是知识产权维权策略专家。",
                    "prompt": "基于以下分析，制定最优维权方案：\n\n侵权分析：{results.ip_infringement_analysis}\n赔偿计算：{results.ip_damages_calc}\n\n请制定：\n1. 行政投诉方案（市场监管局/版权局）\n2. 民事维权方案（诉前警告→调解→诉讼）\n3. 刑事报案方案（如构成犯罪）\n4. 电商平台投诉方案\n5. 海关备案与扣货\n6. 各方案的成本效益分析\n7. 维权时间规划",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="ip_final_report",
                name="知识产权保护综合报告",
                type="llm",
                description="生成知识产权保护完整报告",
                depends_on=["ip_rights_analysis", "ip_infringement_analysis", "ip_law_search", "ip_case_search", "ip_evidence_preservation", "ip_damages_calc", "ip_enforcement_plan"],
                config={
                    "system_prompt": "你是资深知识产权律师，请生成完整的知识产权保护报告。",
                    "prompt": "请综合以下分析生成知识产权保护完整报告：\n\n权利分析：{results.ip_rights_analysis}\n侵权分析：{results.ip_infringement_analysis}\n法律依据：{results.ip_law_search}\n类案参考：{results.ip_case_search}\n证据方案：{results.ip_evidence_preservation}\n赔偿计算：{results.ip_damages_calc}\n维权方案：{results.ip_enforcement_plan}\n\n报告结构：\n一、知识产权权利状态\n二、侵权行为分析\n三、法律依据\n四、类案裁判参考\n五、证据保全方案\n六、损害赔偿计算\n七、维权行动方案\n八、风险与注意事项",
                    "temperature": 0.3,
                    "max_tokens": 8192,
                },
            ),
        ],
        estimated_time_seconds=150,
        difficulty="complex",
    )


def _corporate_compliance_guide_skill() -> SkillDefinition:
    """企业合规审查指南"""
    return SkillDefinition(
        id="corporate_compliance_guide",
        name="企业合规审查指南",
        description="企业合规全领域审查：数据隐私、反腐败、反垄断、劳动用工、环保、安全生产合规",
        category="compliance",
        version="2.0.0",
        tags=["企业合规", "数据隐私", "反垄断", "反腐败", "劳动合规", "环保合规", "安全生产"],
        input_schema={
            "type": "object",
            "properties": {
                "industry": {"type": "string", "description": "所属行业"},
                "company_size": {"type": "string", "enum": ["小微企业", "中型企业", "大型企业", "上市公司"], "description": "企业规模"},
                "compliance_areas": {"type": "array", "items": {"type": "string", "enum": ["数据隐私", "反垄断", "反腐败", "劳动用工", "环境保护", "安全生产", "税务合规", "外贸合规", "知识产权", "全面合规"]}, "description": "合规领域"},
                "compliance_description": {"type": "string", "description": "合规情况描述"},
                "existing_issues": {"type": "array", "description": "已发现的合规问题"},
            },
            "required": ["compliance_description"],
        },
        steps=[
            SkillStep(
                id="cc_risk_assessment",
                name="合规风险全面评估",
                type="llm",
                description="对企业合规风险进行全面评估",
                config={
                    "system_prompt": "你是企业合规风险评估专家，精通多个合规领域的风险识别和评估。",
                    "prompt": "请对以下企业进行全面的合规风险评估：\n\n行业：{input.industry}\n企业规模：{input.company_size}\n合规领域：{input.compliance_areas}\n合规情况：{input.compliance_description}\n已发现问题：{input.existing_issues}\n\n请评估：\n1. 各行业特定合规要求\n2. 高风险合规领域识别\n3. 各合规领域风险等级（高/中/低）\n4. 监管处罚可能性分析\n5. 合规差距分析\n6. 关键合规义务清单",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="cc_law_search",
                name="合规法律检索",
                type="tool",
                description="检索相关合规法律法规",
                depends_on=["cc_risk_assessment"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "{input.compliance_description}",
                        "page_size": 15,
                    },
                },
            ),
            SkillStep(
                id="cc_data_privacy",
                name="数据隐私合规分析",
                type="condition",
                description="数据隐私专项合规分析",
                depends_on=["cc_risk_assessment"],
                config={
                    "condition": "data_privacy",
                },
            ),
            SkillStep(
                id="cc_data_privacy_detail",
                name="数据隐私详细分析",
                type="llm",
                description="数据隐私保护合规详细分析",
                depends_on=["cc_data_privacy"],
                config={
                    "system_prompt": "你是数据隐私保护合规专家，精通《个人信息保护法》《数据安全法》《网络安全法》。",
                    "prompt": "基于以下信息，进行数据隐私合规详细分析：\n\n风险评估：{results.cc_risk_assessment}\n法律依据：{results.cc_law_search}\n\n请分析：\n1. 个人信息收集合法性基础\n2. 隐私政策合规性审查\n3. 数据处理活动合规评估\n4. 跨境数据传输合规\n5. 数据安全措施评估\n6. 用户权利保障机制\n7. 数据泄露应急预案\n8. 合规整改建议",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="cc_anti_corruption",
                name="反腐败合规分析",
                type="llm",
                description="反腐败合规专项分析",
                depends_on=["cc_risk_assessment", "cc_law_search"],
                config={
                    "system_prompt": "你是反腐败合规专家。",
                    "prompt": "基于以下信息，分析反腐败合规情况：\n\n风险评估：{results.cc_risk_assessment}\n法律依据：{results.cc_law_search}\n\n请分析：\n1. 商业贿赂风险点识别\n2. 礼品招待政策合规\n3. 第三方尽职调查建议\n4. 反腐败制度建设\n5. 员工培训建议\n6. 举报机制建设\n7. 合规审计建议",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="cc_labor_compliance",
                name="劳动用工合规分析",
                type="llm",
                description="劳动用工合规专项分析",
                depends_on=["cc_risk_assessment", "cc_law_search"],
                config={
                    "system_prompt": "你是劳动用工合规专家。",
                    "prompt": "基于以下信息，分析劳动用工合规情况：\n\n风险评估：{results.cc_risk_assessment}\n法律依据：{results.cc_law_search}\n\n请分析：\n1. 劳动合同签订合规\n2. 社保公积金缴纳合规\n3. 工时制度合规\n4. 加班管理合规\n5. 劳务派遣和外包合规\n6. 女职工和未成年工保护\n7. 职业健康安全\n8. 规章制度合规审查",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="cc_rectification_plan",
                name="合规整改方案",
                type="llm",
                description="制定合规整改方案和实施计划",
                depends_on=["cc_risk_assessment", "cc_law_search", "cc_data_privacy_detail", "cc_anti_corruption", "cc_labor_compliance"],
                config={
                    "system_prompt": "你是企业合规整改专家。",
                    "prompt": "基于以下合规分析，制定整改方案：\n\n风险评估：{results.cc_risk_assessment}\n法律依据：{results.cc_law_search}\n数据隐私：{results.cc_data_privacy_detail}\n反腐败：{results.cc_anti_corruption}\n劳动用工：{results.cc_labor_compliance}\n\n请制定：\n1. 紧急整改事项（高风险立即处理）\n2. 短期整改计划（1-3个月）\n3. 中期整改计划（3-6个月）\n4. 长期合规制度建设\n5. 合规组织架构建议\n6. 合规培训计划\n7. 合规考核机制\n8. 整改时间表和责任人",
                    "temperature": 0.3,
                    "max_tokens": 8192,
                },
            ),
            SkillStep(
                id="cc_final_report",
                name="合规审查综合报告",
                type="llm",
                description="生成企业合规审查完整报告",
                depends_on=["cc_risk_assessment", "cc_law_search", "cc_data_privacy_detail", "cc_anti_corruption", "cc_labor_compliance", "cc_rectification_plan"],
                config={
                    "system_prompt": "你是资深企业合规律师，请生成完整的合规审查报告。",
                    "prompt": "请综合以下分析生成企业合规审查报告：\n\n风险评估：{results.cc_risk_assessment}\n法律依据：{results.cc_law_search}\n数据隐私：{results.cc_data_privacy_detail}\n反腐败：{results.cc_anti_corruption}\n劳动用工：{results.cc_labor_compliance}\n整改方案：{results.cc_rectification_plan}\n\n报告结构：\n一、企业基本情况\n二、合规风险评估总览\n三、各合规领域详细分析\n四、法律法规要求\n五、合规差距分析\n六、整改方案与时间表\n七、合规制度建设建议\n八、风险提示与注意事项",
                    "temperature": 0.3,
                    "max_tokens": 8192,
                },
            ),
        ],
        estimated_time_seconds=150,
        difficulty="complex",
    )


def _debt_credit_guide_skill() -> SkillDefinition:
    """债权债务处理指南"""
    return SkillDefinition(
        id="debt_credit_guide",
        name="债权债务处理指南",
        description="债权债务全流程处理：债权确认、催收方案、诉讼时效、财产保全、执行方案",
        category="debt",
        version="1.0.0",
        tags=["债权债务", "借款", "欠款", "催收", "诉讼时效", "财产保全", "执行"],
        input_schema={
            "type": "object",
            "properties": {
                "debt_type": {"type": "string", "enum": ["借款", "货款", "工程款", "租金", "劳动报酬", "侵权赔偿", "其他"], "description": "债务类型"},
                "debt_description": {"type": "string", "description": "债务情况描述"},
                "debt_amount": {"type": "number", "description": "债务金额"},
                "debt_date": {"type": "string", "description": "债务发生日期"},
                "due_date": {"type": "string", "description": "到期日"},
                "debtor_info": {"type": "object", "description": "债务人信息"},
                "has_guarantor": {"type": "boolean", "description": "是否有担保人"},
                "has_collateral": {"type": "boolean", "description": "是否有抵押/质押"},
            },
            "required": ["debt_description", "debt_amount"],
        },
        steps=[
            SkillStep(
                id="dc_debt_analysis",
                name="债权有效性分析",
                type="llm",
                description="分析债权有效性和法律状态",
                config={
                    "system_prompt": "你是债权债务法律专家，精通《民法典》合同编关于债权债务的规定。",
                    "prompt": "请分析以下债权债务情况：\n\n债务类型：{input.debt_type}\n债务描述：{input.debt_description}\n金额：{input.debt_amount}\n发生日期：{input.debt_date}\n到期日：{input.due_date}\n担保人：{input.has_guarantor}\n抵押/质押：{input.has_collateral}\n\n请分析：\n1. 债权有效性确认\n2. 诉讼时效状态（3年时效分析）\n3. 时效中断/中止事由分析\n4. 担保效力分析\n5. 抵押/质押效力分析\n6. 债务人可能的抗辩事由\n7. 债权实现的可能性评估",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="dc_law_search",
                name="债权债务法律检索",
                type="tool",
                description="检索债权债务相关法律法规",
                depends_on=["dc_debt_analysis"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "{input.debt_description}",
                        "category": "civil",
                        "page_size": 10,
                    },
                },
            ),
            SkillStep(
                id="dc_collection_plan",
                name="催收方案设计",
                type="llm",
                description="设计阶梯式催收方案",
                depends_on=["dc_debt_analysis", "dc_law_search"],
                config={
                    "system_prompt": "你是债务催收法律专家。",
                    "prompt": "基于以下信息，设计催收方案：\n\n债权分析：{results.dc_debt_analysis}\n法律依据：{results.dc_law_search}\n\n请设计：\n1. 友好协商阶段方案\n2. 正式催收函阶段\n3. 律师函催收阶段\n4. 调解方案\n5. 支付令申请\n6. 诉讼方案\n7. 各阶段时间节点\n8. 催收注意事项（合法催收边界）",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="dc_asset_investigation",
                name="财产线索分析",
                type="llm",
                description="分析债务人财产线索和保全方案",
                depends_on=["dc_debt_analysis"],
                config={
                    "system_prompt": "你是财产调查和保全专家。",
                    "prompt": "基于以下信息，分析债务人的财产线索：\n\n债权分析：{results.dc_debt_analysis}\n债务人信息：{input.debtor_info}\n\n请分析：\n1. 可能的财产线索（房产、车辆、银行存款、股权等）\n2. 财产调查途径\n3. 诉前财产保全方案\n4. 诉中财产保全方案\n5. 保全担保要求\n6. 保全错误风险提示\n7. 执行阶段财产调查建议",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="dc_litigation_plan",
                name="诉讼/执行方案",
                type="llm",
                description="制定诉讼或执行方案",
                depends_on=["dc_debt_analysis", "dc_collection_plan", "dc_asset_investigation"],
                config={
                    "system_prompt": "你是债权债务诉讼专家。",
                    "prompt": "基于以下信息，制定诉讼/执行方案：\n\n债权分析：{results.dc_debt_analysis}\n催收方案：{results.dc_collection_plan}\n财产线索：{results.dc_asset_investigation}\n\n请制定：\n1. 管辖法院确定\n2. 诉讼请求设计\n3. 证据清单\n4. 诉讼费用计算\n5. 财产保全申请\n6. 判决后的执行方案\n7. 执行不能的风险应对\n8. 债务重组/和解方案",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="dc_document_draft",
                name="法律文书起草",
                type="agent",
                description="起草催收函、律师函或起诉状",
                depends_on=["dc_debt_analysis", "dc_law_search", "dc_litigation_plan"],
                config={
                    "agent": "document_gen",
                    "input": {
                        "document_type": "demand_letter_or_complaint",
                        "case_info": "{results.dc_debt_analysis}",
                        "legal_basis": "{results.dc_law_search}",
                        "strategy": "{results.dc_litigation_plan}",
                    },
                },
            ),
            SkillStep(
                id="dc_final_report",
                name="债权债务处理综合报告",
                type="llm",
                description="生成债权债务处理完整报告",
                depends_on=["dc_debt_analysis", "dc_law_search", "dc_collection_plan", "dc_asset_investigation", "dc_litigation_plan", "dc_document_draft"],
                config={
                    "system_prompt": "你是资深债权债务法律专家，请生成完整的处理报告。",
                    "prompt": "请综合以下分析生成债权债务处理完整报告：\n\n债权分析：{results.dc_debt_analysis}\n法律依据：{results.dc_law_search}\n催收方案：{results.dc_collection_plan}\n财产线索：{results.dc_asset_investigation}\n诉讼方案：{results.dc_litigation_plan}\n法律文书：{results.dc_document_draft}\n\n报告结构：\n一、债权基本情况\n二、债权有效性分析\n三、诉讼时效分析\n四、催收方案\n五、财产线索与保全\n六、诉讼方案\n七、法律文书\n八、风险提示与建议",
                    "temperature": 0.3,
                    "max_tokens": 6144,
                },
            ),
        ],
        estimated_time_seconds=100,
        difficulty="medium",
    )


def _real_estate_dispute_guide_skill() -> SkillDefinition:
    """房产纠纷处理指南"""
    return SkillDefinition(
        id="real_estate_dispute_guide",
        name="房产纠纷处理指南",
        description="房产纠纷全流程处理：买卖纠纷、租赁纠纷、物业纠纷、拆迁纠纷、权属争议",
        category="real_estate",
        version="1.0.0",
        tags=["房产纠纷", "房屋买卖", "房屋租赁", "物业纠纷", "拆迁安置", "权属争议", "装修纠纷"],
        input_schema={
            "type": "object",
            "properties": {
                "dispute_type": {"type": "string", "enum": ["房屋买卖", "房屋租赁", "物业纠纷", "拆迁安置", "权属争议", "装修纠纷", "相邻关系", "房产继承", "其他"], "description": "纠纷类型"},
                "dispute_description": {"type": "string", "description": "纠纷描述"},
                "property_info": {"type": "object", "description": "房产信息"},
                "dispute_amount": {"type": "number", "description": "争议金额"},
                "party_role": {"type": "string", "enum": ["买方/承租方", "卖方/出租方", "业主", "物业公司", "其他"], "description": "当事人角色"},
            },
            "required": ["dispute_type", "dispute_description"],
        },
        steps=[
            SkillStep(
                id="re_intake",
                name="房产纠纷受理分析",
                type="llm",
                description="分析房产纠纷基本情况",
                config={
                    "system_prompt": "你是房地产法律专家，精通《民法典》物权编、合同编及相关司法解释。",
                    "prompt": "请分析以下房产纠纷：\n\n纠纷类型：{input.dispute_type}\n纠纷描述：{input.dispute_description}\n房产信息：{input.property_info}\n争议金额：{input.dispute_amount}\n当事人角色：{input.party_role}\n\n请分析：\n1. 纠纷性质和法律关系\n2. 核心争议焦点\n3. 当事人权利义务\n4. 适用的法律法规\n5. 管辖法院分析（不动产专属管辖）\n6. 初步处理建议",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="re_law_search",
                name="房产法律检索",
                type="tool",
                description="检索房产相关法律法规",
                depends_on=["re_intake"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "{input.dispute_description}",
                        "category": "civil",
                        "page_size": 10,
                    },
                },
            ),
            SkillStep(
                id="re_case_search",
                name="房产类案检索",
                type="tool",
                description="检索同类房产纠纷案例",
                depends_on=["re_intake"],
                config={
                    "tool": "wenshu_search",
                    "parameters": {
                        "keyword": "{input.dispute_description}",
                        "case_type": "民事",
                        "page_size": 8,
                    },
                },
            ),
            SkillStep(
                id="re_rights_analysis",
                name="权利状态分析",
                type="llm",
                description="分析房产权利状态",
                depends_on=["re_intake", "re_law_search"],
                config={
                    "system_prompt": "你是房产权利分析专家。",
                    "prompt": "基于以下信息，分析房产权利状态：\n\n纠纷分析：{results.re_intake}\n法律依据：{results.re_law_search}\n\n请分析：\n1. 房屋权属状况\n2. 产权登记状态\n3. 是否存在共有权人\n4. 是否存在抵押、查封\n5. 是否存在优先购买权\n6. 权利瑕疵分析\n7. 善意取得适用分析（如适用）",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="re_remedy_plan",
                name="救济方案制定",
                type="llm",
                description="制定纠纷救济方案",
                depends_on=["re_intake", "re_rights_analysis"],
                config={
                    "system_prompt": "你是房产纠纷解决专家。",
                    "prompt": "基于以下信息，制定纠纷救济方案：\n\n纠纷分析：{results.re_intake}\n权利分析：{results.re_rights_analysis}\n\n请制定：\n1. 协商和解方案\n2. 行政投诉方案（住建局等）\n3. 仲裁/诉讼方案\n4. 具体诉讼请求设计\n5. 损失计算\n6. 证据清单\n7. 房产保全建议\n8. 执行可行性分析",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="re_document_draft",
                name="法律文书起草",
                type="agent",
                description="起草房产纠纷相关法律文书",
                depends_on=["re_intake", "re_law_search", "re_remedy_plan"],
                config={
                    "agent": "document_gen",
                    "input": {
                        "document_type": "complaint",
                        "case_info": "{results.re_intake}",
                        "legal_basis": "{results.re_law_search}",
                        "strategy": "{results.re_remedy_plan}",
                    },
                },
            ),
            SkillStep(
                id="re_final_report",
                name="房产纠纷处理报告",
                type="llm",
                description="生成房产纠纷处理完整报告",
                depends_on=["re_intake", "re_law_search", "re_case_search", "re_rights_analysis", "re_remedy_plan", "re_document_draft"],
                config={
                    "system_prompt": "你是资深房产律师，请生成完整的处理报告。",
                    "prompt": "请综合以下分析生成房产纠纷处理报告：\n\n纠纷分析：{results.re_intake}\n法律依据：{results.re_law_search}\n类案参考：{results.re_case_search}\n权利分析：{results.re_rights_analysis}\n救济方案：{results.re_remedy_plan}\n法律文书：{results.re_document_draft}\n\n报告结构：\n一、纠纷基本情况\n二、权利状态分析\n三、法律依据\n四、类案裁判参考\n五、救济方案\n六、法律文书\n七、证据清单\n八、风险提示与建议",
                    "temperature": 0.3,
                    "max_tokens": 6144,
                },
            ),
        ],
        estimated_time_seconds=100,
        difficulty="medium",
    )


def _traffic_accident_guide_skill() -> SkillDefinition:
    """交通事故理赔指南"""
    return SkillDefinition(
        id="traffic_accident_guide",
        name="交通事故理赔指南",
        description="交通事故理赔全流程：责任认定、伤残鉴定、保险理赔、赔偿计算、诉讼方案",
        category="accident",
        version="1.0.0",
        tags=["交通事故", "责任认定", "伤残鉴定", "保险理赔", "人身损害", "赔偿计算"],
        input_schema={
            "type": "object",
            "properties": {
                "accident_description": {"type": "string", "description": "事故描述"},
                "accident_date": {"type": "string", "description": "事故发生日期"},
                "accident_location": {"type": "string", "description": "事故地点"},
                "injury_severity": {"type": "string", "enum": ["轻微伤", "轻伤", "重伤", "死亡"], "description": "伤情程度"},
                "liability_determination": {"type": "string", "enum": ["全部责任", "主要责任", "同等责任", "次要责任", "无责任", "待认定"], "description": "责任认定"},
                "vehicle_info": {"type": "object", "description": "车辆信息"},
                "insurance_info": {"type": "object", "description": "保险信息"},
                "medical_expenses": {"type": "number", "description": "已发生的医疗费"},
                "victim_info": {"type": "object", "description": "受害人信息"},
            },
            "required": ["accident_description"],
        },
        steps=[
            SkillStep(
                id="ta_intake",
                name="事故情况分析",
                type="llm",
                description="分析交通事故基本情况",
                config={
                    "system_prompt": "你是交通事故理赔专家，精通《道路交通安全法》及人身损害赔偿相关规定。",
                    "prompt": "请分析以下交通事故：\n\n事故描述：{input.accident_description}\n事故日期：{input.accident_date}\n事故地点：{input.accident_location}\n伤情程度：{input.injury_severity}\n责任认定：{input.liability_determination}\n车辆信息：{input.vehicle_info}\n保险信息：{input.insurance_info}\n受害人信息：{input.victim_info}\n\n请分析：\n1. 事故责任认定分析\n2. 损害赔偿法律关系\n3. 赔偿主体分析（车主、驾驶人、保险公司）\n4. 保险理赔顺序\n5. 适用的法律法规\n6. 初步处理建议",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="ta_law_search",
                name="交通事故法律检索",
                type="tool",
                description="检索交通事故相关法律",
                depends_on=["ta_intake"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "{input.accident_description}",
                        "page_size": 10,
                    },
                },
            ),
            SkillStep(
                id="ta_injury_assessment",
                name="伤残鉴定指导",
                type="llm",
                description="伤残等级鉴定指导",
                depends_on=["ta_intake"],
                config={
                    "system_prompt": "你是人身损害伤残鉴定专家。",
                    "prompt": "基于以下信息，分析伤残鉴定事宜：\n\n事故分析：{results.ta_intake}\n伤情程度：{input.injury_severity}\n\n请分析：\n1. 伤残鉴定时机建议\n2. 鉴定机构选择建议\n3. 可能的伤残等级预估\n4. 鉴定材料准备清单\n5. 鉴定注意事项\n6. 重新鉴定的条件\n7. 鉴定费用承担",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="ta_compensation_calc",
                name="赔偿金精确计算",
                type="llm",
                description="计算各项人身损害赔偿金",
                depends_on=["ta_intake", "ta_law_search", "ta_injury_assessment"],
                config={
                    "system_prompt": "你是人身损害赔偿计算专家，精通《民法典》侵权责任编和人身损害赔偿司法解释。",
                    "prompt": "基于以下信息，精确计算各项赔偿金：\n\n事故分析：{results.ta_intake}\n法律依据：{results.ta_law_search}\n伤残分析：{results.ta_injury_assessment}\n医疗费：{input.medical_expenses}\n\n请计算：\n1. 医疗费（含后续治疗费）\n2. 误工费（收入x误工时间）\n3. 护理费\n4. 交通费\n5. 住院伙食补助费\n6. 营养费\n7. 残疾赔偿金（城镇/农村居民标准）\n8. 残疾辅助器具费\n9. 被扶养人生活费\n10. 精神损害抚慰金\n11. 财产损失\n12. 合计金额\n\n请按当地标准计算，注明计算依据。",
                    "temperature": 0.2,
                },
            ),
            SkillStep(
                id="ta_insurance_claim",
                name="保险理赔方案",
                type="llm",
                description="制定保险理赔方案",
                depends_on=["ta_intake", "ta_compensation_calc"],
                config={
                    "system_prompt": "你是交通保险理赔专家。",
                    "prompt": "基于以下信息，制定保险理赔方案：\n\n事故分析：{results.ta_intake}\n赔偿计算：{results.ta_compensation_calc}\n保险信息：{input.insurance_info}\n\n请制定：\n1. 交强险理赔方案\n2. 商业三者险理赔方案\n3. 车损险理赔方案（如适用）\n4. 理赔顺序和比例\n5. 保险拒赔风险及应对\n6. 理赔材料清单\n7. 理赔时限要求\n8. 保险诉讼策略",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="ta_document_draft",
                name="理赔文书起草",
                type="agent",
                description="起草理赔申请书或起诉状",
                depends_on=["ta_intake", "ta_law_search", "ta_compensation_calc", "ta_insurance_claim"],
                config={
                    "agent": "document_gen",
                    "input": {
                        "document_type": "insurance_claim_or_complaint",
                        "case_info": "{results.ta_intake}",
                        "legal_basis": "{results.ta_law_search}",
                        "claims": "{results.ta_compensation_calc}",
                        "insurance": "{results.ta_insurance_claim}",
                    },
                },
            ),
            SkillStep(
                id="ta_final_report",
                name="交通事故理赔综合报告",
                type="llm",
                description="生成交通事故理赔完整报告",
                depends_on=["ta_intake", "ta_law_search", "ta_injury_assessment", "ta_compensation_calc", "ta_insurance_claim", "ta_document_draft"],
                config={
                    "system_prompt": "你是资深交通事故律师，请生成完整的理赔报告。",
                    "prompt": "请综合以下分析生成交通事故理赔报告：\n\n事故分析：{results.ta_intake}\n法律依据：{results.ta_law_search}\n伤残分析：{results.ta_injury_assessment}\n赔偿计算：{results.ta_compensation_calc}\n保险理赔：{results.ta_insurance_claim}\n法律文书：{results.ta_document_draft}\n\n报告结构：\n一、事故基本情况\n二、责任认定分析\n三、伤残鉴定指导\n四、赔偿明细计算\n五、保险理赔方案\n六、法律文书\n七、处理流程与时间节点\n八、注意事项与建议",
                    "temperature": 0.3,
                    "max_tokens": 8192,
                },
            ),
        ],
        estimated_time_seconds=120,
        difficulty="medium",
    )


def _criminal_defense_guide_skill() -> SkillDefinition:
    """刑事辩护辅助指南"""
    return SkillDefinition(
        id="criminal_defense_guide",
        name="刑事辩护辅助指南",
        description="刑事辩护全流程辅助：罪名分析、辩护策略、量刑情节、取保候审、庭审准备",
        category="criminal",
        version="2.0.0",
        tags=["刑事辩护", "罪名分析", "量刑辩护", "取保候审", "无罪辩护", "罪轻辩护", "缓刑"],
        input_schema={
            "type": "object",
            "properties": {
                "case_description": {"type": "string", "description": "案件描述"},
                "charge": {"type": "string", "description": "指控罪名"},
                "stage": {"type": "string", "enum": ["侦查阶段", "审查起诉", "一审", "二审", "再审", "死刑复核"], "description": "诉讼阶段"},
                "defendant_info": {"type": "object", "description": "被告人信息"},
                "victim_info": {"type": "object", "description": "被害人信息"},
                "evidence_summary": {"type": "string", "description": "证据概要"},
                "has_surrendered": {"type": "boolean", "description": "是否自首"},
                "has_confessed": {"type": "boolean", "description": "是否认罪认罚"},
            },
            "required": ["case_description", "charge"],
        },
        steps=[
            SkillStep(
                id="cr_intake",
                name="刑事案件受理分析",
                type="llm",
                description="分析刑事案件基本情况（仅供法律分析参考）",
                config={
                    "system_prompt": "你是刑事辩护律师，请基于事实和法律进行客观分析。注意：你的分析仅供法律研究参考，不构成正式辩护意见。所有分析应在合法合规的前提下进行。",
                    "prompt": "请分析以下刑事案件：\n\n案件描述：{input.case_description}\n指控罪名：{input.charge}\n诉讼阶段：{input.stage}\n被告人信息：{input.defendant_info}\n证据概要：{input.evidence_summary}\n是否自首：{input.has_surrendered}\n是否认罪认罚：{input.has_confessed}\n\n请分析：\n1. 指控罪名的构成要件\n2. 案件基本事实梳理\n3. 证据情况初步分析\n4. 程序合法性审查要点\n5. 被告人权利告知\n6. 初步辩护方向",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="cr_law_search",
                name="刑法法条检索",
                type="tool",
                description="检索相关刑法条文和司法解释",
                depends_on=["cr_intake"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "{input.charge}",
                        "category": "criminal",
                        "page_size": 15,
                    },
                },
            ),
            SkillStep(
                id="cr_case_search",
                name="刑事类案检索",
                type="tool",
                description="检索同类刑事案件裁判文书",
                depends_on=["cr_intake"],
                config={
                    "tool": "wenshu_search",
                    "parameters": {
                        "keyword": "{input.charge}",
                        "case_type": "刑事",
                        "page_size": 10,
                    },
                },
            ),
            SkillStep(
                id="cr_defense_strategy",
                name="辩护策略制定",
                type="llm",
                description="制定刑事辩护策略",
                depends_on=["cr_intake", "cr_law_search", "cr_case_search"],
                config={
                    "system_prompt": "你是刑事辩护策略专家。请依法制定辩护策略。",
                    "prompt": "基于以下分析，制定辩护策略：\n\n案件分析：{results.cr_intake}\n法律依据：{results.cr_law_search}\n类案参考：{results.cr_case_search}\n\n请制定：\n1. 无罪辩护可行性分析\n2. 罪轻辩护方向\n3. 量刑辩护策略\n4. 程序辩护要点\n5. 证据辩护方向\n6. 认罪认罚从宽分析\n7. 辩护方案对比",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="cr_sentencing_analysis",
                name="量刑情节分析",
                type="llm",
                description="分析量刑情节和量刑建议",
                depends_on=["cr_intake", "cr_defense_strategy"],
                config={
                    "system_prompt": "你是量刑分析专家，精通《刑法》量刑指导意见。",
                    "prompt": "基于以下信息，分析量刑情况：\n\n案件分析：{results.cr_intake}\n辩护策略：{results.cr_defense_strategy}\n自首：{input.has_surrendered}\n认罪认罚：{input.has_confessed}\n\n请分析：\n1. 法定量刑幅度\n2. 从重处罚情节\n3. 从轻/减轻处罚情节\n4. 自首/坦白情节分析\n5. 立功情节分析\n6. 认罪认罚从宽幅度\n7. 退赃退赔影响\n8. 被害人谅解影响\n9. 量刑预测\n10. 缓刑适用可能性",
                    "temperature": 0.2,
                },
            ),
            SkillStep(
                id="cr_bail_application",
                name="取保候审方案",
                type="llm",
                description="分析取保候审条件并制定申请方案",
                depends_on=["cr_intake", "cr_defense_strategy"],
                config={
                    "system_prompt": "你是刑事程序专家。",
                    "prompt": "基于以下信息，分析取保候审：\n\n案件分析：{results.cr_intake}\n辩护策略：{results.cr_defense_strategy}\n\n请分析：\n1. 取保候审法定条件\n2. 本案适用可能性\n3. 保证方式选择（保证人/保证金）\n4. 申请材料准备\n5. 申请时机建议\n6. 不予批准的应对方案",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="cr_trial_prep",
                name="庭审准备",
                type="llm",
                description="庭审准备工作",
                depends_on=["cr_intake", "cr_defense_strategy", "cr_sentencing_analysis"],
                config={
                    "system_prompt": "你是刑事辩护庭审专家。",
                    "prompt": "基于以下信息，准备庭审：\n\n案件分析：{results.cr_intake}\n辩护策略：{results.cr_defense_strategy}\n量刑分析：{results.cr_sentencing_analysis}\n\n请准备：\n1. 辩护提纲\n2. 质证意见\n3. 发问提纲\n4. 辩护词框架\n5. 证据提交方案\n6. 证人出庭申请\n7. 非法证据排除申请\n8. 庭审注意事项",
                    "temperature": 0.3,
                    "max_tokens": 6144,
                },
            ),
            SkillStep(
                id="cr_final_report",
                name="刑事辩护综合报告",
                type="llm",
                description="生成刑事辩护综合分析报告",
                depends_on=["cr_intake", "cr_law_search", "cr_case_search", "cr_defense_strategy", "cr_sentencing_analysis", "cr_bail_application", "cr_trial_prep"],
                config={
                    "system_prompt": "你是资深刑事辩护律师，请生成完整的辩护分析报告。",
                    "prompt": "请综合以下分析生成刑事辩护报告：\n\n案件分析：{results.cr_intake}\n法律依据：{results.cr_law_search}\n类案参考：{results.cr_case_search}\n辩护策略：{results.cr_defense_strategy}\n量刑分析：{results.cr_sentencing_analysis}\n取保候审：{results.cr_bail_application}\n庭审准备：{results.cr_trial_prep}\n\n报告结构：\n一、案件基本情况\n二、罪名分析\n三、证据分析\n四、辩护策略\n五、量刑分析\n六、取保候审方案\n七、庭审准备\n八、权利告知与注意事项\n\n特别声明：本报告仅供法律研究参考使用。",
                    "temperature": 0.3,
                    "max_tokens": 8192,
                },
            ),
        ],
        estimated_time_seconds=150,
        difficulty="complex",
    )


def _administrative_litigation_guide_skill() -> SkillDefinition:
    """行政诉讼指南"""
    return SkillDefinition(
        id="administrative_litigation_guide",
        name="行政诉讼指南",
        description="行政诉讼全流程指南：行政行为分析、复议/诉讼方案、证据准备、庭审策略",
        category="administrative",
        version="1.0.0",
        tags=["行政诉讼", "行政复议", "行政处罚", "行政许可", "行政强制", "政府信息公开"],
        input_schema={
            "type": "object",
            "properties": {
                "admin_action_type": {"type": "string", "enum": ["行政处罚", "行政许可", "行政强制", "行政征收", "行政确认", "行政裁决", "政府信息公开", "行政不作为", "其他"], "description": "行政行为类型"},
                "case_description": {"type": "string", "description": "案件描述"},
                "admin_organ": {"type": "string", "description": "行政机关名称"},
                "action_date": {"type": "string", "description": "行政行为日期"},
                "action_content": {"type": "string", "description": "行政行为内容"},
                "plaintiff_interest": {"type": "string", "description": "原告利益受影响情况"},
            },
            "required": ["admin_action_type", "case_description"],
        },
        steps=[
            SkillStep(
                id="al_intake",
                name="行政案件受理分析",
                type="llm",
                description="分析行政案件基本情况",
                config={
                    "system_prompt": "你是行政法律专家，精通《行政诉讼法》《行政处罚法》《行政许可法》《行政复议法》等。",
                    "prompt": "请分析以下行政案件：\n\n行政行为类型：{input.admin_action_type}\n案件描述：{input.case_description}\n行政机关：{input.admin_organ}\n行为日期：{input.action_date}\n行为内容：{input.action_content}\n利益影响：{input.plaintiff_interest}\n\n请分析：\n1. 行政行为类型和性质\n2. 原告主体资格分析\n3. 被告确定\n4. 受案范围分析\n5. 起诉期限分析（6个月/1年）\n6. 管辖法院\n7. 初步法律意见",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="al_law_search",
                name="行政法律检索",
                type="tool",
                description="检索行政相关法律",
                depends_on=["al_intake"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "{input.case_description}",
                        "page_size": 15,
                    },
                },
            ),
            SkillStep(
                id="al_legality_analysis",
                name="行政行为合法性分析",
                type="llm",
                description="分析行政行为的合法性",
                depends_on=["al_intake", "al_law_search"],
                config={
                    "system_prompt": "你是行政法专家，精通行政行为的合法性审查标准。",
                    "prompt": "基于以下信息，分析行政行为的合法性：\n\n案件分析：{results.al_intake}\n法律依据：{results.al_law_search}\n\n请从以下方面分析：\n1. 职权依据（是否有法定职权）\n2. 事实认定（证据是否充分）\n3. 法律适用（是否正确适用法律）\n4. 程序合法性（是否违反法定程序）\n5. 合理性（是否显失公正）\n6. 是否存在滥用职权\n7. 综合合法性评价",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="al_remedy_choice",
                name="救济途径选择",
                type="llm",
                description="分析复议和诉讼的利弊",
                depends_on=["al_intake", "al_legality_analysis"],
                config={
                    "system_prompt": "你是行政救济专家。",
                    "prompt": "基于以下信息，分析救济途径：\n\n案件分析：{results.al_intake}\n合法性分析：{results.al_legality_analysis}\n\n请分析：\n1. 行政复议方案\n   - 复议机关确定\n   - 复议申请期限（60日）\n   - 复议优劣势\n2. 行政诉讼方案\n   - 起诉期限\n   - 管辖法院\n   - 诉讼优劣势\n3. 复议前置情形分析\n4. 复议+诉讼组合方案\n5. 其他救济途径（信访、投诉等）\n6. 最优方案建议",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="al_evidence_prep",
                name="证据准备",
                type="llm",
                description="行政诉讼证据准备",
                depends_on=["al_intake", "al_legality_analysis"],
                config={
                    "system_prompt": "你是行政诉讼证据专家。注意：行政诉讼中被告承担举证责任。",
                    "prompt": "基于以下信息，准备诉讼证据：\n\n案件分析：{results.al_intake}\n合法性分析：{results.al_legality_analysis}\n\n请准备：\n1. 原告应提交的证据\n2. 要求被告提交的证据清单\n3. 举证责任分配分析\n4. 证据收集途径\n5. 证据保全申请建议\n6. 质证要点",
                    "temperature": 0.2,
                },
            ),
            SkillStep(
                id="al_document_draft",
                name="法律文书起草",
                type="agent",
                description="起草复议申请书或行政起诉状",
                depends_on=["al_intake", "al_law_search", "al_legality_analysis", "al_remedy_choice"],
                config={
                    "agent": "document_gen",
                    "input": {
                        "document_type": "administrative_complaint",
                        "case_info": "{results.al_intake}",
                        "legal_basis": "{results.al_law_search}",
                        "analysis": "{results.al_legality_analysis}",
                        "strategy": "{results.al_remedy_choice}",
                    },
                },
            ),
            SkillStep(
                id="al_final_report",
                name="行政诉讼综合报告",
                type="llm",
                description="生成行政诉讼完整报告",
                depends_on=["al_intake", "al_law_search", "al_legality_analysis", "al_remedy_choice", "al_evidence_prep", "al_document_draft"],
                config={
                    "system_prompt": "你是资深行政法律师，请生成完整的行政诉讼报告。",
                    "prompt": "请综合以下分析生成行政诉讼报告：\n\n案件分析：{results.al_intake}\n法律依据：{results.al_law_search}\n合法性分析：{results.al_legality_analysis}\n救济方案：{results.al_remedy_choice}\n证据准备：{results.al_evidence_prep}\n法律文书：{results.al_document_draft}\n\n报告结构：\n一、案件基本情况\n二、行政行为分析\n三、合法性分析\n四、救济途径\n五、证据方案\n六、法律文书\n七、诉讼策略\n八、风险与建议",
                    "temperature": 0.3,
                    "max_tokens": 6144,
                },
            ),
        ],
        estimated_time_seconds=120,
        difficulty="complex",
    )


def _consumer_rights_guide_skill() -> SkillDefinition:
    """消费者权益维权指南"""
    return SkillDefinition(
        id="consumer_rights_guide",
        name="消费者权益维权指南",
        description="消费者权益维权全流程：侵权识别、投诉渠道、赔偿计算、维权文书、诉讼方案",
        category="consumer",
        version="1.0.0",
        tags=["消费者权益", "产品责任", "虚假宣传", "退货退款", "惩罚性赔偿", "食品安全"],
        input_schema={
            "type": "object",
            "properties": {
                "consumer_issue": {"type": "string", "enum": ["商品质量", "虚假宣传", "价格欺诈", "食品安全", "服务纠纷", "网络购物", "预付卡", "霸王条款", "个人信息泄露", "其他"], "description": "消费问题类型"},
                "case_description": {"type": "string", "description": "案件描述"},
                "product_service": {"type": "string", "description": "商品或服务名称"},
                "purchase_amount": {"type": "number", "description": "消费金额"},
                "purchase_date": {"type": "string", "description": "购买日期"},
                "seller_info": {"type": "object", "description": "经营者信息"},
                "damage_description": {"type": "string", "description": "损害情况"},
            },
            "required": ["consumer_issue", "case_description"],
        },
        steps=[
            SkillStep(
                id="cr_intake",
                name="消费纠纷受理分析",
                type="llm",
                description="分析消费纠纷基本情况",
                config={
                    "system_prompt": "你是消费者权益保护专家，精通《消费者权益保护法》《产品质量法》《食品安全法》等。",
                    "prompt": "请分析以下消费纠纷：\n\n问题类型：{input.consumer_issue}\n案件描述：{input.case_description}\n商品/服务：{input.product_service}\n消费金额：{input.purchase_amount}\n购买日期：{input.purchase_date}\n经营者：{input.seller_info}\n损害情况：{input.damage_description}\n\n请分析：\n1. 消费者权利受侵害情况\n2. 经营者违法行为认定\n3. 适用的法律法规\n4. 消费者可主张的权利\n5. 赔偿请求权分析\n6. 维权难度评估",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="cr_law_search",
                name="消费者法律检索",
                type="tool",
                description="检索消费者保护相关法律",
                depends_on=["cr_intake"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "{input.case_description}",
                        "page_size": 10,
                    },
                },
            ),
            SkillStep(
                id="cr_compensation_calc",
                name="赔偿金额计算",
                type="llm",
                description="计算消费者可主张的赔偿金",
                depends_on=["cr_intake", "cr_law_search"],
                config={
                    "system_prompt": "你是消费者赔偿计算专家。",
                    "prompt": "基于以下信息，计算赔偿金额：\n\n案件分析：{results.cr_intake}\n法律依据：{results.cr_law_search}\n消费金额：{input.purchase_amount}\n\n请计算：\n1. 退货退款金额\n2. 惩罚性赔偿\n   - 欺诈：退一赔三（最低500元）\n   - 食品安全：退一赔十（最低1000元）\n3. 实际损失赔偿\n4. 人身损害赔偿（如有）\n5. 精神损害赔偿（如有）\n6. 合理维权费用\n7. 合计可主张金额",
                    "temperature": 0.2,
                },
            ),
            SkillStep(
                id="cr_complaint_plan",
                name="维权方案设计",
                type="llm",
                description="设计阶梯式维权方案",
                depends_on=["cr_intake", "cr_compensation_calc"],
                config={
                    "system_prompt": "你是消费者维权专家。",
                    "prompt": "基于以下信息，设计维权方案：\n\n案件分析：{results.cr_intake}\n赔偿计算：{results.cr_compensation_calc}\n\n请设计：\n1. 与经营者协商方案\n2. 12315投诉方案\n3. 消费者协会投诉\n4. 行政投诉（市场监管局等）\n5. 仲裁方案（如有仲裁条款）\n6. 诉讼方案\n7. 媒体曝光建议\n8. 各方案的时间成本和成功率",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="cr_evidence_prep",
                name="证据收集指导",
                type="llm",
                description="指导消费者收集证据",
                depends_on=["cr_intake", "cr_complaint_plan"],
                config={
                    "system_prompt": "你是消费者维权证据专家。",
                    "prompt": "基于以下信息，指导证据收集：\n\n案件分析：{results.cr_intake}\n维权方案：{results.cr_complaint_plan}\n\n请指导：\n1. 交易证据（订单、发票、支付记录）\n2. 商品/服务证据（照片、视频、鉴定报告）\n3. 沟通记录（聊天记录、录音录像）\n4. 宣传证据（广告页面、宣传材料）\n5. 损害证据（医疗记录、损失证明）\n6. 证据保全建议\n7. 电子证据公证",
                    "temperature": 0.2,
                },
            ),
            SkillStep(
                id="cr_document_draft",
                name="维权文书起草",
                type="agent",
                description="起草投诉书或起诉状",
                depends_on=["cr_intake", "cr_law_search", "cr_compensation_calc", "cr_complaint_plan"],
                config={
                    "agent": "document_gen",
                    "input": {
                        "document_type": "consumer_complaint",
                        "case_info": "{results.cr_intake}",
                        "legal_basis": "{results.cr_law_search}",
                        "claims": "{results.cr_compensation_calc}",
                        "strategy": "{results.cr_complaint_plan}",
                    },
                },
            ),
            SkillStep(
                id="cr_final_report",
                name="消费维权综合报告",
                type="llm",
                description="生成消费维权完整报告",
                depends_on=["cr_intake", "cr_law_search", "cr_compensation_calc", "cr_complaint_plan", "cr_evidence_prep", "cr_document_draft"],
                config={
                    "system_prompt": "你是资深消费者维权律师，请生成完整报告。",
                    "prompt": "请综合以下分析生成消费维权报告：\n\n案件分析：{results.cr_intake}\n法律依据：{results.cr_law_search}\n赔偿计算：{results.cr_compensation_calc}\n维权方案：{results.cr_complaint_plan}\n证据指导：{results.cr_evidence_prep}\n法律文书：{results.cr_document_draft}\n\n报告结构：\n一、消费纠纷概述\n二、权益受侵害分析\n三、法律依据\n四、赔偿金额计算\n五、维权方案\n六、证据清单\n七、法律文书\n八、注意事项与技巧",
                    "temperature": 0.3,
                    "max_tokens": 6144,
                },
            ),
        ],
        estimated_time_seconds=100,
        difficulty="medium",
    )


def _corporate_legal_daily_guide_skill() -> SkillDefinition:
    """公司法务日常指南"""
    return SkillDefinition(
        id="corporate_legal_daily_guide",
        name="公司法务日常指南",
        description="公司法务日常事务指南：合同管理、劳动用工、知识产权、公司治理、法律风险防控",
        category="corporate",
        version="1.0.0",
        tags=["公司法务", "合同管理", "劳动用工", "知识产权", "公司治理", "风险防控"],
        input_schema={
            "type": "object",
            "properties": {
                "legal_issue_type": {"type": "string", "enum": ["合同审查", "劳动用工", "知识产权", "公司治理", "股权事务", "投融资", "合规管理", "诉讼仲裁", "法律咨询", "其他"], "description": "法务问题类型"},
                "issue_description": {"type": "string", "description": "问题描述"},
                "company_info": {"type": "object", "description": "公司信息"},
                "urgency": {"type": "string", "enum": ["紧急", "重要", "一般"], "description": "紧急程度"},
                "related_documents": {"type": "array", "description": "相关文件"},
            },
            "required": ["legal_issue_type", "issue_description"],
        },
        steps=[
            SkillStep(
                id="cl_intake",
                name="法务问题受理分析",
                type="llm",
                description="分析公司法务问题",
                config={
                    "system_prompt": "你是资深公司法务专家，精通企业法律事务管理。",
                    "prompt": "请分析以下公司法务问题：\n\n问题类型：{input.legal_issue_type}\n问题描述：{input.issue_description}\n公司信息：{input.company_info}\n紧急程度：{input.urgency}\n相关文件：{input.related_documents}\n\n请分析：\n1. 问题性质和范围\n2. 涉及的法律关系\n3. 适用的法律法规\n4. 法律风险评估\n5. 处理优先级\n6. 初步处理建议\n7. 是否需要外部律师介入",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="cl_law_search",
                name="相关法律检索",
                type="tool",
                description="检索相关法律法规",
                depends_on=["cl_intake"],
                config={
                    "tool": "law_article_search",
                    "parameters": {
                        "keyword": "{input.issue_description}",
                        "page_size": 10,
                    },
                },
            ),
            SkillStep(
                id="cl_risk_assessment",
                name="法律风险评估",
                type="llm",
                description="评估法律风险",
                depends_on=["cl_intake", "cl_law_search"],
                config={
                    "system_prompt": "你是企业法律风险评估专家。",
                    "prompt": "基于以下信息，评估法律风险：\n\n问题分析：{results.cl_intake}\n法律依据：{results.cl_law_search}\n\n请评估：\n1. 法律风险识别\n2. 风险等级（高/中/低）\n3. 风险发生概率\n4. 潜在损失分析\n5. 风险影响范围\n6. 现有防控措施评估\n7. 风险应对建议",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="cl_solution",
                name="解决方案制定",
                type="llm",
                description="制定解决方案",
                depends_on=["cl_intake", "cl_risk_assessment"],
                config={
                    "system_prompt": "你是企业法律问题解决专家。",
                    "prompt": "基于以下信息，制定解决方案：\n\n问题分析：{results.cl_intake}\n风险评估：{results.cl_risk_assessment}\n\n请制定：\n1. 即时处理措施\n2. 短期解决方案\n3. 长期防控机制\n4. 成本效益分析\n5. 实施时间表\n6. 责任分工建议",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="cl_document_draft",
                name="法律文书起草",
                type="agent",
                description="起草相关法律文书",
                depends_on=["cl_intake", "cl_law_search", "cl_solution"],
                config={
                    "agent": "document_gen",
                    "input": {
                        "document_type": "legal_opinion_or_contract",
                        "case_info": "{results.cl_intake}",
                        "legal_basis": "{results.cl_law_search}",
                        "solution": "{results.cl_solution}",
                    },
                },
            ),
            SkillStep(
                id="cl_prevention",
                name="风险防控建议",
                type="llm",
                description="提供风险防控建议",
                depends_on=["cl_risk_assessment", "cl_solution"],
                config={
                    "system_prompt": "你是企业法律风险防控专家。",
                    "prompt": "基于以下信息，提供风险防控建议：\n\n风险评估：{results.cl_risk_assessment}\n解决方案：{results.cl_solution}\n\n请提供：\n1. 制度建设建议\n2. 流程优化建议\n3. 合同模板优化\n4. 员工培训建议\n5. 合规检查清单\n6. 预警机制建设\n7. 应急预案",
                    "temperature": 0.3,
                },
            ),
            SkillStep(
                id="cl_final_report",
                name="法务处理综合报告",
                type="llm",
                description="生成法务处理报告",
                depends_on=["cl_intake", "cl_law_search", "cl_risk_assessment", "cl_solution", "cl_document_draft", "cl_prevention"],
                config={
                    "system_prompt": "你是资深公司法务总监，请生成完整的法务处理报告。",
                    "prompt": "请综合以下分析生成法务处理报告：\n\n问题分析：{results.cl_intake}\n法律依据：{results.cl_law_search}\n风险评估：{results.cl_risk_assessment}\n解决方案：{results.cl_solution}\n法律文书：{results.cl_document_draft}\n防控建议：{results.cl_prevention}\n\n报告结构：\n一、问题概述\n二、法律分析\n三、风险评估\n四、解决方案\n五、法律文书\n六、防控建议\n七、实施计划\n八、后续跟踪事项",
                    "temperature": 0.3,
                    "max_tokens": 6144,
                },
            ),
        ],
        estimated_time_seconds=100,
        difficulty="medium",
    )


# =============================================================================
# Convenience functions
# =============================================================================

def get_skill_engine() -> SkillEngine:
    """Get the singleton SkillEngine instance."""
    return SkillEngine.get_instance()
