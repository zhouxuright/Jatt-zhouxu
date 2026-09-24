"""API v1 router -- aggregates all v1 sub-routers."""

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user

from app.api.v1.admin import router as admin_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.auth import router as auth_router
from app.api.v1.cases import router as cases_router
from app.api.v1.chat import router as chat_router
from app.api.v1.collaboration import router as collaboration_router
from app.api.v1.contract import router as contract_router
from app.api.v1.data_expansion import router as data_expansion_router
from app.api.v1.deep_think import router as deep_think_router
from app.api.v1.document import router as document_router
from app.api.v1.feedback import router as feedback_router
from app.api.v1.knowledge import router as knowledge_router
from app.api.v1.law import router as law_router
from app.api.v1.lifecycle import router as lifecycle_router
from app.api.v1.litigation import router as litigation_router
from app.api.v1.compliance import router as compliance_router
from app.api.v1.enterprise import router as enterprise_router
from app.api.v1.mcp_tools import router as mcp_tools_router
from app.mcp.server import router as mcp_server_router
from app.api.v1.search import router as search_router
from app.api.v1.skills import router as skills_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.batch import router as batch_router

router = APIRouter()

router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
router.include_router(chat_router, prefix="/chat", tags=["Chat"])
router.include_router(contract_router, prefix="/contract", tags=["Contract Review"])
router.include_router(document_router, prefix="/document", tags=["Document Generation"])
router.include_router(feedback_router, prefix="/feedback", tags=["Feedback"])
router.include_router(law_router, prefix="/law", tags=["Law Search"])
router.include_router(cases_router, prefix="/cases", tags=["Case Retrieval"])
router.include_router(analytics_router, prefix="/analytics", tags=["Analytics"])
router.include_router(tasks_router, prefix="/tasks", tags=["Async Tasks"])
router.include_router(knowledge_router, prefix="/knowledge", tags=["Knowledge Graph"])
router.include_router(collaboration_router, prefix="/collaboration", tags=["Multi-Agent Collaboration"])
router.include_router(admin_router, prefix="/admin", tags=["Admin"])

# New commercial-grade modules
#
# Every router below can spend money or touch customer data: MCP tools and
# skills run LLM calls, deep-think runs multi-pass reasoning, web search bills
# a paid API, data expansion kicks off crawls/bulk imports, and the enterprise /
# litigation / compliance / lifecycle routers read tenant documents. They were
# all mounted without an auth dependency, so an anonymous caller could invoke
# them over the public nginx entrypoint (verified: `/api/v1/mcp/tools`,
# `/api/v1/skills`, `/api/v1/data/stats`, `/api/v1/reasoning/deep-think/models`
# and `POST /api/v1/search/web` all answered 200 with no token).
#
# Auth is applied here, once, rather than per endpoint, so a newly added route
# is protected by default instead of by remembering.
_auth = [Depends(get_current_user)]

# `/mcp-server/health` stays open: it is the only endpoint on that router that
# reports liveness and carries no tool execution.
router.include_router(mcp_tools_router, prefix="/mcp", tags=["MCP Tool Calling"],
                      dependencies=_auth)
router.include_router(mcp_server_router, prefix="/mcp-server", tags=["MCP Server (2026)"])
router.include_router(skills_router, prefix="", tags=["Skills System"],
                      dependencies=_auth)
router.include_router(deep_think_router, prefix="/reasoning", tags=["Deep Thinking"],
                      dependencies=_auth)
router.include_router(search_router, prefix="", tags=["Web Search"],
                      dependencies=_auth)
router.include_router(data_expansion_router, prefix="", tags=["Data Expansion"],
                      dependencies=_auth)
router.include_router(lifecycle_router, prefix="/contract", tags=["Contract Lifecycle"],
                      dependencies=_auth)
router.include_router(litigation_router, prefix="", tags=["Litigation Support"],
                      dependencies=_auth)
router.include_router(compliance_router, prefix="", tags=["Compliance Risk"],
                      dependencies=_auth)
router.include_router(enterprise_router, prefix="", tags=["Enterprise"],
                      dependencies=_auth)
router.include_router(batch_router, prefix="/batch", tags=["Batch Upload"])