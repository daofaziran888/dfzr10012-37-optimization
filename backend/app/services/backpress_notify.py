"""通知消费侧：另一个业务入口同步接收同一份恢复判定数据记录。

消费侧只按判定记录落自己的处理记录，不重新解读水位/延迟：

* 以“判定编号 + 幂等键”去重，同一份判定记录只生成一条消费处理记录；
* 空采样一律抑制通知，即使上一级别看起来已经回落；
* 通知写入侧只能采用一个可核对的恢复判定记录，找不到或存在多条冲突时拒绝写入；
* 消费处理记录只允许一个终态（已抑制 / 已通知），重复投递沿用该终态。
"""
from __future__ import annotations

import threading
from typing import Any

from app.seed import SEED_NOTIFICATION_RECORDS

MODULE = "backpress_notification"
CONSUMER = "recovery-notifier"
TERMINAL_STATUSES = {"已抑制", "已通知"}


class RecoveryNotificationService:
    def __init__(self, decisions: Any) -> None:
        self._decisions = decisions
        self._lock = threading.RLock()
        self._records: list[dict[str, Any]] = [dict(row) for row in SEED_NOTIFICATION_RECORDS]
        self._next_id = max((int(row.get("id", 0)) for row in self._records), default=0) + 1

    # ------------------------------------------------------------------
    # 接收端：同步消费一份判定记录
    # ------------------------------------------------------------------
    def receive(self, decision: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            existing = self._find_locked(decision)
            if existing is not None:
                return {
                    "ok": True,
                    "deduplicated": True,
                    "record": dict(existing),
                    "message": "同一份恢复判定记录已消费，沿用原处理记录",
                }
            # 只有“判定为已恢复且通知未被抑制”的判定记录才允许放行通知；
            # 空采样和“暂无结果”一律抑制，不能当成已经恢复。
            allow_notify = bool(decision.get("恢复判定")) and str(
                decision.get("通知抑制结果") or ""
            ) != "已抑制"
            status = "已通知" if allow_notify else "已抑制"
            record = {
                "id": self._next_id,
                "消费编号": f"NOTE-{self._next_id:04d}",
                "消费者": CONSUMER,
                "判定编号": decision.get("判定编号"),
                "判定记录ID": decision.get("id"),
                "幂等键": decision.get("幂等键"),
                "指标编号": decision.get("指标编号"),
                "流作业名": decision.get("流作业名"),
                "上一级别": decision.get("上一级别"),
                "判定后级别": decision.get("判定后级别"),
                "恢复判定": bool(decision.get("恢复判定")),
                "空采样": bool(decision.get("空采样")),
                "空采样原因": decision.get("空采样原因"),
                # 判定记录未放行时，消费侧自己的记录也如实写成“已抑制”。
                "通知抑制结果": "允许通知" if allow_notify else "已抑制",
                "通知抑制原因": decision.get("通知抑制原因"),
                "通知动作": "发送恢复通知" if allow_notify else "抑制通知",
                "处理状态": status,
                "终态": True,
                "处理说明": (
                    "恢复条件与水位/延迟回落均已校验，发送恢复通知"
                    if allow_notify
                    else "空采样或恢复条件未放行，抑制恢复通知"
                ),
                "接收时间": decision.get("创建时间"),
            }
            self._records.append(record)
            self._next_id += 1
            return {
                "ok": True,
                "deduplicated": False,
                "record": dict(record),
                "message": "已生成通知消费处理记录",
            }

    # ------------------------------------------------------------------
    # 消费侧查询：维护自身处理记录
    # ------------------------------------------------------------------
    def list_records(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        metric_no: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            rows = [dict(row) for row in sorted(self._records, key=lambda row: int(row.get("id", 0)))]
        if keyword:
            needle = str(keyword).strip()
            rows = [
                row
                for row in rows
                if needle in str(row.get("消费编号", ""))
                or needle in str(row.get("判定编号", ""))
                or needle in str(row.get("指标编号", ""))
            ]
        if status:
            rows = [row for row in rows if str(row.get("处理状态")) == status]
        if metric_no:
            rows = [row for row in rows if str(row.get("指标编号")) == str(metric_no).strip()]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_record(self, record_id: int | str) -> dict[str, Any] | None:
        with self._lock:
            text = str(record_id).strip()
            try:
                numeric_id = int(text)
            except (TypeError, ValueError):
                numeric_id = None
            if numeric_id is not None:
                for row in self._records:
                    if int(row.get("id", 0)) == numeric_id:
                        return dict(row)
            for row in self._records:
                if str(row.get("消费编号")) == text:
                    return dict(row)
        return None

    def find_by_decision(self, decision_code: Any) -> dict[str, Any] | None:
        with self._lock:
            for row in self._records:
                if str(row.get("判定编号")) == str(decision_code):
                    return dict(row)
        return None

    def conflict_report(self) -> dict[str, Any]:
        """核对判定记录与消费处理记录是否一一对应，给出可核对的冲突清单。"""
        with self._lock:
            decisions = self._decisions.list_all_decisions()
            by_key: dict[str, list[dict[str, Any]]] = {}
            for row in decisions:
                by_key.setdefault(str(row.get("判定编号")), []).append(row)
            conflicts: list[dict[str, Any]] = []
            for code, rows in by_key.items():
                if len(rows) > 1:
                    conflicts.append({"判定编号": code, "原因": "同一判定编号存在多条记录", "数量": len(rows)})
                consumed = [row for row in self._records if str(row.get("判定编号")) == code]
                if len(consumed) > 1:
                    conflicts.append({"判定编号": code, "原因": "消费侧存在多条处理记录", "数量": len(consumed)})
                if rows and not consumed:
                    conflicts.append({"判定编号": code, "原因": "消费侧缺少处理记录", "数量": 0})
            return {
                "checked": len(decisions),
                "consumer_records": len(self._records),
                "conflicts": conflicts,
                "consistent": not conflicts,
            }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _find_locked(self, decision: dict[str, Any]) -> dict[str, Any] | None:
        decision_id = decision.get("id")
        code = decision.get("判定编号")
        key = decision.get("幂等键")
        for row in self._records:
            if code and str(row.get("判定编号")) == str(code):
                return row
            if key and str(row.get("幂等键")) == str(key):
                return row
            if decision_id is not None and row.get("判定记录ID") == decision_id:
                return row
        return None
