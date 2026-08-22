"""LLM 调用服务 -- 分层模型策略，基于 LangChain 统一接口

DeepSeek 官方 API（OpenAI 兼容格式，base_url=https://api.deepseek.com）：
- plus_model / flash_model 当前统一使用 deepseek-v4-flash
- 保留两个单例，后续如需分层（重活/轻活）只需分别改模型名

Embedding 仍走 DashScope（DeepSeek 官方 API 不提供 embedding 接口），见 milvus.py
"""
import openai
import threading
import time

from typing import Any, List, Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.callbacks import CallbackManagerForLLMRun

import os as _os, sys as _sys
_config_dir = _os.path.normpath(_os.path.join(_os.path.dirname(__file__), '..', '..', '..', '环境配置'))
if _config_dir not in _sys.path:
    _sys.path.insert(0, _config_dir)
from 环境配置.api_keys import DASHSCOPE_API_KEY, DEEPSEEK_API_KEY

# 仅供 embedding 使用（milvus.py / test_recall.py 引用），勿用于 chat
API_KEY = DASHSCOPE_API_KEY

# 30s 超时 + 仅 1 次重试：DeepSeek 偶发慢响应时快速失败并向前端报错，避免无限挂起
_client = openai.OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
    timeout=30.0,
    max_retries=1,
)

# 线程局部：当前节点名（由 graph_builder._timed 设置，供 LLM 日志使用）
_current_node = threading.local()
_current_node.name = "unknown"

# 线程局部：当前 session_id（由 graph_builder._timed 从 state 读取后设置）
_current_session = threading.local()
_current_session.id = None

# 思维链推送注册表：session_id → fn(node_name, delta_text)
# 由 api.py 注册，把 LLM 的 reasoning_content 增量实时推到前端 SSE
_thinking_callbacks: dict = {}

# 思维链增量节流：累计多少字符推送一次
_THINKING_CHUNK = 60


def _push_thinking(node: str, text: str):
    """将 LLM 思维链增量推送到前端（若已注册回调）"""
    session_id = getattr(_current_session, 'id', None)
    if not session_id:
        return
    cb = _thinking_callbacks.get(session_id)
    if cb:
        try:
            cb(node, text)
        except Exception:
            pass  # 推送失败不阻塞 LLM 调用


class _DeepSeekChatModel(BaseChatModel):
    """LangChain ChatModel 包装器，底层使用 DeepSeek 官方 API（OpenAI 兼容格式）。

    使用流式调用：实时提取 reasoning_content（思维链）推送到前端，
    并在流式结束后组装完整正文。
    """
    model: str

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """将 LangChain messages 转为 OpenAI 格式并流式调用"""
        openai_messages = []
        for msg in messages:
            role = 'user' if isinstance(msg, HumanMessage) else 'assistant'
            openai_messages.append({'role': role, 'content': str(msg.content)})

        # 拼接 prompt 文本用于日志
        prompt_text = "\n".join(
            f"[{m['role']}]: {m['content'][:2000]}" for m in openai_messages
        )
        node = getattr(_current_node, 'name', 'unknown')

        t0 = time.perf_counter()
        try:
            stream = _client.chat.completions.create(
                model=self.model, messages=openai_messages, stream=True
            )
            content_parts = []
            rc_buf = []
            rc_len = 0
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                rc = getattr(delta, 'reasoning_content', None)
                if rc:
                    rc_buf.append(rc)
                    rc_len += len(rc)
                    if rc_len >= _THINKING_CHUNK:
                        _push_thinking(node, "".join(rc_buf))
                        rc_buf = []
                        rc_len = 0
                if delta.content:
                    content_parts.append(delta.content)
            if rc_buf:
                _push_thinking(node, "".join(rc_buf))
        except openai.APIError as e:
            raise RuntimeError(f"LLM 调用失败: {e}") from e
        elapsed = round(time.perf_counter() - t0, 3)

        text = "".join(content_parts)
        if not text:
            raise RuntimeError("LLM 返回空内容")

        # 日志记录
        session_id = getattr(_current_session, 'id', None)
        if session_id:
            try:
                from .logger import _loggers as _llm_loggers, _lock as _llm_lock
                with _llm_lock:
                    lggr = _llm_loggers.get(session_id)
                if lggr:
                    lggr.log_llm(self.model, node, prompt_text, text, elapsed)
            except Exception:
                pass  # 日志失败不阻塞 LLM 调用

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    @property
    def _llm_type(self) -> str:
        return "deepseek"


# 单例实例，供节点通过管道直接使用
# 统一使用 deepseek-v4-flash，保留双单例以便未来按任务分层
plus_model = _DeepSeekChatModel(model="deepseek-v4-flash")
flash_model = _DeepSeekChatModel(model="deepseek-v4-flash")


def call_llm(prompt: str) -> str:
    """调用 plus 模型（用于 SQL 生成等精确任务）"""
    return plus_model.invoke([HumanMessage(content=prompt)]).content


def call_llm_fast(prompt: str) -> str:
    """调用 flash 模型（用于轻量快速任务）"""
    return flash_model.invoke([HumanMessage(content=prompt)]).content


def get_short_description(schema_text: str) -> str:
    """从完整 schema 中提取表名+角色作为简短描述，用于 Stage 1 选表"""
    lines = schema_text.split('\n')
    short_lines = []
    for line in lines:
        if line.startswith('表名:') or line.startswith('角色:'):
            short_lines.append(line)
        if len(short_lines) >= 2:
            break
    return '\n'.join(short_lines) if short_lines else schema_text[:300]