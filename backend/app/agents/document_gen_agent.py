"""
Document Generation Agent - Generates Chinese legal documents using LangGraph.

Flow: understand_requirement -> extract_elements -> match_template -> generate_document
"""
from typing import Any, Optional
import json

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field

from app.agents.base_agent import BaseAgent, AgentState
from app.prompts.legal_prompts import (
    DOCUMENT_GEN_SYSTEM_PROMPT,
    DOCUMENT_GEN_TEMPLATE,
    LEGAL_ELEMENT_EXTRACTION_PROMPT,
    LEGAL_DISCLAIMER,
)
from app.services.llm_service import ChatLLMService, get_llm_service


# =============================================================================
# Document Templates
# =============================================================================

DOCUMENT_TEMPLATES: dict[str, dict[str, Any]] = {
    "民事起诉状": {
        "display_name": "民事起诉状",
        "description": "民事案件原告向人民法院提起诉讼的文书",
        "structure": [
            "标题：民事起诉状",
            "一、当事人信息（原告、被告基本信息）",
            "二、诉讼请求",
            "三、事实与理由",
            "四、证据清单及证据来源",
            "此致",
            "XXXX人民法院",
            "具状人签名/盖章",
            "日期",
        ],
        "required_fields": [
            "原告姓名/名称",
            "原告住所地",
            "被告姓名/名称",
            "被告住所地",
            "诉讼请求",
            "案件事实",
            "法律依据",
        ],
        "optional_fields": [
            "法定代表人信息",
            "委托代理人信息",
            "第三人信息",
            "财产保全申请",
            "证据保全申请",
        ],
        "format_rules": [
            "当事人信息须完整准确，法人应注明法定代表人",
            "诉讼请求应明确具体，涉及金额须精确计算",
            "事实与理由按时间顺序陈述，逻辑清晰",
            "引用法条须注明法律全称及条款编号",
            "证据清单须逐一列明证据名称、来源及证明目的",
        ],
        "law_basis": "《中华人民共和国民事诉讼法》第122条",
    },
    "民事答辩状": {
        "display_name": "民事答辩状",
        "description": "民事案件被告针对起诉状进行答辩的文书",
        "structure": [
            "标题：民事答辩状",
            "一、答辩人信息",
            "二、答辩意见（对原告诉讼请求的回应）",
            "三、事实反驳与理由",
            "四、证据清单",
            "此致",
            "XXXX人民法院",
            "答辩人签名/盖章",
            "日期",
        ],
        "required_fields": [
            "答辩人姓名/名称",
            "被答辩人（原告）信息",
            "案由",
            "答辩意见",
            "反驳事实与理由",
        ],
        "optional_fields": [
            "反诉请求",
            "管辖权异议",
            "追加当事人申请",
        ],
        "format_rules": [
            "针对起诉状逐项答辩，观点明确",
            "反驳须有事实依据和法律依据",
            "可提出反诉请求",
            "注意答辩期限（收到起诉状副本后15日内）",
        ],
        "law_basis": "《中华人民共和国民事诉讼法》第128条",
    },
    "法律意见书": {
        "display_name": "法律意见书",
        "description": "对特定法律问题进行分析论证的专业文书",
        "structure": [
            "标题：法律意见书",
            "一、委托事项概述",
            "二、案件事实/背景情况",
            "三、相关法律法规",
            "四、法律分析",
            "五、结论与建议",
            "六、声明",
            "出具人签名/盖章",
            "日期",
        ],
        "required_fields": [
            "委托事项",
            "案件事实",
            "法律分析",
            "结论与建议",
        ],
        "optional_fields": [
            "附件清单",
            "案例参考",
            "风险提示",
        ],
        "format_rules": [
            "法律分析须严谨，区分事实判断与法律判断",
            "引用法条须注明法律名称、条款编号及原文",
            "结论应明确，如存在不确定性须如实说明",
            "须声明意见书的适用范围和限制",
            "注意法律意见书的法律效力",
        ],
        "law_basis": "律师执业规范相关要求",
    },
    "律师函": {
        "display_name": "律师函",
        "description": "律师代表当事人向对方发出的正式法律通知文书",
        "structure": [
            "标题：律师函",
            "一、委托声明",
            "二、事实陈述",
            "三、法律依据",
            "四、权利主张",
            "五、期限要求",
            "六、法律后果提示",
            "律师事务所盖章",
            "律师签名",
            "日期",
        ],
        "required_fields": [
            "委托人信息",
            "收函人信息",
            "委托事项",
            "事实陈述",
            "权利主张",
            "期限要求",
        ],
        "optional_fields": [
            "附件材料",
            "证据清单",
            "联系方式",
        ],
        "format_rules": [
            "发送主体须为律师事务所",
            "须明确委托关系",
            "事实陈述须客观准确",
            "权利主张须有法律依据",
            "须设定合理的履行期限",
            "注意措辞，避免构成威胁或敲诈",
        ],
        "law_basis": "律师执业规范及《中华人民共和国民法典》相关规定",
    },
    "仲裁申请书": {
        "display_name": "仲裁申请书",
        "description": "向仲裁机构提交的仲裁申请文书",
        "structure": [
            "标题：仲裁申请书",
            "一、申请人信息",
            "二、被申请人信息",
            "三、仲裁请求",
            "四、事实与理由",
            "五、仲裁协议依据",
            "六、证据清单",
            "此致",
            "XXXX仲裁委员会",
            "申请人签名/盖章",
            "日期",
        ],
        "required_fields": [
            "申请人姓名/名称",
            "被申请人姓名/名称",
            "仲裁请求",
            "事实与理由",
            "仲裁协议依据",
        ],
        "optional_fields": [
            "仲裁员选定/委托",
            "财产保全申请",
            "证据保全申请",
        ],
        "format_rules": [
            "须有有效的仲裁协议作为管辖依据",
            "仲裁请求应明确具体",
            "事实陈述应客观完整",
            "须注明仲裁协议的具体条款",
            "注意仲裁时效",
        ],
        "law_basis": "《中华人民共和国仲裁法》第23条",
    },
}


# =============================================================================
# State Definition
# =============================================================================

class DocumentGenState(AgentState):
    """State for the Document Generation Agent.

    Attributes:
        document_type: Type of legal document to generate.
        description: User's description of the case/requirement.
        extracted_elements: Legal elements extracted from the description.
        selected_template: The matched document template.
        generated_document: The generated legal document text.
        missing_fields: Required fields that are missing from user input.
    """

    document_type: str = Field(default="")
    description: str = Field(default="")
    extracted_elements: dict[str, Any] = Field(default_factory=dict)
    selected_template: dict[str, Any] = Field(default_factory=dict)
    generated_document: str = Field(default="")
    missing_fields: list[str] = Field(default_factory=list)


# =============================================================================
# Document Generation Agent
# =============================================================================

class DocumentGenAgent(BaseAgent[DocumentGenState]):
    """Agent for generating professional Chinese legal documents.

    Workflow:
        1. understand_requirement - Understand the document type and requirements
        2. extract_elements - Extract legal elements from user description
        3. match_template - Match the appropriate document template
        4. generate_document - Generate the final legal document
    """

    def __init__(self, name: str = "document_gen_agent") -> None:
        super().__init__(name=name)
        self._llm_service: Optional[ChatLLMService] = None

    @property
    def llm_service(self) -> ChatLLMService:
        if self._llm_service is None:
            self._llm_service = get_llm_service()
        return self._llm_service

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph StateGraph for document generation.

        Nodes:
            - understand_requirement: Determine document type and requirements
            - extract_elements: Extract legal elements from description
            - match_template: Match and load the appropriate template
            - generate_document: Generate the final document
        """
        builder = StateGraph(DocumentGenState)

        builder.add_node("understand_requirement", self._understand_requirement_node)
        builder.add_node("extract_elements", self._extract_elements_node)
        builder.add_node("match_template", self._match_template_node)
        builder.add_node("generate_document", self._generate_document_node)

        builder.set_entry_point("understand_requirement")
        builder.add_edge("understand_requirement", "extract_elements")
        builder.add_edge("extract_elements", "match_template")
        builder.add_edge("match_template", "generate_document")
        builder.add_edge("generate_document", END)

        return builder

    # -------------------------------------------------------------------------
    # Node: understand_requirement
    # -------------------------------------------------------------------------

    async def _understand_requirement_node(self, state: DocumentGenState) -> dict[str, Any]:
        """Understand the document type and requirements from user input."""
        description = state.description
        document_type = state.document_type

        if document_type and document_type in DOCUMENT_TEMPLATES:
            return {"document_type": document_type}

        if not description:
            return {"document_type": ""}

        template_names = "、".join(DOCUMENT_TEMPLATES.keys())

        llm = self.llm_service.get_llm(temperature=0.0)
        classify_prompt = f"""请根据以下用户描述，判断需要生成哪种类型的法律文书。

## 用户描述
{description}

## 可选文书类型
{template_names}

请以JSON格式返回：
```json
{{
    "document_type": "文书类型",
    "confidence": 0.0-1.0
}}
```
仅返回JSON，不要附加说明。"""

        try:
            response = await llm.ainvoke([
                SystemMessage(content=DOCUMENT_GEN_SYSTEM_PROMPT),
                HumanMessage(content=classify_prompt),
            ])
            content = response.content if hasattr(response, "content") else str(response)
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                parsed = json.loads(json_match.group())
                return {"document_type": parsed.get("document_type", document_type)}
        except Exception:
            pass

        return {"document_type": document_type}

    # -------------------------------------------------------------------------
    # Node: extract_elements
    # -------------------------------------------------------------------------

    async def _extract_elements_node(self, state: DocumentGenState) -> dict[str, Any]:
        """Extract legal elements from the user's description."""
        description = state.description
        document_type = state.document_type

        if not description or not document_type:
            return {"extracted_elements": {}, "missing_fields": []}

        llm = self.llm_service.get_llm(temperature=0.0)
        prompt = LEGAL_ELEMENT_EXTRACTION_PROMPT.format(
            description=description,
            document_type=document_type,
        )

        try:
            response = await llm.ainvoke([
                SystemMessage(content=DOCUMENT_GEN_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            content = response.content if hasattr(response, "content") else str(response)
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                elements = json.loads(json_match.group())
            else:
                elements = {}
        except Exception:
            elements = {}

        # Check for missing required fields
        template = DOCUMENT_TEMPLATES.get(document_type, {})
        required_fields = template.get("required_fields", [])
        missing = [f for f in required_fields if not elements.get(f)]

        return {
            "extracted_elements": elements,
            "missing_fields": missing,
        }

    # -------------------------------------------------------------------------
    # Node: match_template
    # -------------------------------------------------------------------------

    async def _match_template_node(self, state: DocumentGenState) -> dict[str, Any]:
        """Match and load the appropriate document template."""
        document_type = state.document_type

        template = DOCUMENT_TEMPLATES.get(document_type)
        if template is None:
            # Default to basic template
            template = {
                "display_name": document_type or "法律文书",
                "structure": ["标题", "正文", "落款", "日期"],
                "required_fields": [],
                "optional_fields": [],
                "format_rules": [],
                "law_basis": "",
            }

        return {"selected_template": template}

    # -------------------------------------------------------------------------
    # Node: generate_document
    # -------------------------------------------------------------------------

    async def _generate_document_node(self, state: DocumentGenState) -> dict[str, Any]:
        """Generate the final legal document."""
        document_type = state.document_type
        description = state.description
        elements = state.extracted_elements
        template = state.selected_template
        missing_fields = state.missing_fields

        # Format template info
        template_str = self._format_template(template)

        # Format elements
        elements_str = json.dumps(elements, ensure_ascii=False, indent=2)

        llm = self.llm_service.get_llm(temperature=0.2, max_tokens=4096)
        prompt = DOCUMENT_GEN_TEMPLATE.format(
            document_type=document_type,
            description=description,
            elements=elements_str,
            template=template_str,
        )

        # Add missing fields warning
        if missing_fields:
            missing_str = "、".join(missing_fields)
            prompt += f"\n\n**注意：以下必要信息缺失，请在文书中标注【待补充】：{missing_str}**"

        try:
            response = await llm.ainvoke([
                SystemMessage(content=DOCUMENT_GEN_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            document = response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            document = f"生成文书时出现错误：{str(e)}"

        final_output = document + LEGAL_DISCLAIMER

        return {
            "generated_document": document,
            "final_output": final_output,
        }

    def _format_template(self, template: dict[str, Any]) -> str:
        """Format a document template into a readable string."""
        parts: list[str] = []

        parts.append(f"**文书名称**：{template.get('display_name', '')}")
        parts.append(f"**法律依据**：{template.get('law_basis', '')}")

        structure = template.get("structure", [])
        if structure:
            parts.append("\n**结构框架**：")
            parts.extend(f"  {item}" for item in structure)

        required = template.get("required_fields", [])
        if required:
            parts.append(f"\n**必填信息**：{', '.join(required)}")

        optional = template.get("optional_fields", [])
        if optional:
            parts.append(f"\n**选填信息**：{', '.join(optional)}")

        format_rules = template.get("format_rules", [])
        if format_rules:
            parts.append("\n**格式规范**：")
            parts.extend(f"  - {rule}" for rule in format_rules)

        return "\n".join(parts)

    # -------------------------------------------------------------------------
    # Public run method
    # -------------------------------------------------------------------------

    async def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Execute the document generation agent.

        Args:
            input_data: Must contain 'description' key. Optional 'document_type'.

        Returns:
            Dictionary with generated document and related information.
        """
        description = input_data.get("description", "")
        document_type = input_data.get("document_type", "")

        if not description and not document_type:
            return {
                "final_output": "请提供案件描述或指定需要生成的文书类型。",
                "generated_document": "",
                "document_type": "",
                "missing_fields": [],
            }

        initial_state: dict[str, Any] = {
            "document_type": document_type,
            "description": description,
            "extracted_elements": {},
            "selected_template": {},
            "generated_document": "",
            "missing_fields": [],
            "messages": [HumanMessage(content=f"请生成{document_type or '法律文书'}：{description}")],
            "context": {},
            "final_output": "",
        }

        graph = self.compile()
        result = await graph.ainvoke(initial_state)

        return {
            "final_output": result.get("final_output", ""),
            "generated_document": result.get("generated_document", ""),
            "document_type": result.get("document_type", ""),
            "missing_fields": result.get("missing_fields", []),
            "extracted_elements": result.get("extracted_elements", {}),
        }

    def run_sync(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Synchronous wrapper for the run method."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import nest_asyncio
                nest_asyncio.apply()
            return loop.run_until_complete(self.run(input_data))
        except RuntimeError:
            return asyncio.run(self.run(input_data))


# =============================================================================
# Factory function
# =============================================================================

def create_document_gen_agent() -> DocumentGenAgent:
    """Create and return a DocumentGenAgent instance."""
    return DocumentGenAgent()