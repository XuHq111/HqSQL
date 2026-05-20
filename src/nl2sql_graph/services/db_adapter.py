"""数据库适配器抽象层 — 解耦执行引擎与具体数据库"""
from abc import ABC, abstractmethod
import re
import sqlite3
from contextlib import closing


class BaseDBAdapter(ABC):
    """数据库适配器抽象基类"""

    @abstractmethod
    def execute(self, sql: str) -> tuple:
        """执行 SELECT 语句，返回 (列名列表, 行数据列表)"""
        ...

    @abstractmethod
    def get_columns(self, table_name: str) -> list[str]:
        """获取表的实际列名列表（供 fix_agent 列名校正用）"""
        ...

    @abstractmethod
    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在"""
        ...


class SQLiteAdapter(BaseDBAdapter):
    """SQLite 适配器"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    @staticmethod
    def _validate_identifier(name: str) -> str:
        """验证数据库标识符（表名/列名），仅允许字母、数字和下划线"""
        if not re.fullmatch(r'^[a-zA-Z0-9_]+$', name):
            raise ValueError(f"非法标识符，仅允许 [a-zA-Z0-9_] 字符: {name!r}")
        return name

    def execute(self, sql: str) -> tuple:
        with closing(sqlite3.connect(self.db_path)) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            headers = [d[0] for d in cur.description] if cur.description else []
            return (headers, rows)

    def get_columns(self, table_name: str) -> list[str]:
        self._validate_identifier(table_name)
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute(f"PRAGMA table_info('{table_name}')")
                return [r[1] for r in cur.fetchall()]
        except sqlite3.Error:
            return []

    def table_exists(self, table_name: str) -> bool:
        self._validate_identifier(table_name)
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT 1 FROM {table_name} LIMIT 0")
                return True
        except sqlite3.Error:
            return False
