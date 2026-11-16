"""接口文档业务规则：状态流转、字段校验与筛选口径都收在这里。

登记、更新、发布、台账同步、导出和失败重试共用 services/contract.py 的解析与比较实现，
保证列表页、详情页、台账和导出看到的是同一份字段清单。
"""
from __future__ import annotations

import copy
from typing import Any

from app.services.contract import (
    arbitrate_doc_version,
    build_contract,
    compare_field_lists,
    parse_error_codes,
    verify_contract,
)
from app.store import store

MODULE = "schema"
REQUIRED_FIELDS = ["定义编号", "关联服务", "请求方法"]
DOCUMENT_FIELDS = ["定义编号", "关联服务", "请求方法", "参数列表", "响应示例", "错误码说明", "调用示例", "文档版本"]
STATUS_ORDER = ["草稿", "待审核", "已发布", "已废弃"]
ACTION_RULES = {"提交审核": "待审核", "发布文档": "已发布", "废弃文档": "已废弃"}
NEGATIVE_ACTIONS = []

LEDGER_PENDING = "待审核"
LEDGER_REVIEWED = "已审核"
FAILURE_PENDING = "待重试"
FAILURE_RESOLVED = "已重试"


class SchemaService:
    # ---------- 查询 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("定义编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._present(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._present(entry) if entry else None

    def ledger(self, *, reviewed_only: bool = False) -> list[dict[str, Any]]:
        """字段台账：导出模块只读取已审核的字段清单。"""
        rows = store.ledger()
        if reviewed_only:
            rows = [row for row in rows if row.get("审核状态") == LEDGER_REVIEWED]
        return [copy.deepcopy(row) for row in rows]

    def failures(self) -> list[dict[str, Any]]:
        """待重试的记录；已重试完成的保留在存储里，便于追溯。"""
        return [copy.deepcopy(row) for row in store.failures() if row.get("状态") != FAILURE_RESOLVED]

    def review(self, entry_id: int) -> tuple[dict[str, Any] | None, str, list[str]]:
        """复核台账中的字段清单、错误码清单和文档版本是否仍然一致。"""
        ledger = store.find_ledger(entry_id)
        if ledger is None:
            return None, f"接口定义 {entry_id} 还没有字段台账", ["字段台账不存在"]
        result = verify_contract(ledger)
        message = "字段清单、错误码清单与文档版本一致" if result["ok"] else "契约复核未通过"
        return dict(ledger), message, result["issues"]

    # ---------- 登记与更新 ----------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}", None
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        ok, message, failure = self._save_contract(entry, values, mode="create")
        if not ok:
            return None, message, failure
        rows.append(entry)
        return self._present(entry), message, None

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"接口定义 {entry_id} 不存在或已归档", None
        ok, message, failure = self._save_contract(entry, values, mode="update")
        if not ok:
            return None, message, failure
        return self._present(entry), message, None

    def _save_contract(
        self,
        entry: dict[str, Any],
        values: dict[str, Any],
        *,
        mode: str,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """把响应示例、错误码清单和文档版本作为一份契约一起保存。

        任一项解析失败都不落库，并写入失败记录；修好后可按失败记录续跑重试。
        """
        merged = {field: entry.get(field) for field in DOCUMENT_FIELDS}
        for field in DOCUMENT_FIELDS:
            if field in values:
                merged[field] = values[field]

        ledger = store.find_ledger(entry["id"]) if mode == "update" else None
        previous_fields = list((ledger or {}).get("字段清单") or entry.get("字段清单") or [])
        current_version = str((ledger or {}).get("文档版本") or entry.get("文档版本") or "").strip()

        provisional = build_contract(merged, old_fields=previous_fields)
        if not provisional["契约校验"]["ok"]:
            issues = provisional["契约校验"]["issues"]
            failure = self._record_failure(entry, merged, issues)
            return False, f"契约校验未通过：{'；'.join(issues)}", failure

        changed = bool(
            compare_field_lists(previous_fields, provisional["字段清单"])["新增字段"]
            or compare_field_lists(previous_fields, provisional["字段清单"])["移除字段"]
        )
        chosen_version, conflict = arbitrate_doc_version(
            current_version,
            merged.get("文档版本"),
            fields_changed=changed,
        )
        merged["文档版本"] = chosen_version
        final = build_contract(merged, old_fields=previous_fields)
        if not final["契约校验"]["ok"]:
            issues = final["契约校验"]["issues"]
            failure = self._record_failure(entry, merged, issues)
            return False, f"契约校验未通过：{'；'.join(issues)}", failure

        # 一次性写回：文档字段、字段清单、错误码清单、文档版本同时落库。
        for field in DOCUMENT_FIELDS:
            entry[field] = merged.get(field)
        if mode == "create":
            entry["status"] = STATUS_ORDER[0]
            entry["pending"] = True
            entry["abnormal"] = False
        entry["字段清单"] = final["字段清单"]
        entry["旧字段清单"] = final["旧字段清单"]
        entry["错误码清单"] = final["错误码清单"]
        entry["重复字段"] = final["重复字段"]
        entry["兼容级别"] = final["兼容级别"]
        entry["契约校验"] = final["契约校验"]
        entry["版本仲裁"] = conflict

        self._sync_ledger(entry, final, ledger, previous_fields, conflict)
        return True, self._save_message(mode, conflict, chosen_version), None

    def _sync_ledger(
        self,
        entry: dict[str, Any],
        contract: dict[str, Any],
        ledger: dict[str, Any] | None,
        previous_fields: list[str],
        conflict: dict[str, Any] | None,
    ) -> None:
        """台账与接口定义同步：旧字段清单和版本历史都要可追溯。"""
        is_new = ledger is None
        ledger = ledger or {
            "id": max((int(row.get("id", 0)) for row in store.ledger()), default=0) + 1,
            "接口定义ID": entry["id"],
            "版本历史": [],
            "旧字段清单": [],
        }
        history = list(ledger.get("版本历史") or [])
        old_version = ledger.get("文档版本")
        old_fields = list(ledger.get("字段清单") or [])
        old_errors = [item.get("code") for item in ledger.get("错误码清单") or [] if isinstance(item, dict)]
        new_errors = [item.get("code") for item in contract["错误码清单"]]
        contract_changed = (
            old_version != contract["文档版本"]
            or old_fields != contract["字段清单"]
            or old_errors != new_errors
        )
        if contract_changed and (old_version or old_fields):
            history.append({
                "文档版本": old_version,
                "字段清单": old_fields,
                "错误码清单": list(ledger.get("错误码清单") or []),
                "兼容级别": ledger.get("兼容级别"),
                "审核状态": ledger.get("审核状态"),
            })
        # 已发布的文档一旦改了契约就要重新审核；只有未变更的已审核台账保持已审核。
        reviewed = ledger.get("审核状态") == LEDGER_REVIEWED and not contract_changed
        fields_changed = old_fields != contract["字段清单"]
        if fields_changed:
            baseline = previous_fields
        else:
            # 字段没变时不覆盖已有基线，旧字段清单始终保留最近一次可比较的版本。
            baseline = list(ledger.get("旧字段清单") or previous_fields)
        compatibility = compare_field_lists(baseline, contract["字段清单"])["兼容级别"]
        entry["兼容级别"] = compatibility
        entry["旧字段清单"] = baseline
        ledger.update({
            "定义编号": entry.get("定义编号"),
            "文档版本": contract["文档版本"],
            "字段清单": contract["字段清单"],
            # 发布前必须保留可比较的旧字段清单；版本提升与补字段同时发生时也不覆盖。
            "旧字段清单": baseline,
            "错误码清单": contract["错误码清单"],
            "兼容级别": compatibility,
            "审核状态": LEDGER_REVIEWED if reviewed else LEDGER_PENDING,
            "版本历史": history,
            "版本仲裁": conflict,
        })
        if is_new:
            store.ledger().append(ledger)

    def _save_message(self, mode: str, conflict: dict[str, Any] | None, version: str) -> str:
        message = "接口定义已登记" if mode == "create" else "接口定义契约已更新"
        if conflict:
            message = f"{message}；版本仲裁：{conflict['原因']}，采用 {version}"
        return message

    def _record_failure(self, entry: dict[str, Any], values: dict[str, Any], issues: list[str]) -> dict[str, Any]:
        reason = "；".join(issues)
        same_key = self._failure_key(entry, values)
        existing = next(
            (row for row in store.failures() if row.get("状态") == FAILURE_PENDING and row.get("记录键") == same_key),
            None,
        )
        if existing is not None:
            # 同一条记录反复失败时沿用原失败记录，重试次数累计而不是另起一条。
            existing.update({
                "文档版本": values.get("文档版本"),
                "失败原因": reason,
                "提交内容": dict(values),
            })
            return existing
        entry_id = entry.get("id") if store.find(MODULE, int(entry.get("id") or 0)) else None
        failure = {
            "id": max((int(row.get("id", 0)) for row in store.failures()), default=0) + 1,
            "记录键": same_key,
            "接口定义ID": entry_id,
            "定义编号": values.get("定义编号") or entry.get("定义编号"),
            "文档版本": values.get("文档版本"),
            "失败原因": reason,
            "状态": FAILURE_PENDING,
            "重试次数": 0,
            "提交内容": dict(values),
        }
        store.failures().append(failure)
        return failure

    @staticmethod
    def _failure_key(entry: dict[str, Any], values: dict[str, Any]) -> str:
        entry_id = entry.get("id")
        if entry_id and store.find(MODULE, int(entry_id)) is not None:
            return f"接口定义:{entry_id}"
        return f"定义编号:{values.get('定义编号') or entry.get('定义编号') or ''}"

    # ---------- 动作 ----------

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"接口定义 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于接口文档可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        ledger = store.find_ledger(entry_id)
        if target in (STATUS_ORDER[1], STATUS_ORDER[2]):
            ledger, ok, message = self._sync_review(entry, ledger)
            if not ok:
                return None, message
            if target == STATUS_ORDER[1]:
                ledger["审核状态"] = LEDGER_PENDING
            else:
                # 发布是唯一放开导出模块的关口：字段清单、错误码、版本三者对齐才置为已审核。
                # 发布不覆盖旧字段清单，上一版字段仍可用于比较兼容级别。
                ledger["审核状态"] = LEDGER_REVIEWED

        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return self._present(entry), f"接口定义已{action}"

    def _sync_review(
        self, entry: dict[str, Any], ledger: dict[str, Any] | None
    ) -> tuple[dict[str, Any] | None, bool, str]:
        """发布前从响应示例重新解析字段清单，并与台账保存的清单比较。

        台账落后于当前文档时不会放行：必须先按最新响应示例保存一次契约，把新版字段
        清单、错误码和文档版本一起写进台账，然后才能提交审核与发布。
        """
        parsed = build_contract(entry, old_fields=list((ledger or {}).get("旧字段清单") or []))
        issues = list(parsed["契约校验"]["issues"])
        if ledger is None:
            issues.append("字段台账不存在，先登记或更新契约后才能提交审核与发布")
        if ledger is not None and not issues:
            comparison = compare_field_lists(list(ledger.get("字段清单") or []), parsed["字段清单"])
            if comparison["新增字段"] or comparison["移除字段"]:
                issues.append(
                    "台账字段清单与响应示例不一致："
                    f"新增 {'、'.join(comparison['新增字段']) or '无'}，"
                    f"移除 {'、'.join(comparison['移除字段']) or '无'}"
                )
            saved_codes, _ = parse_error_codes(ledger.get("错误码清单"))
            if [item["code"] for item in saved_codes] != [item["code"] for item in parsed["错误码清单"]]:
                issues.append("台账错误码清单与错误码说明不一致")
            if str(ledger.get("文档版本") or "") != str(parsed["文档版本"] or ""):
                issues.append(
                    f"台账文档版本 {ledger.get('文档版本') or '空'} 与当前文档版本 {parsed['文档版本'] or '空'} 不一致"
                )
            review = verify_contract(ledger)
            issues.extend(issue for issue in review["issues"] if issue not in issues)
        if issues:
            return ledger, False, f"字段清单未对齐，不能发布：{'；'.join(issues)}"
        return ledger, True, ""

    # ---------- 重试 ----------

    def retry_failure(self, failure_id: int, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        failure = store.find_failure(failure_id)
        if failure is None:
            return None, f"失败记录 {failure_id} 不存在"
        if failure.get("状态") == FAILURE_RESOLVED:
            return None, f"失败记录 {failure_id} 已经重试完成"
        failure["重试次数"] = int(failure.get("重试次数", 0)) + 1
        entry_id = int(failure.get("接口定义ID") or 0)
        # 默认沿用失败时保存的提交内容；允许带修正后的字段从同一条失败记录接着重试。
        merged = dict(failure.get("提交内容") or {})
        merged.update({key: value for key, value in (values or {}).items() if value is not None})
        failure["提交内容"] = dict(merged)
        if entry_id and store.find(MODULE, entry_id):
            entry, message, _ = self.update_entry(entry_id, merged)
        else:
            entry, message, _ = self.create_entry(merged)
        if entry is None:
            failure["失败原因"] = message
            return None, f"重试仍未通过：{message}"
        failure["状态"] = FAILURE_RESOLVED
        return entry, f"失败记录 {failure_id} 已重试成功：{message}"

    # ---------- 展示 ----------

    def _present(self, entry: dict[str, Any]) -> dict[str, Any]:
        """列表页与详情页共用同一份字段清单，不允许某个视图自行丢字段。"""
        row = copy.deepcopy(entry)
        ledger = store.find_ledger(int(entry.get("id") or 0))
        if ledger:
            row["文档版本"] = ledger.get("文档版本") or row.get("文档版本")
            row["字段清单"] = list(ledger.get("字段清单") or [])
            row["旧字段清单"] = list(ledger.get("旧字段清单") or [])
            row["错误码清单"] = list(ledger.get("错误码清单") or [])
            row["台账审核状态"] = ledger.get("审核状态")
            row["版本历史"] = list(ledger.get("版本历史") or [])
            row["字段总数"] = len(row["字段清单"])
            row["契约复核"] = verify_contract(ledger)
        else:
            row["字段清单"] = list(row.get("字段清单") or [])
            row["旧字段清单"] = list(row.get("旧字段清单") or [])
            row["错误码清单"] = list(row.get("错误码清单") or [])
            row["台账审核状态"] = "未建账"
            row["版本历史"] = []
            row["字段总数"] = len(row["字段清单"])
            row["契约复核"] = {"ok": False, "issues": ["字段台账不存在"]}
        return row
