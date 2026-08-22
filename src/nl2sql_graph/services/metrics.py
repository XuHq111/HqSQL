"""指标语义层：指标注册表的加载、校验与确定性展开。

两段式架构：NL→指标 由 semantic_map 节点完成，本模块负责第二段
（指标→口径骨架/硬性需求，纯确定性计算，不调用 LLM）。
注册表配置见 rules/metrics.json，改口径不需要改代码。
"""
import json
import os
from typing import List, Optional

_REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "..", "rules", "metrics.json")

# 用户/LLM 对粒度的各种叫法 → 注册表 date_granules 的键
_GRANULE_ALIASES = {
    "日": "日", "天": "日", "day": "日", "days": "日", "daily": "日", "date": "日",
    "月": "月", "month": "月", "months": "月", "monthly": "月",
    "年": "年", "year": "年", "years": "年", "yearly": "年", "annual": "年",
}

# 命中日期维度时的宽松叫法（LLM 可能输出"月份/年份"而非注册表中的"日期"）
_DATE_DIM_NAMES = {
    "日", "天", "日期", "日/月/年",
    "月", "月份", "month", "months", "monthly",
    "年", "年份", "year", "years", "yearly",
}

_AGG_WHITELIST = {"SUM", "COUNT", "AVG", "MIN", "MAX"}


class Metric:
    """单条指标定义（metrics.json 中的一个条目）"""

    def __init__(self, raw: dict):
        self.raw = raw
        self.name: str = raw["name"]
        self.aliases: List[str] = [a.lower().strip() for a in raw.get("aliases", [])]
        self.description: str = raw.get("description", "")
        self.enabled: bool = raw.get("enabled", True)
        self.measure: dict = raw["measure"]                 # {table, alias, column, agg}
        self.joins: List[dict] = raw.get("joins", [])       # [{table, alias, on: [[左列, 右列], ...]}]
        self.filters: List[dict] = raw.get("filters", [])   # [{expr, op, value}]
        self.dims: List[dict] = raw.get("dims", [])         # [{name, column, date_granules?}]

    @property
    def all_names(self) -> List[str]:
        return [self.name.lower()] + self.aliases

    def matches(self, text: str) -> bool:
        return text.strip().lower() in self.all_names

    def dim(self, name: str) -> Optional[dict]:
        """按名称匹配维度；日期维度的常见叫法（月份/年份/月…）也能命中"""
        target = name.strip().lower()
        for d in self.dims:
            if d["name"].lower() == target:
                return d
        if target in _DATE_DIM_NAMES:
            for d in self.dims:
                if d.get("date_granules"):
                    return d
        return None

    def dims_text(self) -> str:
        parts = []
        for d in self.dims:
            text = d["name"]
            if d.get("date_granules"):
                text += "（支持按日/月/年聚合）"
            parts.append(text)
        return "、".join(parts) or "无"


class MetricsRegistry:
    """指标注册表：加载、别名检索、口径展开、配置静态校验"""

    def __init__(self, path: str = _REGISTRY_PATH):
        self.path = path
        self.metrics: List[Metric] = []
        self.problems: List[str] = []
        self.load()

    def load(self) -> None:
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.metrics = [Metric(raw) for raw in data.get("metrics", [])]

    # ------------------------------------------------------------------ 检索

    def lookup(self, name: str) -> Optional[Metric]:
        for m in self.metrics:
            if m.matches(name):
                return m
        return None

    def digest(self) -> str:
        """注册表摘要：注入 semantic_map 的 LLM 上下文"""
        enabled = [m for m in self.metrics if m.enabled]
        lines = [f"内置指标（共 {len(enabled)} 个）："]
        for m in enabled:
            aliases = "、".join(m.aliases[:5])
            lines.append(f"- {m.name}（别名：{aliases}）：{m.description}")
        lines.append("若用户查询明确要求统计/计算上述某个指标，metric 字段填其名称；否则置 null。")
        return "\n".join(lines)

    # ------------------------------------------------------------------ 校验

    def validate(self, adapter) -> List[str]:
        """静态校验：表/列是否存在（启动与 reload 时执行，问题不静默）"""
        problems: List[str] = []
        for m in self.metrics:
            if not m.enabled:
                continue
            alias_map = {m.measure["alias"]: m.measure["table"]}
            for j in m.joins:
                alias_map[j["alias"]] = j["table"]
            tables = {t for t in alias_map.values()} | {m.measure["table"]}
            for t in tables:
                if not adapter.table_exists(t):
                    problems.append(f"指标[{m.name}]：表 {t} 不存在（注册表需同步库结构）")
            columns = {t: set(adapter.get_columns(t)) for t in tables}

            def check_col(expr: str):
                qualifier, col = _split_qualified(expr)
                target = alias_map.get(qualifier) if qualifier else m.measure["table"]
                if target in columns and col not in columns[target]:
                    problems.append(f"指标[{m.name}]：列 {expr} 不存在（{target} 实际列：{sorted(columns[target])}）")

            check_col(m.measure["column"])
            for f in m.filters:
                check_col(f["expr"])
            for d in m.dims:
                check_col(d["column"])
        return problems

    # ------------------------------------------------------------------ 展开

    def expand(self, metric_name: str, dims: List[str], granule: Optional[str] = None) -> dict:
        """指标 → SQL 口径骨架 + 硬性需求条目。

        Returns:
            {hit, skeleton, context, requirement_items, warnings}
        """
        metric = self.lookup(metric_name)
        warnings: List[str] = []
        if metric is None:
            return {"hit": False, "skeleton": "", "context": "", "requirement_items": [], "warnings": warnings}
        if not metric.enabled:
            warnings.append(f"指标[{metric.name}]已停用，按通用流程生成")
            return {"hit": False, "skeleton": "", "context": "", "requirement_items": [], "warnings": warnings}

        # 1) 维度白名单校验（非法维度丢弃并告警，不阻塞）
        selected: List[dict] = []
        for name in dims:
            d = metric.dim(name)
            if d is None:
                warnings.append(f"维度[{name}]不在指标[{metric.name}]白名单内，已忽略")
            else:
                selected.append(d)

        # 2) 粒度解析（仅对日期维度生效）
        granule_key = None
        if granule:
            granule_key = _GRANULE_ALIASES.get(granule.strip().lower())
            if granule_key is None:
                warnings.append(f"粒度[{granule}]无法识别，回退为按原始日期分组")

        # 3) 维度表达式
        dim_exprs, dim_names = [], []
        for d in selected:
            if granule_key and d.get("date_granules") and granule_key in d["date_granules"]:
                dim_exprs.append(d["date_granules"][granule_key])
            else:
                dim_exprs.append(d["column"])
            dim_names.append(d["name"])

        # 4) 骨架 SQL（口径硬约束的具象化）
        measure = metric.measure
        alias = measure["alias"]
        agg = measure["agg"].upper() if measure["agg"].upper() in _AGG_WHITELIST else "SUM"
        sel_col = f"{agg}({alias}.{measure['column']}) AS {metric.name}_金额"

        lines = ["SELECT"]
        select_parts = [sel_col]
        if dim_exprs:
            select_parts = [f"{expr} AS {name}" for expr, name in zip(dim_exprs, dim_names)] + select_parts
        lines.append("    " + ",\n    ".join(select_parts))
        lines.append(f"FROM {measure['table']} {alias}")
        for j in metric.joins:
            jc = j["alias"]
            on = " AND ".join(f"{l} = {r}" for l, r in j["on"])
            lines.append(f"JOIN {j['table']} {jc} ON {on}")
        where = " AND ".join(f"{f['expr']} {f['op']} {_render_value(f['value'])}" for f in metric.filters)
        if where:
            lines.append(f"WHERE {where}")
        if dim_exprs:
            lines.append("GROUP BY " + ", ".join(dim_exprs))
        skeleton = "\n".join(lines)

        # 5) 硬性需求条目（id 从 1001 起，进入 semantic_validate/修复闭环）
        items: List[dict] = []
        nid = 1001
        items.append({
            "id": nid, "type": "aggregate",
            "desc": f"聚合口径：{metric.name} = {agg}({alias}.{measure['column']})，不得改用其他金额列或聚合函数",
        })
        nid += 1
        for j in metric.joins:
            on = " AND ".join(f"{l} = {r}" for l, r in j["on"])
            items.append({
                "id": nid, "type": "join",
                "desc": f"必须 JOIN {j['table']}（{j['alias']}，ON {on}），用于匹配科目大类口径",
            })
            nid += 1
        for f in metric.filters:
            items.append({
                "id": nid, "type": "filter",
                "desc": f"过滤口径：{f['expr']} {f['op']} {_render_value(f['value'])}",
            })
            nid += 1
        if dim_names:
            items.append({
                "id": nid, "type": "aggregate",
                "desc": f"分组维度：按{'、'.join(dim_names)}分组（指标维度白名单内）",
            })

        # 6) 上下文文本（注入 build_prompt 的硬约束段）
        filter_text = " 且 ".join(
            f"{f['expr']} {f['op']} {_render_value(f['value'])}" for f in metric.filters
        ) or "无"
        context = (
            f"指标[{metric.name}]（口径）：{metric.description}\n"
            f"度量：{agg}({alias}.{measure['column']})（表 {measure['table']}）\n"
            f"口径过滤：{filter_text}\n"
            f"允许维度：{metric.dims_text()}\n"
            "参考骨架（仅为口径示意，可补充时间范围/排序/限量等条件，但不得删改上述口径）：\n"
            + skeleton
        )

        return {
            "hit": True,
            "skeleton": skeleton,
            "context": context,
            "requirement_items": items,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------ 摘要

    def summary(self) -> dict:
        return {
            "path": self.path,
            "loaded_metrics": len(self.metrics),
            "metrics": [
                {
                    "name": m.name,
                    "enabled": m.enabled,
                    "description": m.description,
                    "dimensions": [d["name"] for d in m.dims],
                }
                for m in self.metrics
            ],
            "problems": self.problems,
        }


def _render_value(value) -> str:
    """数值不带引号；func: 前缀按 SQL 表达式原样输出；其余字符串加单引号"""
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text.startswith("func:"):
        return text[len("func:"):]
    return "'" + text.replace("'", "''") + "'"


def _split_qualified(expr: str) -> tuple:
    """'a.Account_type' → ('a', 'Account_type')；'Amount' → (None, 'Amount')"""
    if "." in expr:
        qualifier, col = expr.split(".", 1)
        return qualifier.strip(), col.strip()
    return None, expr.strip()


# ------------------------------------------------------------------ 模块级单例（供节点与 api 共用，reload 热替换实例）
_registry: Optional[MetricsRegistry] = None


def get_registry() -> MetricsRegistry:
    global _registry
    if _registry is None:
        _registry = MetricsRegistry()
    return _registry


def init_registry(adapter=None) -> MetricsRegistry:
    """启动时加载注册表并做静态校验（表/列存在性）"""
    global _registry
    _registry = MetricsRegistry()
    if adapter is not None:
        try:
            _registry.problems = _registry.validate(adapter)
        except Exception as e:  # 校验失败也要让服务能起来，问题留给日志/接口
            _registry.problems = [f"指标注册表校验异常: {e}"]
    return _registry


def reload_registry(adapter=None) -> MetricsRegistry:
    """热加载：POST /api/metrics/reload 使用；节点每次经 get_registry() 读取，即时生效"""
    global _registry
    _registry = MetricsRegistry()
    if adapter is not None:
        try:
            _registry.problems = _registry.validate(adapter)
        except Exception as e:
            _registry.problems = [f"指标注册表校验异常: {e}"]
    return _registry