"""LLM 调用服务 -- 分层模型策略，基于 LangChain 统一接口

qwen3.6-flash: 轻量任务（澄清对话、选表重排序）
qwen3.6-plus: 重量任务（SQL 生成、SQL 修复）

qwen3.x 系列必须使用 MultiModalConversation API，通过自定义 BaseChatModel 包装
"""
import dashscope
import threading

from typing import Any, List, Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.callbacks import CallbackManagerForLLMRun

import os as _os, sys as _sys
_config_dir = _os.path.normpath(_os.path.join(_os.path.dirname(__file__), '..', '..', '..', '环境配置'))
if _config_dir not in _sys.path:
    _sys.path.insert(0, _config_dir)
from 环境配置.api_keys import DASHSCOPE_API_KEY
API_KEY = DASHSCOPE_API_KEY
dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'

# 线程局部：当前节点名（由 graph_builder._timed 设置，供 LLM 日志使用）
_current_node = threading.local()
_current_node.name = "unknown"

# 线程局部：当前 session_id（由 graph_builder._timed 从 state 读取后设置）
_current_session = threading.local()
_current_session.id = None


class _DashScopeChatModel(BaseChatModel):
    """LangChain ChatModel 包装器，底层使用 DashScope MultiModalConversation API。

    用于 qwen3.x 系列模型，因为此类模型必须走 MultiModalConversation 而非 Generation API。
    """
    model: str
    api_key: str

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """将 LangChain messages 转为 DashScope 格式并调用"""
        dashscope_messages = []
        for msg in messages:
            role = 'user' if isinstance(msg, HumanMessage) else 'assistant'
            dashscope_messages.append({'role': role, 'content': [{'text': msg.content}]})

        # 拼接 prompt 文本用于日志
        prompt_text = "\n".join(
            f"[{m['role']}]: {m['content'][0]['text'][:2000]}" for m in dashscope_messages
        )

        t0 = __import__('time').perf_counter()
        resp = dashscope.MultiModalConversation.call(
            model=self.model,
            messages=dashscope_messages,
            api_key=self.api_key,
        )
        elapsed = round(__import__('time').perf_counter() - t0, 3)

        if resp.status_code != 200:
            raise RuntimeError(f"LLM 调用失败: {resp.code} {resp.message}")
        if resp.output is None:
            raise RuntimeError(f"LLM 返回空: code={resp.code}, message={resp.message}")

        text = resp.output.choices[0].message.content[0]["text"]

        # 日志记录
        node = getattr(_current_node, 'name', 'unknown')
        session_id = getattr(_current_session, 'id', None)
        if session_id:
            try:
                from .logger import _loggers, _lock
                with _lock:
                    lggr = _loggers.get(session_id)
                if lggr:
                    lggr.log_llm(self.model, node, prompt_text, text, elapsed)
            except Exception:
                pass

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    @property
    def _llm_type(self) -> str:
        return "dashscope-multimodal"


# 单例实例，供节点通过管道直接使用
plus_model = _DashScopeChatModel(model="qwen3.6-plus", api_key=API_KEY)
flash_model = _DashScopeChatModel(model="qwen3.6-flash", api_key=API_KEY)


def call_llm(prompt: str) -> str:
    """调用 qwen3.6-plus（用于 SQL 生成等精确任务）"""
    return plus_model.invoke([HumanMessage(content=prompt)]).content


def call_llm_fast(prompt: str) -> str:
    """调用 qwen3.6-flash（用于轻量快速任务）"""
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
