"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from typing import Any

from app.seed import SEED_FAILURES, SEED_LEDGER, SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 接口字段台账与失败记录属于接口文档的伴随存储，不参与模块概览的模块计数。
        self._ledger: list[dict[str, Any]] = [dict(row) for row in SEED_LEDGER]
        self._failures: list[dict[str, Any]] = [dict(row) for row in SEED_FAILURES]

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def ledger(self) -> list[dict[str, Any]]:
        """接口字段台账：每个接口定义对应一条，按文档版本保存字段清单。"""
        return self._ledger

    def find_ledger(self, entry_id: int) -> dict[str, Any] | None:
        for row in self._ledger:
            if int(row.get("接口定义ID", 0)) == entry_id:
                return row
        return None

    def failures(self) -> list[dict[str, Any]]:
        """保存失败的记录，中断后可以从这里接着重试。"""
        return self._failures

    def find_failure(self, failure_id: int) -> dict[str, Any] | None:
        for row in self._failures:
            if int(row.get("id", 0)) == failure_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
