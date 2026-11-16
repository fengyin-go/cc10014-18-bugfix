"""接口契约共用实现：解析响应示例、字段清单比较、错误码归一与版本仲裁。

接口文档的列表、详情、发布、台账同步和导出都必须走这一份实现，避免各模块各写一套
字段判定口径。字段清单只从响应示例里的原始数据解析，重复字段判定也集中在这里。
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any

FIELD_SEPARATORS = re.compile(r"[,，;；\n|、]+")
VERSION_SPLIT = re.compile(r"[._\-+]+")


class ContractError(ValueError):
    """响应示例或错误码说明无法解析时抛出，调用方据此阻断整次保存。"""


def load_structured(raw: Any) -> Any:
    """把响应示例文本还原成结构化数据；已经是 dict/list 时原样返回。"""
    if isinstance(raw, (dict, list)):
        return raw
    text = str(raw or "").strip()
    if not text:
        raise ContractError("响应示例为空")
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        try:
            return ast.literal_eval(text)
        except (SyntaxError, ValueError) as exc:
            raise ContractError("响应示例不是合法 JSON") from exc


def unique_values(values: list[str]) -> tuple[list[str], list[str]]:
    """统一去重写法：返回（去重后顺序, 重复出现的值）。"""
    seen: set[str] = set()
    unique: list[str] = []
    duplicates: list[str] = []
    for value in values:
        if value in seen:
            if value not in duplicates:
                duplicates.append(value)
            continue
        seen.add(value)
        unique.append(value)
    return unique, duplicates


def flatten_fields(node: Any, prefix: str = "") -> list[str]:
    """展开完整字段清单：只保留叶子字段，数组内的同名字段按一个字段计算。"""
    fields: list[str] = []
    if isinstance(node, dict):
        if not node:
            return [prefix] if prefix else []
        for key, value in node.items():
            name = str(key).strip()
            if not name:
                continue
            path = f"{prefix}.{name}" if prefix else name
            if isinstance(value, (dict, list)):
                fields.extend(flatten_fields(value, path))
            else:
                fields.append(path)
        return fields
    if isinstance(node, list):
        if not node:
            return [prefix] if prefix else []
        for item in node:
            if isinstance(item, (dict, list)):
                fields.extend(flatten_fields(item, prefix))
            elif prefix:
                fields.append(prefix)
        return fields
    return [prefix] if prefix else []


def parse_field_list(raw: Any) -> list[str]:
    """解析响应示例，得到去重后的完整字段清单。"""
    fields, _ = parse_field_report(raw)
    return fields


def parse_field_report(raw: Any) -> tuple[list[str], list[str]]:
    """解析响应示例，返回（去重后的完整字段清单, 重复出现的字段）。"""
    fields = flatten_fields(load_structured(raw))
    unique, duplicates = unique_values(fields)
    if not unique:
        raise ContractError("响应示例未解析出字段")
    return unique, duplicates


def normalize_param(name: Any) -> str:
    """参数名只保留字段主体，兼容 `order_id: string`、`order_id(必填)` 这类写法。"""
    text = str(name or "").strip()
    if not text:
        return ""
    for marker in ("(", "（", ":", "：", " "):
        if marker in text:
            text = text.split(marker, 1)[0].strip()
    return text


def parse_param_list(raw: Any) -> tuple[list[str], list[str]]:
    """解析参数列表，返回（参数清单, 重复参数）。"""
    raw_items: list[Any]
    if isinstance(raw, dict):
        raw_items = list(raw.keys())
    elif isinstance(raw, (list, tuple)):
        raw_items = list(raw)
    else:
        text = str(raw or "").strip()
        if not text:
            return [], []
        try:
            loaded = json.loads(text)
        except (TypeError, ValueError):
            raw_items = FIELD_SEPARATORS.split(text)
        else:
            if isinstance(loaded, dict):
                raw_items = list(loaded.keys())
            elif isinstance(loaded, list):
                raw_items = list(loaded)
            else:
                raw_items = FIELD_SEPARATORS.split(text)
    params = [normalize_param(item) for item in raw_items]
    params = [item for item in params if item]
    unique, duplicates = unique_values(params)
    return unique, duplicates


def _error_item(code: Any, message: Any) -> dict[str, str] | None:
    code_text = str(code if code is not None else "").strip()
    if not code_text:
        return None
    return {"code": code_text, "message": str(message or "").strip()}


def parse_error_codes(raw: Any) -> tuple[list[dict[str, str]], list[str]]:
    """把错误码说明归一成 {code, message} 清单，并给出重复的错误码。"""
    if isinstance(raw, dict):
        entries = [_error_item(code, message) for code, message in raw.items()]
    elif isinstance(raw, (list, tuple)):
        entries = []
        for item in raw:
            if isinstance(item, dict):
                code = item.get("code", item.get("错误码", item.get("错误码值")))
                message = item.get("message", item.get("说明", item.get("错误说明")))
                entries.append(_error_item(code, message))
            else:
                text = str(item or "").strip()
                if not text:
                    continue
                if ":" in text or "：" in text:
                    code, message = re.split(r"[:：]", text, maxsplit=1)
                else:
                    parts = text.split(maxsplit=1)
                    code, message = parts[0], (parts[1] if len(parts) > 1 else "")
                entries.append(_error_item(code, message))
    else:
        text = str(raw or "").strip()
        if not text:
            return [], []
        try:
            loaded = json.loads(text)
        except (TypeError, ValueError):
            loaded = None
        if isinstance(loaded, (dict, list)):
            return parse_error_codes(loaded)
        entries = []
        for segment in FIELD_SEPARATORS.split(text):
            segment = segment.strip()
            if not segment:
                continue
            if ":" in segment or "：" in segment:
                code, message = re.split(r"[:：]", segment, maxsplit=1)
            else:
                parts = segment.split(maxsplit=1)
                code, message = parts[0], (parts[1] if len(parts) > 1 else "")
            entries.append(_error_item(code, message))
    cleaned = [item for item in entries if item is not None]
    codes = [item["code"] for item in cleaned]
    unique_codes, duplicates = unique_values(codes)
    by_code: dict[str, dict[str, str]] = {}
    for item in cleaned:
        by_code.setdefault(item["code"], item)
    return [by_code[code] for code in unique_codes], duplicates


def parse_version(version: Any) -> tuple[tuple[int, int | str], ...]:
    """把文档版本拆成可比较的片段，支持 v1.2.0、1.2、v2 这类写法。"""
    text = str(version or "").strip().lower()
    if text.startswith("v"):
        text = text[1:]
    if not text:
        return ()
    parts: list[tuple[int, int | str]] = []
    for part in VERSION_SPLIT.split(text):
        if not part:
            continue
        if part.isdigit():
            parts.append((0, int(part)))
        else:
            parts.append((1, part))
    return tuple(parts)


def compare_versions(left: Any, right: Any) -> int:
    """比较两个文档版本，返回 -1 / 0 / 1。"""
    left_key = parse_version(left)
    right_key = parse_version(right)
    if left_key == right_key:
        return 0
    if not left_key:
        return -1
    if not right_key:
        return 1
    return 1 if left_key > right_key else -1


def next_patch_version(current: Any) -> str:
    """在当前版本上提升一个补丁版本；没有基线时从 v0.1.0 起。"""
    text = str(current or "").strip()
    if not text:
        return "v0.1.0"
    prefix = "v" if text.lower().startswith("v") else ""
    body = text[1:] if prefix else text
    numbers = [int(part) for part in body.split(".") if part.isdigit()]
    if not numbers:
        return "v0.1.0"
    while len(numbers) < 3:
        numbers.append(0)
    numbers[-1] += 1
    return f"{prefix or 'v'}{numbers[0]}.{numbers[1]}.{numbers[2]}"


def arbitrate_doc_version(
    current: Any,
    submitted: Any,
    *,
    fields_changed: bool,
) -> tuple[str, dict[str, Any] | None]:
    """版本冲突仲裁：高版本优先；同版本字段变化时提升补丁版本，不覆盖已比对基线。"""
    current_text = str(current or "").strip()
    submitted_text = str(submitted or "").strip()
    if not submitted_text and not current_text:
        return next_patch_version(""), None
    if not submitted_text:
        chosen = next_patch_version(current_text)
        return chosen, {
            "当前版本": current_text,
            "提交版本": "",
            "采用版本": chosen,
            "原因": "缺省提交版本，按补丁版本提升",
        }
    if not current_text:
        return submitted_text, None
    order = compare_versions(submitted_text, current_text)
    if order < 0:
        chosen = next_patch_version(current_text) if fields_changed else current_text
        return chosen, {
            "当前版本": current_text,
            "提交版本": submitted_text,
            "采用版本": chosen,
            "原因": "提交版本低于当前版本，保留较高版本作为基线",
        }
    if order == 0 and fields_changed:
        chosen = next_patch_version(current_text)
        return chosen, {
            "当前版本": current_text,
            "提交版本": submitted_text,
            "采用版本": chosen,
            "原因": "同版本字段清单变化，提升补丁版本后再比较",
        }
    return submitted_text, None


def compare_field_lists(old_fields: list[str], new_fields: list[str]) -> dict[str, Any]:
    """字段清单比较的唯一实现：新增、移除、保留、重复与兼容级别。"""
    old_unique, old_duplicates = unique_values(list(old_fields or []))
    new_unique, new_duplicates = unique_values(list(new_fields or []))
    old_set = set(old_unique)
    new_set = set(new_unique)
    added = [field for field in new_unique if field not in old_set]
    removed = [field for field in old_unique if field not in new_set]
    kept = [field for field in new_unique if field in old_set]
    duplicates, _ = unique_values(old_duplicates + new_duplicates)
    if not old_unique and not new_unique:
        level = "无字段"
    elif not old_unique:
        level = "首次登记"
    elif added and not removed:
        level = "兼容"
    elif removed:
        level = "不兼容"
    else:
        level = "一致"
    return {
        "新增字段": added,
        "移除字段": removed,
        "保留字段": kept,
        "重复字段": duplicates,
        "兼容级别": level,
        "旧字段清单": old_unique,
        "字段清单": new_unique,
    }


def _param_covered(param: str, fields: list[str]) -> bool:
    target = param.lower()
    for field in fields:
        if target == field.lower() or target == field.split(".")[-1].lower():
            return True
    return False


def build_contract(
    values: dict[str, Any],
    *,
    old_fields: list[str] | None = None,
) -> dict[str, Any]:
    """从参数列表和响应示例解析出文档版本、字段清单、错误码清单及校验结果。

    `issues` 是阻断保存/发布的硬约束；`warnings` 只提示参数与响应示例的对齐情况。
    """
    issues: list[str] = []
    warnings: list[str] = []
    fields: list[str] = []
    errors: list[dict[str, str]] = []
    params: list[str] = []
    field_duplicates: list[str] = []
    error_duplicates: list[str] = []
    param_duplicates: list[str] = []

    raw_response = values.get("响应示例")
    raw_errors = values.get("错误码说明")
    version = str(values.get("文档版本") or "").strip()

    try:
        fields, field_duplicates = parse_field_report(raw_response)
    except ContractError as exc:
        issues.append(str(exc))

    try:
        errors, error_duplicates = parse_error_codes(raw_errors)
    except ContractError as exc:
        issues.append(str(exc))

    params, param_duplicates = parse_param_list(values.get("参数列表"))

    if field_duplicates:
        warnings.append(f"响应示例存在重复字段：{'、'.join(field_duplicates)}")
    if param_duplicates:
        warnings.append(f"参数列表存在重复字段：{'、'.join(param_duplicates)}")
    if error_duplicates:
        warnings.append(f"错误码清单存在重复错误码：{'、'.join(error_duplicates)}")
    if not fields:
        issues.append("字段清单为空")
    if not errors:
        issues.append("错误码清单为空")
    if not version:
        issues.append("缺少文档版本")
    missing_params = [param for param in params if not _param_covered(param, fields)]
    if missing_params and fields:
        warnings.append(f"参数未在响应示例中覆盖：{'、'.join(missing_params)}")

    comparison = compare_field_lists(list(old_fields or []), fields)
    duplicates, _ = unique_values(field_duplicates + param_duplicates)
    return {
        "文档版本": version,
        "参数清单": params,
        "字段清单": comparison["字段清单"],
        "错误码清单": errors,
        "旧字段清单": comparison["旧字段清单"],
        "新增字段": comparison["新增字段"],
        "移除字段": comparison["移除字段"],
        "保留字段": comparison["保留字段"],
        "重复字段": duplicates,
        "兼容级别": comparison["兼容级别"],
        "契约校验": {"ok": not issues, "issues": issues, "warnings": warnings},
    }


def verify_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """复核字段清单、错误码清单与文档版本是否仍然一致。"""
    issues: list[str] = []
    warnings: list[str] = []
    if not str(contract.get("文档版本") or "").strip():
        issues.append("缺少文档版本")
    fields = list(contract.get("字段清单") or [])
    if not fields:
        issues.append("字段清单为空")
    field_unique, field_duplicates = unique_values(fields)
    if field_duplicates:
        warnings.append(f"字段清单存在重复字段：{'、'.join(field_duplicates)}")
    errors = list(contract.get("错误码清单") or [])
    if not errors:
        issues.append("错误码清单为空")
    codes = [str((item or {}).get("code", "")) for item in errors if isinstance(item, dict)]
    _, code_duplicates = unique_values(codes)
    if code_duplicates:
        warnings.append(f"错误码清单存在重复错误码：{'、'.join(code_duplicates)}")
    comparison = compare_field_lists(list(contract.get("旧字段清单") or []), field_unique)
    if contract.get("兼容级别") and contract["兼容级别"] != comparison["兼容级别"]:
        issues.append(
            f"兼容级别与字段清单比较结果不一致：记录为 {contract['兼容级别']}，"
            f"复核为 {comparison['兼容级别']}"
        )
    return {"ok": not issues, "issues": issues, "warnings": warnings}
