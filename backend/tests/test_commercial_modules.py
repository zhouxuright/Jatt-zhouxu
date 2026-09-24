"""Tests for MCP Tool Calling, Skills, Deep Thinking, Web Search, and Enterprise modules."""

import pytest
from unittest.mock import patch, AsyncMock


# ============================================================================
# MCP Tool Framework Tests
# ============================================================================

class TestMCPToolRegistry:
    """Test MCP tool registry and execution."""

    def test_registry_loads_builtin_tools(self):
        from app.mcp import get_tool_registry_sync
        registry = get_tool_registry_sync()
        tools = registry.list_tools()
        assert len(tools) >= 6

    def test_tool_categories(self):
        from app.mcp import get_tool_registry_sync
        registry = get_tool_registry_sync()
        categories = registry.list_categories()
        assert "legal_research" in categories
        assert "utility" in categories

    def test_get_tool_by_name(self):
        from app.mcp import get_tool_registry_sync
        registry = get_tool_registry_sync()
        tool = registry.get_tool("legal_calculator")
        assert tool is not None
        assert tool.name == "legal_calculator"

    def test_get_nonexistent_tool(self):
        from app.mcp import get_tool_registry_sync
        registry = get_tool_registry_sync()
        tool = registry.get_tool("nonexistent_tool")
        assert tool is None

    def test_tool_schema(self):
        from app.mcp import get_tool_registry_sync
        registry = get_tool_registry_sync()
        tool = registry.get_tool("legal_calculator")
        schema = tool.get_schema()
        assert schema["name"] == "legal_calculator"
        assert "parameters" in schema
        assert schema["category"] == "utility"

    def test_registry_stats(self):
        from app.mcp import get_tool_registry_sync
        registry = get_tool_registry_sync()
        stats = registry.get_stats()
        assert stats["total_tools"] >= 6
        assert "categories" in stats


class TestLegalCalculator:
    """Test the legal calculator MCP tool."""

    @pytest.mark.asyncio
    async def test_litigation_fee_calculation(self):
        from app.mcp import get_tool_registry_sync
        registry = get_tool_registry_sync()
        result = await registry.execute_tool(
            "legal_calculator",
            calc_type="litigation_fee",
            params={"amount": 500000},
        )
        assert result.success
        data = result.data
        assert data["calc_type"] == "litigation_fee"
        assert data["litigation_fee"] == 8800.0
        assert data["claim_amount"] == 500000

    @pytest.mark.asyncio
    async def test_interest_calculation(self):
        from app.mcp import get_tool_registry_sync
        registry = get_tool_registry_sync()
        result = await registry.execute_tool(
            "legal_calculator",
            calc_type="interest",
            params={"principal": 100000, "annual_rate": 0.05, "days": 365},
        )
        assert result.success
        assert result.data["interest"] == 5000.0

    @pytest.mark.asyncio
    async def test_economic_compensation(self):
        from app.mcp import get_tool_registry_sync
        registry = get_tool_registry_sync()
        result = await registry.execute_tool(
            "legal_calculator",
            calc_type="economic_compensation",
            params={"monthly_salary": 10000, "years_of_service": 5},
        )
        assert result.success
        assert result.data["compensation_amount"] > 0

    @pytest.mark.asyncio
    async def test_invalid_calc_type(self):
        from app.mcp import get_tool_registry_sync
        registry = get_tool_registry_sync()
        result = await registry.execute_tool(
            "legal_calculator",
            calc_type="invalid_type",
            params={},
        )
        assert not result.success


# ============================================================================
# Skills System Tests
# ============================================================================

class TestSkillsSystem:
    """Test the skills system."""

    def test_engine_loads_builtin_skills(self):
        from app.skills import get_skill_engine
        engine = get_skill_engine()
        skills = engine.list_skills()
        assert len(skills) >= 7

    def test_skill_categories(self):
        from app.skills import get_skill_engine
        engine = get_skill_engine()
        categories = engine.list_categories()
        assert "labor" in categories
        assert "contract" in categories

    def test_get_skill_by_id(self):
        from app.skills import get_skill_engine
        engine = get_skill_engine()
        skill = engine.get_skill("labor_dispute")
        assert skill is not None
        assert skill.name == "劳动争议处理技能包"
        assert len(skill.steps) >= 5

    def test_search_skills(self):
        from app.skills import get_skill_engine
        engine = get_skill_engine()
        results = engine.search_skills("劳动")
        assert len(results) >= 1
        assert results[0]["id"] == "labor_dispute"

    def test_nonexistent_skill(self):
        from app.skills import get_skill_engine
        engine = get_skill_engine()
        skill = engine.get_skill("nonexistent_skill")
        assert skill is None

    def test_engine_stats(self):
        from app.skills import get_skill_engine
        engine = get_skill_engine()
        stats = engine.get_stats()
        assert stats["total_skills"] >= 7


# ============================================================================
# Contract Templates Tests
# ============================================================================

class TestContractTemplates:
    """Test the contract template engine."""

    def test_templates_loaded(self):
        from app.services.contract_templates import get_contract_template_engine
        engine = get_contract_template_engine()
        templates = engine.list_templates()
        assert len(templates) >= 10

    def test_fill_employment_contract(self):
        from app.services.contract_templates import get_contract_template_engine
        engine = get_contract_template_engine()
        result = engine.fill_template("employment_contract", {
            "employer_name": "测试科技有限公司",
            "employee_name": "张三",
            "position": "软件工程师",
            "monthly_salary": "25000",
        })
        assert result["success"] is True
        assert "测试科技有限公司" in result["filled_content"]
        assert "张三" in result["filled_content"]

    def test_fill_nonexistent_template(self):
        from app.services.contract_templates import get_contract_template_engine
        engine = get_contract_template_engine()
        result = engine.fill_template("nonexistent_template", {})
        assert result["success"] is False

    def test_template_categories(self):
        from app.services.contract_templates import get_contract_template_engine
        engine = get_contract_template_engine()
        categories = engine.list_categories()
        assert len(categories) >= 4


# ============================================================================
# Web Search Tests
# ============================================================================

class TestWebSearch:
    """Test the web search service."""

    def test_trusted_legal_domains(self):
        from app.services.web_search import TRUSTED_LEGAL_DOMAINS, is_legal_source
        assert "court.gov.cn" in TRUSTED_LEGAL_DOMAINS
        assert "npc.gov.cn" in TRUSTED_LEGAL_DOMAINS
        assert len(TRUSTED_LEGAL_DOMAINS) >= 14

    def test_legal_source_detection(self):
        from app.services.web_search import is_legal_source
        assert is_legal_source("https://www.court.gov.cn/case/123") is True
        assert is_legal_source("https://www.baidu.com/s?wd=test") is False

    def test_search_engine_creation(self):
        import os
        import pytest
        if not os.getenv("SERPER_API_KEY"):
            pytest.skip("SERPER_API_KEY not configured; web search engine runs without a key")
        from app.services.web_search import get_web_search_engine
        engine = get_web_search_engine()
        assert engine is not None
        assert engine._serper_api_key != ""


# ============================================================================
# TTS Service Tests
# ============================================================================

class TestTTSService:
    """Test the TTS voice output service."""

    def test_tts_service_creation(self):
        from app.services.tts_service import get_tts_service
        tts = get_tts_service()
        assert tts is not None

    def test_list_voices(self):
        from app.services.tts_service import get_tts_service
        tts = get_tts_service()
        voices = tts.list_voices()
        assert len(voices) >= 20
        voice_ids = [v["id"] for v in voices]
        assert "zh-CN-XiaoxiaoNeural" in voice_ids

    @pytest.mark.asyncio
    async def test_synthesize_text(self):
        from app.services.tts_service import get_tts_service
        tts = get_tts_service()
        result = await tts.synthesize("测试语音合成")
        if not result.get("error"):
            assert len(result["audio_content"]) > 0
            assert result["format"] == "mp3"


# ============================================================================
# Intent Classification Tests (with new intents)
# ============================================================================

class TestIntentClassification:
    """Test the enhanced intent classification with tool_call and deep_think."""

    def test_tool_call_intent(self):
        from app.api.v1.chat import classify_intent
        assert classify_intent("查一下北京科技有限公司") == "tool_call"
        assert classify_intent("诉讼时效是多久") == "tool_call"

    def test_deep_think_intent(self):
        from app.api.v1.chat import classify_intent
        assert classify_intent("帮我深度分析这个劳动争议") == "deep_think"

    def test_contract_review_intent(self):
        from app.api.v1.chat import classify_intent
        assert classify_intent("审查合同") == "contract_review"

    def test_document_generation_intent(self):
        from app.api.v1.chat import classify_intent
        assert classify_intent("帮我写起诉状") == "document_generation"

    def test_legal_consultation_intent(self):
        from app.api.v1.chat import classify_intent
        assert classify_intent("老板拖欠工资怎么办") == "legal_consultation"

    def test_law_retrieval_intent(self):
        from app.api.v1.chat import classify_intent
        assert classify_intent("查一下法律法规") == "law_retrieval"


# ============================================================================
# Compliance Domain Tests
# ============================================================================

class TestComplianceDomains:
    """Test compliance risk agent domains."""

    def test_domains_loaded(self):
        from app.agents.compliance_risk_agent import COMPLIANCE_DOMAINS
        assert len(COMPLIANCE_DOMAINS) >= 8
        assert "data_privacy" in COMPLIANCE_DOMAINS
        assert "labor" in COMPLIANCE_DOMAINS

    def test_domain_structure(self):
        from app.agents.compliance_risk_agent import COMPLIANCE_DOMAINS
        for domain_id, domain in COMPLIANCE_DOMAINS.items():
            assert "name" in domain
            assert "regulations" in domain
            assert "keywords" in domain
            assert len(domain["regulations"]) >= 1


# ============================================================================
# Fine-tuning Pipeline Tests
# ============================================================================

class TestFineTuningPipeline:
    """Test the fine-tuning pipeline utilities."""

    def test_supported_models(self):
        from app.services.finetuning import get_finetuning_pipeline
        pipeline = get_finetuning_pipeline()
        assert len(pipeline.SUPPORTED_BASE_MODELS) >= 5

    def test_legal_tasks(self):
        from app.services.finetuning import get_finetuning_pipeline
        pipeline = get_finetuning_pipeline()
        assert len(pipeline.LEGAL_TASKS) >= 6
        assert "legal_qa" in pipeline.LEGAL_TASKS

    def test_finetuning_config(self):
        from app.services.finetuning import get_finetuning_pipeline
        pipeline = get_finetuning_pipeline()
        config = pipeline.get_finetuning_config("qwen2.5-7b", "legal_qa")
        assert "config" in config
        assert config["config"]["finetuning_type"] == "lora"

    def test_evaluation_benchmark(self):
        from app.services.finetuning import get_finetuning_pipeline
        pipeline = get_finetuning_pipeline()
        benchmark = pipeline.get_evaluation_benchmark()
        assert benchmark["name"] == "LegalBench-CN"
        assert benchmark["total_samples"] == 1800
        assert len(benchmark["tasks"]) >= 6
