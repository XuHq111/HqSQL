"""LLM 调用服务 -- 分层模型策略

qwen3.6-flash: 轻量任务（澄清对话、选表重排序）
qwen3.6-plus: 重量任务（SQL 生成、SQL 修复）

qwen3.x 系列必须使用 MultiModalConversation API
"""
import dashscope

API_KEY = "sk-fad59201f05146a5988bd4d78a04f3fa"
dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'


def _call_model(prompt: str, model: str) -> str:
    """底层调用（MultiModalConversation，纯文本消息）"""
    resp = dashscope.MultiModalConversation.call(
        model=model,
        messages=[{'role': 'user', 'content': [{'text': prompt}]}],
        api_key=API_KEY,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"LLM 调用失败: {resp.code} {resp.message}")
    if resp.output is None:
        raise RuntimeError(f"LLM 返回空: code={resp.code}, message={resp.message}")
    return resp.output.choices[0].message.content[0]["text"]


def call_llm(prompt: str) -> str:
    """调用 qwen3.6-plus（用于 SQL 生成等精确任务）"""
    return _call_model(prompt, "qwen3.6-plus")


def call_llm_fast(prompt: str) -> str:
    """调用 qwen3.6-flash（用于轻量快速任务）"""
    return _call_model(prompt, "qwen3.6-flash")


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
