"""接口文档接口：维护接口定义，覆盖提交审核、发布文档、废弃文档等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.schema import SchemaService

router = APIRouter(prefix="/api/schema", tags=["接口文档"])

service = SchemaService()

LIST_FIELDS = ["定义编号", "关联服务", "请求方法", "参数列表", "响应示例", "错误码说明", "调用示例", "文档版本"]
STATUSES = ["草稿", "待审核", "已发布", "已废弃"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按定义编号检索"),
    status: str | None = Query(default=None, description="草稿、待审核、已发布、已废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按定义编号与状态过滤接口文档列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# 固定路径必须声明在 /{entry_id} 之前，否则 /export、/ledger 会被当成明细编号。
@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出接口文档清单：只导出已完成审核的字段清单，未发布文档不进入导出。"""
    reviewed = {int(row.get("接口定义ID") or 0): row for row in service.ledger(reviewed_only=True)}
    items = []
    for entry in service.list_entries(page=1, size=10000)[0]:
        ledger = reviewed.get(int(entry.get("id") or 0))
        if ledger is None:
            continue
        merged = dict(entry)
        merged.update({
            "文档版本": ledger.get("文档版本"),
            "字段清单": ledger.get("字段清单") or [],
            "旧字段清单": ledger.get("旧字段清单") or [],
            "错误码清单": ledger.get("错误码清单") or [],
            "兼容级别": ledger.get("兼容级别"),
            "台账审核状态": ledger.get("审核状态"),
        })
        items.append(merged)
    return {
        "module": "schema",
        "total": len(items),
        "items": items,
        "字段": ["定义编号", "文档版本", "字段清单", "旧字段清单", "错误码清单", "兼容级别"],
        "说明": "导出只读取已完成审核的字段清单；未审核或字段清单缺失的接口不导出。",
    }


@router.get("/ledger")
def get_ledger(reviewed_only: bool = Query(default=False, description="只读取已完成审核的字段清单")) -> dict[str, Any]:
    """接口字段台账：读取字段清单、旧字段清单、错误码清单与文档版本。"""
    items = service.ledger(reviewed_only=reviewed_only)
    return {"module": "schema", "total": len(items), "items": items}


@router.get("/failures")
def get_failures() -> dict[str, Any]:
    """失败的登记/更新记录：中断后从这里接着重试。"""
    items = service.failures()
    return {"module": "schema", "total": len(items), "items": items}


@router.post("/failures/{failure_id}/retry", response_model=ActionResult)
def retry_failure(failure_id: int, payload: EntryPayload | None = None) -> ActionResult:
    """按失败记录重新保存整份契约；可带修正后的字段，成功后清除失败状态。"""
    if payload is None:
        payload = EntryPayload()
    entry, message = service.retry_failure(failure_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


# 静态路径 /export、/ledger、/failures 已声明在 /{entry_id} 之前，避免被当成明细编号。


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条接口定义明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"接口定义 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条接口定义，响应示例、错误码与文档版本必须一起保存。"""
    entry, message, failure = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message, entry=failure)
    return ActionResult(ok=True, message=message, entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """按接口定义更新契约；失败原因与重试记录一并返回。"""
    entry, message, failure = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message, entry=failure)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条接口定义执行提交审核、发布文档、废弃文档；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/review")
def review_entry(entry_id: int) -> dict[str, Any]:
    """从文档版本及字段清单复核契约是否仍然一致。"""
    ledger, message, issues = service.review(entry_id)
    if ledger is None:
        raise HTTPException(status_code=404, detail=message)
    return {"ok": not issues, "message": message, "issues": issues, "ledger": ledger}
