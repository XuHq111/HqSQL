"""预留：未来 ReAct Skill 的 Tool 基类"""
from langchain_core.tools import BaseTool


class BaseSkill(BaseTool):
    """所有 NL2SQL Skill 的基类

    后续将以下节点逻辑封装为 Tool，供 ReAct agent 调用：
    - SchemaLookup: recall_tables 逻辑
    - DependencyChecker: enforce_rules 逻辑
    - SQLValidator: SQL 语法/语义校验
    """

    def _run(self, *args, **kwargs):
        raise NotImplementedError
