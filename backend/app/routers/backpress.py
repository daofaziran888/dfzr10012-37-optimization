"""反压监控面板接口：维护反压指标，覆盖恢复判定、判定记录与通知消费记录。

路由层只做参数解析与错误翻译，状态流转和恢复条件全部收在
`app/services/backpress.py` 与 `app/services/backpress_notify.py`。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    EntryPayload,
    PageResult,
    RecoveryDecisionPayload,
    RecoveryDecisionResult,
)
from app.services.backpress import BackpressService

router = APIRouter(prefix="/api/backpress", tags=["反压监控面板"])

service = BackpressService()

LIST_FIELDS = ["指标编号", "流作业名", "反压级别", "阻塞算子", "缓冲区水位", "消费延迟", "采样时间", "处理状态"]
STATUSES = ["正常", "轻微反压", "严重反压", "已恢复"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按指标编号、流作业名或阻塞算子检索"),
    status: str | None = Query(default=None, description="正常、轻微反压、严重反压、已恢复"),
    level: str | None = Query(default=None, description="级别代码：normal、mild、severe、recovered"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按指标编号、状态与级别过滤反压监控面板列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, level=level, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# ----------------------------------------------------------------------
# 恢复判定数据记录：固定路径必须声明在 /{entry_id} 之前
# ----------------------------------------------------------------------
@router.get("/recoveries", response_model=PageResult[dict])
def list_recoveries(
    keyword: str | None = Query(default=None, description="按判定编号、指标编号、作业名、样本编号检索"),
    metric_no: str | None = Query(default=None, description="按指标编号精确定位"),
    result: str | None = Query(default=None, description="判定结果或结果代码：recovered、not_recovered"),
    recovered: bool | None = Query(default=None, description="只看判定为已恢复的记录"),
    empty_sample: bool | None = Query(default=None, description="只看空采样记录"),
    suppress: str | None = Query(default=None, description="通知抑制结果：已抑制、允许通知"),
    sample_from: str | None = Query(default=None, description="采样时间起，ISO 时间"),
    sample_to: str | None = Query(default=None, description="采样时间止，ISO 时间"),
    version: int | None = Query(default=None, description="按存量版本过滤"),
    decision_id: int | None = Query(default=None, description="按判定记录 ID 定位"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """恢复判定数据记录查询：筛选、检索、分页、定位与过滤共用一套稳定排序（按记录 ID 升序）。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_decisions(
        keyword=keyword,
        metric_no=metric_no,
        result=result,
        recovered=recovered,
        empty_sample=empty_sample,
        suppress=suppress,
        sample_from=sample_from,
        sample_to=sample_to,
        version=version,
        decision_id=decision_id,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/recoveries", response_model=RecoveryDecisionResult)
def evaluate_recovery(payload: RecoveryDecisionPayload) -> RecoveryDecisionResult:
    """读取反压指标后校验恢复条件并写入判定记录。

    恢复必须同时满足水位下限、延迟下限与延迟回落；空采样维持上一级别并抑制通知。
    缺少有效的水位/延迟时返回可读原因，不把“暂无结果”当成“已恢复”。
    """
    result = service.evaluate_recovery(payload.values, source="恢复判定")
    return RecoveryDecisionResult(**result)


@router.post("/recoveries/repair")
def repair_recoveries() -> dict[str, Any]:
    """按现有取数逻辑修正错位记录：只补字段，沿用存量版本，不改判定结果。"""
    return service.repair_decisions()


@router.get("/recoveries/{decision_id}", response_model=dict)
def get_recovery(decision_id: str) -> dict:
    """按数字 ID 或判定编号（如 RECD-0001）读取单条恢复判定记录。"""
    entry = service.get_decision(decision_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"恢复判定记录 {decision_id} 不存在或已归档")
    return entry


# ----------------------------------------------------------------------
# 通知消费侧：同步接收同一份判定记录，维护自己的处理记录
# ----------------------------------------------------------------------
@router.get("/notifications", response_model=PageResult[dict])
def list_notifications(
    keyword: str | None = Query(default=None, description="按消费编号、判定编号或指标编号检索"),
    status: str | None = Query(default=None, description="已抑制、已通知"),
    metric_no: str | None = Query(default=None, description="按指标编号过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """读取通知消费侧的处理记录；与判定记录按判定编号一一对应。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.notifier.list_records(
        keyword=keyword, status=status, metric_no=metric_no, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/notifications/conflicts", response_model=dict)
def notification_conflicts() -> dict:
    """核对判定记录与消费处理记录是否一一对应，返回可核对的冲突清单。"""
    return service.notifier.conflict_report()


@router.get("/notifications/{record_id}", response_model=dict)
def get_notification(record_id: str) -> dict:
    """按数字 ID 或消费编号读取单条通知处理记录。"""
    entry = service.notifier.get_record(record_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"通知处理记录 {record_id} 不存在或已归档")
    return entry


@router.post("/notifications", response_model=dict)
def receive_notification(payload: RecoveryDecisionPayload) -> dict:
    """另一个业务入口：按判定编号/幂等键接收同一份判定记录并落消费记录。"""
    values = payload.values
    decision = None
    decision_id = values.get("判定记录ID", values.get("id"))
    code = values.get("判定编号")
    if decision_id not in (None, ""):
        decision = service.get_decision(decision_id)
    if decision is None and code:
        decision = service.get_decision_by_code(str(code))
    if decision is None:
        raise HTTPException(
            status_code=404,
            detail="未找到可核对的恢复判定记录，通知写入侧不能把暂无结果当成已经恢复",
        )
    if values.get("version") not in (None, "") and int(values["version"]) != int(decision.get("版本") or 0):
        raise HTTPException(status_code=409, detail="判定记录版本已变化，请重新读取后再消费")
    return service.notifier.receive(decision)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出反压监控面板清单：返回当前过滤条件下的全量数据。

    必须声明在 `/{entry_id}` 之前，否则 "export" 会被当成指标 ID。
    """
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "backpress", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条反压指标明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"反压指标 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条反压指标，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="反压指标已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条反压指标执行标记反压、扩容处理、标记恢复；标记恢复必须通过恢复条件校验。"""
    action = str(payload.values.get("action") or payload.action or "").strip()
    result = service.run_action_result(entry_id, action)
    if not result.get("ok"):
        # 判定记录可能已经写入（例如空采样），但恢复未通过时动作本身必须报失败。
        return ActionResult(ok=False, message=str(result.get("message") or ""), entry=result.get("entry"))
    return ActionResult(ok=True, message=str(result.get("message") or ""), entry=result.get("entry"))

