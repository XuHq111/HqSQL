"""LLM 调用服务"""
import dashscope

API_KEY = "sk-fad59201f05146a5988bd4d78a04f3fa"


def call_llm(prompt: str) -> str:
    """调用 qwen-max，返回文本响应"""
    resp = dashscope.Generation.call(
        model="qwen-max",
        messages=[{'role': 'user', 'content': prompt}],
        api_key=API_KEY
    )
    if resp.status_code != 200:
        raise RuntimeError(f"LLM 调用失败: {resp.code} {resp.message}")
    if resp.output is None:
        raise RuntimeError(f"LLM 返回空: code={resp.code}, message={resp.message}")
    if hasattr(resp.output, 'text') and resp.output.text:
        return resp.output.text
    return resp.output['choices'][0]['message']['content']


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
