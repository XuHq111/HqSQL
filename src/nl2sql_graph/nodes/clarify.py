"""Stage 0: 查询澄清节点 -- 多轮对话消歧义与增强

通过 LangGraph interrupt() 机制暂停图执行，与用户进行多轮对话，
将模糊的自然语言查询增强为精确的 NL2SQL 输入。
"""

import json
import re

from ..services.llm import call_llm_fast as call_llm
from ..rules.clarify_context import CLARIFY_DOMAIN_CONTEXT

_MAX_CLARIFY_ROUNDS = 3


# ---------------------------------------------------------------------------
# 主节点函数
# ---------------------------------------------------------------------------

def clarify_query(state: dict) -> dict:
    """多轮对话澄清用户查询。

    状态机驱动，依赖 state["clarify_phase"] 判断当前阶段。
    使用 langgraph.types.interrupt() 暂停图执行并等待用户输入。
    """

    # --- skip_clarify 快捷路径 -----------------------------------------------
    if state.get("skip_clarify"):
        return {
            "raw_query": state["query"],
            "clarify_phase": "skipped",
        }

    phase = state.get("clarify_phase", "init")

    # ------------------------------------------------------------------
    # phase = "init"
    # ------------------------------------------------------------------
    if phase == "init":
        analysis = _analyze_query(state["query"])
        return {
            "raw_query": state["query"],
            "clarify_phase": "ask_user",
            "clarify_analysis": analysis,
            "clarify_round": 1,
        }

    # ------------------------------------------------------------------
    # phase = "ask_user"
    # ------------------------------------------------------------------
    elif phase == "ask_user":
        round_num = state.get("clarify_round", 1)
        analysis = state.get("clarify_analysis", {})

        # 安全阀：超出最大轮数则强制确认
        if round_num > _MAX_CLARIFY_ROUNDS:
            enhanced = analysis.get("enhanced_query", state.get("raw_query", ""))
            return {
                "query": enhanced,
                "clarify_phase": "confirmed",
            }

        # 调用 interrupt() 暂停图执行
        # 首次执行 → 抛出 GraphInterrupt，caller 拿到 payload
        # 恢复执行 → interrupt() 返回用户输入
        from langgraph.types import interrupt

        payload = {
            "enhanced_query": analysis.get("enhanced_query", ""),
            "questions": analysis.get("questions", []),
            "round": round_num,
        }

        user_input = interrupt(payload)

        # 按回车 → 直接确认当前增强查询，不额外调 LLM
        if not user_input.strip():
            return {
                "query": analysis.get("enhanced_query", state.get("raw_query", "")),
                "clarify_phase": "confirmed",
            }

        return {
            "clarify_phase": "process_response",
            "clarify_user_response": user_input,
        }

    # ------------------------------------------------------------------
    # phase = "process_response"
    # ------------------------------------------------------------------
    elif phase == "process_response":
        refined = _refine_query(
            raw_query=state.get("raw_query", ""),
            previous_analysis=state.get("clarify_analysis", {}),
            user_response=state.get("clarify_user_response", ""),
            round_number=state.get("clarify_round", 1),
        )

        if refined.get("confirmed"):
            return {
                "query": refined.get("enhanced_query", state.get("raw_query", "")),
                "clarify_phase": "confirmed",
                "clarify_analysis": refined,
            }
        else:
            return {
                "clarify_phase": "ask_user",
                "clarify_analysis": refined,
                "clarify_round": state.get("clarify_round", 1) + 1,
            }

    # ------------------------------------------------------------------
    # phase = "confirmed" / "skipped" → 直接放行
    # ------------------------------------------------------------------
    elif phase in ("confirmed", "skipped"):
        return {}

    # 未知 phase 兜底
    return {}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _analyze_query(query: str) -> dict:
    """首次分析用户查询，生成增强版本和澄清问题。

    调用 LLM 输出 JSON：
    {enhanced_query, questions[], domain_notes, confidence}

    返回 dict：解析成功返回 LLM 结果，失败返回兜底结构。
    """
    prompt = _build_analyze_prompt(query)
    response = call_llm(prompt)
    return _parse_json_safely(response, query)


def _refine_query(
    raw_query: str,
    previous_analysis: dict,
    user_response: str,
    round_number: int,
) -> dict:
    """根据用户反馈更新增强查询。

    调用 LLM 输出 JSON：
    {confirmed: bool, enhanced_query, questions[], domain_notes}

    返回 dict：解析成功返回 LLM 结果，失败返回兜底结构。
    """
    prompt = _build_refine_prompt(
        raw_query, previous_analysis, user_response, round_number
    )
    response = call_llm(prompt)
    return _parse_json_safely(response, raw_query)


def _parse_json_safely(response: str, fallback_query: str) -> dict:
    """安全解析 LLM 返回的 JSON 字符串。

    去除可能的 markdown 代码围栏后尝试 json.loads。
    解析失败时返回安全兜底结构。
    """
    try:
        cleaned = response.strip()
        # 去除 markdown 代码块围栏 ```json ... ```
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        return json.loads(cleaned)
    except (json.JSONDecodeError, AttributeError):
        return {
            "confirmed": True,
            "enhanced_query": fallback_query,
            "enhanced_query": fallback_query,
            "questions": [],
            "domain_notes": "",
            "confidence": "low",
        }


# ---------------------------------------------------------------------------
# Prompt 构建
# ---------------------------------------------------------------------------

def _build_analyze_prompt(query: str) -> str:
    """构建首次分析提示词"""
    return f"""{CLARIFY_DOMAIN_CONTEXT}

## 用户原始查询
{query}

## 任务
分析上述查询：
1. 识别模糊表述
2. 将通用术语替换为数据库专业术语
3. 提出最多2个关键澄清问题

输出格式（严格JSON，不要markdown代码块）：
{{"enhanced_query": "...", "questions": ["...", "..."], "domain_notes": "...", "confidence": "high|medium|low"}}"""


def _build_refine_prompt(
    raw_query: str,
    previous_analysis: dict,
    user_response: str,
    round_number: int,
) -> str:
    """构建反馈精炼提示词"""
    enhanced_query = previous_analysis.get("enhanced_query", raw_query)
    questions = previous_analysis.get("questions", [])

    return f"""{CLARIFY_DOMAIN_CONTEXT}

## 用户原始查询
{raw_query}

## 上一轮增强查询
{enhanced_query}

## 上一轮问题
{json.dumps(questions, ensure_ascii=False)}

## 用户反馈
{user_response}

## 任务
根据用户反馈更新增强查询。当前第{round_number}轮（最多{_MAX_CLARIFY_ROUNDS}轮）。

判断：如果用户确认或提供了足够信息，设置confirmed=true；如果仍有歧义，提出新问题。

输出格式（严格JSON，不要markdown代码块）：
{{"confirmed": true/false, "enhanced_query": "...", "questions": ["..."], "domain_notes": "..."}}

注意：enhanced_query 是增强后的自然语言查询，严禁生成SQL语句！"""
