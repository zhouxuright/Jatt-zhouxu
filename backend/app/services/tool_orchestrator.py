"""
Phase 3: LLM-driven tool orchestration for MCP tools and skill packs.

Replaces keyword-substring tool triggering with actual LLM decision-making:
the model is shown the tool catalogue and decides which tools to call and
with what arguments, then the selected tools run concurrently.

Three entry points:
- select_tools()      -- LLM picks tools + arguments for a query
- execute_tool_calls() -- run selected tools in parallel, formatted for RAG
- orchestrate()        -- select + execute in one call
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

# Hard ceiling on tools invoked for a single query. Each tool is a DB/network
# round trip, so an unbounded fan-out would blow up chat latency.
MAX_TOOLS_PER_QUERY = 3

# Per-tool timeout. A slow tool must not hold the whole chat response hostage.
TOOL_TIMEOUT_SECONDS = 20.0

# Timeout for the selection LLM call itself.
SELECTION_TIMEOUT_SECONDS = 15.0

# Truncation applied to each tool's serialized result before it enters the prompt.
MAX_RESULT_CHARS = 2500


SELECTION_SYSTEM_PROMPT = """你是一个法律AI助手的工具调度器。根据用户的问题，判断需要调用哪些外部工具来获取回答所需的信息。

## 可用工具

{tool_catalogue}

## 判断规则

1. 只在工具能提供你不知道的**具体事实**时才调用（如某公司的工商登记信息、某个数额的诉讼费、数据库中的裁判文书）。
2. 纯法律概念解释、法条含义分析、一般性建议**不需要**调用工具。
3. 用户点名了**具体法律的具体条号**（如"劳动合同法第四十七条""民法典第1079条"）并想看条文内容时，**必须**调用
   `law_article_search` 精确检索。语义检索常常召回邻近条文而漏掉被点名的那一条，
   届时只能凭记忆复述，属于须避免的情形。
4. 最多选择 {max_tools} 个工具。宁少勿多。
5. 参数必须从用户问题中**实际提取**，不要臆造。如果必填参数无法从问题中确定，就不要选这个工具。

## 输出格式

严格输出一个 JSON 对象，不要输出任何解释文字或 markdown 代码块标记：

{{"tool_calls": [{{"tool": "工具名", "arguments": {{"参数名": "参数值"}}, "reason": "为什么需要这个工具"}}]}}

如果不需要任何工具，输出：

{{"tool_calls": []}}"""


# =============================================================================
# Tool selection (LLM-driven)
# =============================================================================

async def build_tool_catalogue(allowed_tools: list[str] | None = None) -> str:
    """Render the tool catalogue for the selection prompt.

    Args:
        allowed_tools: If given, restrict the catalogue to these tool names.
                       This is how a user's explicit tool picks are honoured.
    """
    from app.mcp import get_tool_registry

    registry = await get_tool_registry()
    schemas = registry.list_tools()

    if allowed_tools:
        allowed = set(allowed_tools)
        schemas = [s for s in schemas if s["name"] in allowed]

    lines: list[str] = []
    for schema in schemas:
        params = schema.get("parameters", {})
        props = params.get("properties", {})
        required = set(params.get("required", []))

        param_lines = []
        for pname, pinfo in props.items():
            mark = "必填" if pname in required else "可选"
            ptype = pinfo.get("type", "string")
            desc = pinfo.get("description", "")
            enum = pinfo.get("enum")
            enum_str = f"，可选值: {enum}" if enum else ""
            param_lines.append(f"    - {pname} ({ptype}, {mark}): {desc}{enum_str}")

        param_block = "\n".join(param_lines) if param_lines else "    （无参数）"
        lines.append(
            f"- **{schema['name']}** [{schema.get('category', 'general')}]: "
            f"{schema['description']}\n  参数:\n{param_block}"
        )

    return "\n".join(lines) if lines else "（当前无可用工具）"


def _parse_selection_response(text: str) -> list[dict[str, Any]]:
    """Parse the selection LLM's JSON output into a list of tool calls.

    Tolerates ```json fences and leading/trailing prose, since instruction
    following on strict-JSON output is not guaranteed.
    """
    if not text:
        return []

    candidate = text.strip()

    # Strip markdown fences if the model added them despite instructions
    if candidate.startswith("```"):
        lines = candidate.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        candidate = "\n".join(lines).strip()

    # Try direct parse, then fall back to extracting the outermost JSON object
    for attempt in (candidate, _extract_json_object(candidate)):
        if not attempt:
            continue
        try:
            data = json.loads(attempt)
        except (json.JSONDecodeError, TypeError):
            continue

        if isinstance(data, dict):
            calls = data.get("tool_calls", [])
        elif isinstance(data, list):
            calls = data
        else:
            continue

        if not isinstance(calls, list):
            continue

        valid: list[dict[str, Any]] = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            name = call.get("tool") or call.get("name")
            if not name or not isinstance(name, str):
                continue
            args = call.get("arguments") or call.get("params") or {}
            if not isinstance(args, dict):
                args = {}
            valid.append({
                "tool": name,
                "arguments": args,
                "reason": str(call.get("reason", ""))[:200],
            })
        return valid

    logger.warning("Could not parse tool selection output: %s", text[:200])
    return []


def _extract_json_object(text: str) -> str | None:
    """Extract the outermost {...} span from text, or None."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start:end + 1]


async def select_tools(
    query: str,
    allowed_tools: list[str] | None = None,
    max_tools: int = MAX_TOOLS_PER_QUERY,
) -> list[dict[str, Any]]:
    """Ask the LLM which tools to call for this query.

    Args:
        query: The user's question.
        allowed_tools: Restrict selection to these tools (user's explicit picks).
        max_tools: Ceiling on returned calls.

    Returns:
        List of {tool, arguments, reason}. Empty if no tools are warranted or
        selection fails — callers must treat an empty list as "answer without
        tools", never as an error.
    """
    from app.mcp import get_tool_registry
    from app.services.llm_service import get_raw_llm_service

    catalogue = await build_tool_catalogue(allowed_tools)
    if catalogue.startswith("（当前无可用工具）"):
        return []

    system_prompt = SELECTION_SYSTEM_PROMPT.format(
        tool_catalogue=catalogue,
        max_tools=max_tools,
    )

    try:
        llm = get_raw_llm_service()
        result = await asyncio.wait_for(
            llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"用户问题：{query}\n\n请判断需要调用哪些工具。"},
                ],
                temperature=0.0,   # deterministic dispatch
                max_tokens=600,
            ),
            timeout=SELECTION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("Tool selection timed out for query: %s", query[:60])
        return []
    except Exception as exc:
        logger.warning("Tool selection LLM call failed: %s", exc)
        return []

    calls = _parse_selection_response(result.get("content", ""))

    # Drop calls naming tools that do not exist — the model can hallucinate names
    registry = await get_tool_registry()
    verified: list[dict[str, Any]] = []
    for call in calls:
        if registry.get_tool(call["tool"]) is None:
            logger.warning("LLM selected unknown tool '%s', dropping", call["tool"])
            continue
        if allowed_tools and call["tool"] not in set(allowed_tools):
            logger.warning("LLM selected out-of-scope tool '%s', dropping", call["tool"])
            continue
        verified.append(call)

    if len(verified) > max_tools:
        verified = verified[:max_tools]

    if verified:
        logger.info(
            "Tool selection for '%s': %s",
            query[:50],
            [f"{c['tool']}({list(c['arguments'].keys())})" for c in verified],
        )
    return verified


# =============================================================================
# Parallel execution
# =============================================================================

async def _run_one(call: dict[str, Any]) -> dict[str, Any]:
    """Execute a single tool call with its own timeout and error containment."""
    from app.mcp import get_tool_registry

    name = call["tool"]
    args = call.get("arguments", {})
    started = time.time()

    try:
        registry = await get_tool_registry()
        result = await asyncio.wait_for(
            registry.execute_tool(name, **args),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
        return {
            "tool": name,
            "arguments": args,
            "reason": call.get("reason", ""),
            "success": result.success,
            "data": result.data,
            "error": result.error,
            "elapsed_seconds": round(time.time() - started, 2),
        }
    except asyncio.TimeoutError:
        logger.warning("Tool '%s' timed out after %.0fs", name, TOOL_TIMEOUT_SECONDS)
        return {
            "tool": name, "arguments": args, "reason": call.get("reason", ""),
            "success": False, "data": None,
            "error": f"工具执行超时（{TOOL_TIMEOUT_SECONDS:.0f}秒）",
            "elapsed_seconds": round(time.time() - started, 2),
        }
    except Exception as exc:
        logger.warning("Tool '%s' raised: %s", name, exc)
        return {
            "tool": name, "arguments": args, "reason": call.get("reason", ""),
            "success": False, "data": None, "error": str(exc),
            "elapsed_seconds": round(time.time() - started, 2),
        }


async def execute_tool_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run the given tool calls concurrently.

    Every entry in the return value corresponds to an input call, in order,
    including failures — the caller decides how to surface them.
    """
    if not calls:
        return []
    return list(await asyncio.gather(*(_run_one(c) for c in calls)))


def format_tool_results(results: list[dict[str, Any]]) -> str:
    """Render tool results as a prompt section for the answering LLM."""
    if not results:
        return ""

    blocks: list[str] = []
    for r in results:
        header = f"### 工具 `{r['tool']}`"
        if r.get("reason"):
            header += f"\n调用原因：{r['reason']}"

        if not r.get("success"):
            blocks.append(f"{header}\n**调用失败**：{r.get('error') or '未知错误'}\n（请在回答中说明该信息未能获取，不要编造）")
            continue

        data = r.get("data")
        if data is None or data == {} or data == []:
            blocks.append(f"{header}\n未查询到结果。（请说明未找到相关信息，不要编造）")
            continue

        if isinstance(data, str):
            payload = data[:MAX_RESULT_CHARS]
        else:
            try:
                payload = json.dumps(data, ensure_ascii=False, indent=2, default=str)[:MAX_RESULT_CHARS]
            except Exception:
                payload = str(data)[:MAX_RESULT_CHARS]

        blocks.append(f"{header}\n```json\n{payload}\n```")

    return (
        "\n\n## 外部工具调用结果\n"
        "以下是通过外部工具实时查询到的信息。请优先采用这些数据作答，"
        "并明确说明信息来源。若某工具调用失败或无结果，据实说明，不要编造。\n\n"
        + "\n\n".join(blocks)
    )


# =============================================================================
# Combined entry point
# =============================================================================

async def orchestrate(
    query: str,
    allowed_tools: list[str] | None = None,
    max_tools: int = MAX_TOOLS_PER_QUERY,
) -> dict[str, Any]:
    """Select and execute tools for a query in one call.

    Returns:
        {
          "context":  prompt section to append (empty string if no tools ran),
          "calls":    what the LLM selected,
          "results":  raw execution results,
          "metadata": counts and timing for the SSE meta event,
        }

    Never raises: a failure anywhere degrades to "no tools", so chat still answers.
    """
    started = time.time()

    calls = await select_tools(query, allowed_tools=allowed_tools, max_tools=max_tools)
    if not calls:
        return {
            "context": "",
            "calls": [],
            "results": [],
            "metadata": {
                "tools_selected": 0, "tools_succeeded": 0, "tools_failed": 0,
                "elapsed_seconds": round(time.time() - started, 2),
                "selection_mode": "llm",
            },
        }

    results = await execute_tool_calls(calls)
    succeeded = sum(1 for r in results if r.get("success"))

    return {
        "context": format_tool_results(results),
        "calls": calls,
        "results": results,
        "metadata": {
            "tools_selected": len(calls),
            "tools_succeeded": succeeded,
            "tools_failed": len(results) - succeeded,
            "tools_used": [r["tool"] for r in results],
            "elapsed_seconds": round(time.time() - started, 2),
            "selection_mode": "llm",
        },
    }


# =============================================================================
# Skill pack integration
# =============================================================================

async def run_skill_for_chat(
    skill_id: str,
    query: str,
    extra_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a skill pack and render its output as chat prompt context.

    Bridges the skills engine into the chat pipeline: previously `skill_id`
    was accepted by the chat request schema but never acted on.

    Returns:
        {"context": str, "metadata": dict} — context is "" when the skill
        cannot run, so chat degrades to a normal answer.
    """
    from app.skills import get_skill_engine

    started = time.time()
    engine = get_skill_engine()
    skill = engine.get_skill(skill_id)

    if skill is None:
        logger.warning("Chat requested unknown skill '%s'", skill_id)
        return {
            "context": "",
            "metadata": {"skill_id": skill_id, "success": False, "error": "skill not found"},
        }

    # Map the user's question onto the skill's declared inputs. Skills declare
    # their own schemas, so fill every string field that has no value yet with
    # the query rather than guessing a single canonical field name.
    input_data: dict[str, Any] = dict(extra_input or {})
    props = (skill.input_schema or {}).get("properties", {})
    required = (skill.input_schema or {}).get("required", [])

    for field_name in required:
        if field_name in input_data:
            continue
        spec = props.get(field_name, {})
        if spec.get("type", "string") == "string" and not spec.get("enum"):
            input_data[field_name] = query

    # If the schema declared nothing usable, still pass the query through
    if not input_data:
        input_data = {"query": query}

    try:
        result = await engine.execute_skill(skill_id, input_data)
    except Exception as exc:
        logger.exception("Skill '%s' raised during chat execution: %s", skill_id, exc)
        return {
            "context": "",
            "metadata": {"skill_id": skill_id, "success": False, "error": str(exc)},
        }

    if not result.get("success"):
        logger.warning("Skill '%s' failed: %s", skill_id, result.get("error"))
        return {
            "context": "",
            "metadata": {
                "skill_id": skill_id,
                "skill_name": skill.name,
                "success": False,
                "error": result.get("error"),
                "elapsed_seconds": round(time.time() - started, 2),
            },
        }

    summary = result.get("final_summary") or ""
    context = ""
    if summary:
        context = (
            f"\n\n## 技能包分析结果：{skill.name}\n"
            f"以下是「{skill.name}」技能包对该问题的结构化分析输出。"
            f"请将其整合进你的回答，保留其中的法律依据和结论。\n\n"
            f"{summary[:6000]}"
        )

    return {
        "context": context,
        "metadata": {
            "skill_id": skill_id,
            "skill_name": skill.name,
            "success": True,
            "steps_executed": result.get("steps_executed", 0),
            "elapsed_seconds": round(time.time() - started, 2),
        },
    }
