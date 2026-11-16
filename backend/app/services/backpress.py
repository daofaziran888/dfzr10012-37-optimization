"""反压监控面板业务规则：指标维护、恢复判定、空采样去重与通知抑制。

恢复判定的口径（与运维手册一致）：

* 恢复必须同时满足三个条件：缓冲区水位不高于下限、消费延迟不高于下限、
  消费延迟较上一次采样回落。任一条件不满足都维持上一级别，不能提前关闭反压。
* 空采样不产生可用的水位与延迟，因此绝不判定为已恢复；连续空采样只累计次数，
  同一采样编号重复提交时沿用已有判定，不重复累计。
* 采样中断后只从未完成算子继续，已完成的算子写入“已跳过算子”，不会被重跑。
* 每个“指标编号 + 采样时间 + 样本编号”只保留一个终态判定记录，并发请求返回同一份记录。
* 判定记录保留上一级别、空采样原因和通知抑制结果，并同步给通知消费侧。
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from app.seed import SEED_RECOVERY_DECISIONS
from app.store import store

MODULE = "backpress"
REQUIRED_FIELDS = ["指标编号", "流作业名", "反压级别"]
STATUS_ORDER = ["正常", "轻微反压", "严重反压", "已恢复"]
ACTION_RULES = {"标记反压": "轻微反压", "扩容处理": "严重反压", "标记恢复": "已恢复"}
NEGATIVE_ACTIONS = []

LEVEL_NAMES = {
    "normal": "正常",
    "mild": "轻微反压",
    "severe": "严重反压",
    "recovered": "已恢复",
}
LEVEL_CODES = {name: code for code, name in LEVEL_NAMES.items()}
# 兼容“反压级别”字段里直接给中文名称或英文级别代码的情况。
LEVEL_CODES.update({code: code for code in LEVEL_NAMES})

BUFFER_FLOOR_PERCENT = 30.0
LAG_FLOOR_SECONDS = 3.0
EMPTY_SAMPLE_RESULTS = {"empty", "空", "空采样", "empty-sample", "empty_sample", "none", "null"}

TIMEZONE = timezone(timedelta(hours=8))


def now_text() -> str:
    return datetime.now(TIMEZONE).isoformat(timespec="seconds")


def parse_percent(value: Any) -> float | None:
    """把 40%、0.4（水位比例）、40 都解析成百分数；无法解析时返回 None。

    数字小于等于 1 时按比例换算成百分数，和 Excel 导出的 0.4 口径对齐。
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number * 100.0 if 0.0 < number <= 1.0 else number
    text = str(value).strip().replace("％", "%").replace(" ", "")
    if not text:
        return None
    has_percent_sign = text.endswith("%")
    if has_percent_sign:
        text = text[:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    if not has_percent_sign and 0.0 < number <= 1.0:
        return number * 100.0
    return number


_UNIT_SECONDS = {"ms": 0.001, "毫秒": 0.001, "s": 1.0, "秒": 1.0, "m": 60.0, "min": 60.0, "分": 60.0, "分钟": 60.0}


def parse_seconds(value: Any) -> float | None:
    """把 6s、6000ms、1m30s、6 都解析成秒；无法解析时返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    total = 0.0
    matched = False
    for number, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|毫秒|min|s|秒|m|分|分钟)", text):
        total += float(number) * _UNIT_SECONDS[unit]
        matched = True
    if matched and re.fullmatch(r"(?:\d+(?:\.\d+)?(?:ms|毫秒|min|s|秒|m|分|分钟))+", text):
        return total
    return None


def format_percent(value: float) -> str:
    return f"{value:g}%"


def format_seconds(value: float) -> str:
    return f"{value:g}s"


def parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "是", "空", "空采样", "empty"}:
        return True
    if text in {"0", "false", "no", "n", "否", "非空", "sampled"}:
        return False
    return None


def level_code(value: Any, default: str | None = None) -> str | None:
    if value is None or value == "":
        return default
    text = str(value).strip()
    return LEVEL_CODES.get(text, LEVEL_CODES.get(text.lower(), default))


def level_name(code: str | None) -> str:
    return LEVEL_NAMES.get(str(code or ""), "严重反压")


def _time_key(value: Any) -> tuple[int, str]:
    """采样时间排序键：能解析成时间的按时间排，其余退回字符串比较。"""
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TIMEZONE)
        return (0, parsed.isoformat())
    except ValueError:
        return (1, text)


class BackpressService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._decisions: list[dict[str, Any]] = [dict(row) for row in SEED_RECOVERY_DECISIONS]
        self._next_decision_id = max(
            (int(row.get("id", 0)) for row in self._decisions), default=0
        ) + 1
        # 延迟导入，避免服务模块之间循环引用。
        from app.services.backpress_notify import RecoveryNotificationService

        self.notifier = RecoveryNotificationService(self)

    # ------------------------------------------------------------------
    # 指标列表与明细
    # ------------------------------------------------------------------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        level: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for row in rows:
            self._repair_metric(row)
        rows = sorted(rows, key=lambda row: int(row.get("id", 0)))
        if keyword:
            needle = str(keyword).strip()
            rows = [
                row
                for row in rows
                if needle in str(row.get("指标编号", ""))
                or needle in str(row.get("流作业名", ""))
                or needle in str(row.get("阻塞算子", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if level:
            code = level_code(level, default=level)
            rows = [row for row in rows if row.get("级别代码") == code]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is not None:
            self._repair_metric(entry)
        return entry

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        code = level_code(values.get("反压级别"), default="mild") or "mild"
        entry["级别代码"] = code
        entry["反压级别"] = level_name(code)
        entry["status"] = level_name(code)
        entry["缓冲区水位"] = values.get("缓冲区水位") or format_percent(BUFFER_FLOOR_PERCENT)
        entry["消费延迟"] = values.get("消费延迟") or format_seconds(LAG_FLOOR_SECONDS)
        entry["上次消费延迟"] = values.get("上次消费延迟") or entry["消费延迟"]
        entry["采样时间"] = values.get("采样时间") or now_text()
        entry["处理状态"] = "待恢复判定"
        entry["version"] = int(values.get("version") or 1)
        entry["连续空采样次数"] = 0
        entry["空采样样本"] = []
        entry["最近采样为空"] = False
        entry["最后一个未完成算子"] = None
        entry["算子清单"] = list(values.get("算子清单") or [])
        entry["pending"] = code in {"mild", "severe"}
        entry["abnormal"] = code == "severe"
        rows.append(entry)
        return entry, []

    # ------------------------------------------------------------------
    # 指标动作：标记恢复也走恢复判定，不能直接改状态
    # ------------------------------------------------------------------
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """兼容旧调用：只返回记录与说明，不区分“判定记录已写入但恢复被拦下”。"""
        result = self.run_action_result(entry_id, action)
        return result["entry"], result["message"]

    def run_action_result(self, entry_id: int, action: str) -> dict[str, Any]:
        """执行动作并给出可核对的结果。

        “标记恢复”会写入一条恢复判定记录；未同时满足水位与延迟条件时只写记录、
        不改状态，并且返回 ok=False，避免调用方把“判定已落库”当成“反压已关闭”。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"反压指标 {entry_id} 不存在或已归档", "entry": None}
        if action not in ACTION_RULES:
            return {
                "ok": False,
                "message": f"动作「{action}」不属于反压监控面板可执行范围",
                "entry": None,
            }
        self._repair_metric(entry)
        if action == "标记恢复":
            # 以当前指标上的采样取数，走与 /recoveries 完全相同的校验；
            # 水位或延迟未达标时不会把状态改成“已恢复”。
            sample_time = str(entry.get("采样时间") or now_text())
            sample_id = (entry.get("空采样样本") or [None])[-1]
            result = self.evaluate_recovery(
                {
                    "指标记录ID": entry_id,
                    "指标编号": entry.get("指标编号"),
                    "采样时间": sample_time,
                    "缓冲区水位": entry.get("缓冲区水位"),
                    "消费延迟": entry.get("消费延迟"),
                    "样本编号": sample_id,
                    "采样结果": "empty" if entry.get("最近采样为空") else "sampled",
                },
                source="标记恢复",
            )
            record = result.get("entry")
            if not result.get("ok"):
                return {
                    "ok": False,
                    "message": str(result.get("message") or "恢复判定未通过"),
                    "entry": record,
                }
            ok = bool(record and record.get("恢复判定"))
            return {
                "ok": ok,
                "message": str(result.get("message") or ""),
                "entry": record,
                "notification": result.get("notification"),
            }
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return {
                "ok": False,
                "message": f"目标状态「{target}」不在允许的状态序列里",
                "entry": None,
            }
        code = LEVEL_CODES[target]
        entry["级别代码"] = code
        entry["反压级别"] = target
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS or target == "严重反压"
        entry["处理状态"] = "待恢复判定" if target != STATUS_ORDER[-1] else "已恢复"
        return {"ok": True, "message": f"反压指标已{action}", "entry": entry}

    # ------------------------------------------------------------------
    # 恢复判定
    # ------------------------------------------------------------------
    def evaluate_recovery(
        self, values: Mapping[str, Any] | None = None, *, source: str = "恢复判定"
    ) -> dict[str, Any]:
        values = dict(values or {})
        with self._lock:
            metric = self._resolve_metric(values)
            if metric is None:
                return {
                    "ok": False,
                    "message": "反压指标不存在或已归档，无法执行恢复判定",
                    "entry": None,
                    "created": False,
                    "duplicate": False,
                }
            self._repair_metric(metric)
            sample_time = str(values.get("采样时间") or metric.get("采样时间") or now_text()).strip()
            sample_id = str(values.get("样本编号") or values.get("sample_id") or "").strip() or None
            sample_result = str(values.get("采样结果") or values.get("sample_result") or "").strip().lower()
            buffer_raw = values.get("缓冲区水位", values.get("buffer"))
            lag_raw = values.get("消费延迟", values.get("lag"))
            explicit_empty = sample_result in EMPTY_SAMPLE_RESULTS or parse_bool(values.get("空采样")) is True
            missing_sample = buffer_raw in (None, "") and lag_raw in (None, "")
            empty = explicit_empty or missing_sample
            interrupted = parse_bool(values.get("采样中断")) is True or empty
            expected_version = values.get("version", values.get("expected_version"))
            version = int(metric.get("version") or 1)
            if expected_version not in (None, ""):
                try:
                    expected = int(expected_version)
                except (TypeError, ValueError):
                    return {
                        "ok": False,
                        "message": "版本号必须是整数，无法沿用存量版本执行判定",
                        "entry": None,
                        "created": False,
                        "duplicate": False,
                    }
                if expected != version:
                    existing = self._find_terminal_by_key(
                        self._sample_key(
                            metric, sample_time, sample_id, empty
                        )
                    )
                    if existing is None and empty and sample_id:
                        existing = self._find_decision_for_sample(metric.get("指标编号"), sample_id)
                    if existing is not None:
                        return {
                            "ok": True,
                            "message": "竞态请求已收敛到同一条恢复判定记录",
                            "entry": existing,
                            "created": False,
                            "duplicate": True,
                            "notification": self.notifier.find_by_decision(existing.get("判定编号")),
                        }
                    return {
                        "ok": False,
                        "message": (
                            f"指标版本已从 {expected} 变为 {version}，本次请求未写入；"
                            "请按存量版本重新取数后再判定"
                        ),
                        "entry": None,
                        "created": False,
                        "duplicate": False,
                    }

            key = self._sample_key(metric, sample_time, sample_id, empty)
            existing = self._find_terminal_by_key(key)
            if existing is not None:
                return {
                    "ok": True,
                    "message": "重复请求沿用已有恢复判定记录，未重复写入",
                    "entry": existing,
                    "created": False,
                    "duplicate": True,
                    "notification": self.notifier.find_by_decision(existing.get("判定编号")),
                }
            if empty and sample_id:
                # 同一空采样即使换了上报时间也只对应一条终态记录，避免重复累计。
                duplicate = self._find_decision_for_sample(metric.get("指标编号"), sample_id)
                if duplicate is not None:
                    return {
                        "ok": True,
                        "message": "空采样编号此前已判定过，沿用已有终态记录，不重复累计",
                        "entry": duplicate,
                        "created": False,
                        "duplicate": True,
                        "notification": self.notifier.find_by_decision(duplicate.get("判定编号")),
                    }

            previous_code = level_code(metric.get("级别代码") or metric.get("反压级别"), default="severe") or "severe"
            previous_name = level_name(previous_code)
            resume = self._resume_plan(metric, values.get("未完成算子") or values.get("算子清单"))
            metric["未完成算子"] = resume["未完成算子"]
            metric["恢复起点算子"] = resume["恢复起点算子"]
            metric["最后一个未完成算子"] = resume["未完成算子"][-1] if resume["未完成算子"] else None
            if resume["恢复起点算子"]:
                resume_mode = "从未完成算子继续"
            elif resume["算子清单"]:
                resume_mode = "算子已全部完成，无续跑"
            else:
                resume_mode = "无算子清单"
            record: dict[str, Any] = {
                "id": self._next_decision_id,
                "判定编号": f"RECD-{self._next_decision_id:04d}",
                "指标记录ID": int(metric.get("id", 0)),
                "指标编号": metric.get("指标编号"),
                "流作业名": metric.get("流作业名"),
                "采样时间": sample_time,
                "样本编号": sample_id,
                "上一级别": previous_name,
                "上一级别代码": previous_code,
                "水位下限": format_percent(BUFFER_FLOOR_PERCENT),
                "延迟下限": format_seconds(LAG_FLOOR_SECONDS),
                "延迟回落要求": "低于上次采样",
                "采样中断": interrupted,
                "未完成算子": resume["未完成算子"],
                "恢复起点算子": resume["恢复起点算子"],
                "已跳过算子": resume["已跳过算子"],
                "版本": version,
                "幂等键": key,
                "判定来源": source,
                "终态": True,
                "创建时间": now_text(),
            }

            duplicate_empty = bool(
                empty
                and sample_id
                and sample_id in {str(item) for item in (metric.get("空采样样本") or [])}
            )

            if empty:
                if duplicate_empty:
                    count = int(metric.get("连续空采样次数") or 0)
                    reason = (
                        f"{sample_id} 为空且此前已记录（连续空采样 {count} 次），"
                        "沿用已有空采样判定，不重复累计"
                    )
                    duplicate = True
                else:
                    count = int(metric.get("连续空采样次数") or 0) + 1
                    samples = list(metric.get("空采样样本") or [])
                    if sample_id and sample_id not in samples:
                        samples.append(sample_id)
                    metric["空采样样本"] = samples
                    metric["连续空采样次数"] = count
                    reason = (
                        f"{sample_id} 为空：未返回缓冲区水位与消费延迟，无法校验恢复条件"
                        if sample_id
                        else "本次采样为空：未返回缓冲区水位与消费延迟，无法校验恢复条件"
                    )
                    duplicate = False
                metric["最近采样为空"] = True
                metric["处理状态"] = "空采样待补采"
                metric["pending"] = True
                metric["abnormal"] = previous_code in {"mild", "severe"}
                record.update(
                    {
                        "判定结果": f"维持{previous_name}",
                        "判定结果代码": "not_recovered",
                        "保留级别": previous_code,
                        "保留级别名称": previous_name,
                        "恢复判定": False,
                        "空采样": True,
                        "空采样原因": reason,
                        "重复空采样": duplicate,
                        "连续空采样次数": int(metric.get("连续空采样次数") or 0),
                        "缓冲区水位": None,
                        "消费延迟": None,
                        "上次消费延迟": metric.get("消费延迟"),
                        "缓冲水位达标": False,
                        "延迟达标": False,
                        "延迟回落达标": False,
                        "判定后级别": previous_name,
                        "判定后级别代码": previous_code,
                        "通知抑制结果": "已抑制",
                        "通知抑制原因": "空采样未取得水位与延迟，不能按已恢复通知",
                        "通知动作": "抑制通知",
                    }
                )
                message = (
                    f"{metric.get('指标编号')} 空采样（{sample_id or '无样本编号'}），"
                    f"连续 {record['连续空采样次数']} 次，维持{previous_name}，通知已抑制"
                )
            else:
                buffer_value = parse_percent(buffer_raw)
                lag_value = parse_seconds(lag_raw)
                if buffer_value is None or lag_value is None:
                    return {
                        "ok": False,
                        "message": "缓冲区水位或消费延迟无法解析，本次不写恢复判定记录",
                        "entry": None,
                        "created": False,
                        "duplicate": False,
                    }
                previous_lag_raw = metric.get("上次消费延迟") or metric.get("消费延迟")
                previous_lag = parse_seconds(previous_lag_raw)
                buffer_ok = buffer_value <= BUFFER_FLOOR_PERCENT
                lag_ok = lag_value <= LAG_FLOOR_SECONDS
                lag_fallback_ok = previous_lag is None or lag_value < previous_lag
                recovered = bool(buffer_ok and lag_ok and lag_fallback_ok) and not empty

                metric["上次消费延迟"] = previous_lag_raw
                metric["缓冲区水位"] = str(buffer_raw)
                metric["消费延迟"] = str(lag_raw)
                metric["采样时间"] = sample_time
                metric["最近采样为空"] = False
                metric["连续空采样次数"] = 0
                metric["空采样样本"] = []
                if recovered:
                    metric["级别代码"] = "recovered"
                    metric["反压级别"] = LEVEL_NAMES["recovered"]
                    metric["status"] = LEVEL_NAMES["recovered"]
                    metric["处理状态"] = "已恢复"
                    metric["pending"] = False
                    metric["abnormal"] = False
                else:
                    metric["级别代码"] = previous_code
                    metric["反压级别"] = previous_name
                    metric["status"] = previous_name
                    metric["处理状态"] = "反压持续"
                    metric["pending"] = previous_code in {"mild", "severe"}
                    metric["abnormal"] = previous_code == "severe"

                failures: list[str] = []
                if not buffer_ok:
                    failures.append(
                        f"水位 {format_percent(buffer_value)} 高于下限 {format_percent(BUFFER_FLOOR_PERCENT)}"
                    )
                if not lag_ok:
                    failures.append(
                        f"延迟 {format_seconds(lag_value)} 高于下限 {format_seconds(LAG_FLOOR_SECONDS)}"
                    )
                if not lag_fallback_ok:
                    failures.append(
                        f"延迟 {format_seconds(lag_value)} 未较上次 {format_seconds(previous_lag or 0.0)} 回落"
                    )
                suppression_reason = "；".join(failures)
                record.update(
                    {
                        "判定结果": "已恢复" if recovered else f"维持{previous_name}",
                        "判定结果代码": "recovered" if recovered else "not_recovered",
                        "保留级别": "recovered" if recovered else previous_code,
                        "保留级别名称": LEVEL_NAMES["recovered"] if recovered else previous_name,
                        "恢复判定": recovered,
                        "空采样": False,
                        "空采样原因": "无（采样有效）",
                        "重复空采样": False,
                        "连续空采样次数": 0,
                        "缓冲区水位": str(buffer_raw),
                        "消费延迟": str(lag_raw),
                        "上次消费延迟": previous_lag_raw,
                        "缓冲水位达标": buffer_ok,
                        "延迟达标": lag_ok,
                        "延迟回落达标": lag_fallback_ok,
                        "缓冲区水位数值": buffer_value,
                        "消费延迟数值": lag_value,
                        "判定后级别": LEVEL_NAMES["recovered"] if recovered else previous_name,
                        "判定后级别代码": "recovered" if recovered else previous_code,
                        # 恢复判定的通知由调用方显式触发，记录生成阶段一律先抑制，
                        # 避免“判定通过”被误当成“已经通知”。
                        "通知抑制结果": "已抑制",
                        "通知抑制原因": "" if recovered else suppression_reason,
                        "通知动作": "抑制通知",
                        "待发送恢复通知": recovered,
                    }
                )
                if recovered:
                    message = (
                        f"{metric.get('指标编号')} 水位 {format_percent(buffer_value)}、"
                        f"延迟 {format_seconds(lag_value)} 均达标且回落，判定已恢复"
                    )
                else:
                    message = (
                        f"{metric.get('指标编号')} 未满足恢复条件（{suppression_reason}），"
                        f"维持{previous_name}，通知已抑制"
                    )

            record["恢复方式"] = resume_mode
            record["续跑说明"] = (
                f"从 {record['恢复起点算子']} 继续，跳过已完成的 {('、'.join(record['已跳过算子']) or '无')}"
                if record["恢复起点算子"]
                else "没有未完成算子，不需要续跑"
            )

            self._decisions.append(record)
            self._next_decision_id += 1
            notification = self.notifier.receive(record)
            return {
                "ok": True,
                "message": message,
                "entry": record,
                "created": True,
                "duplicate": bool(record.get("重复空采样")),
                "notification": notification,
            }

    # ------------------------------------------------------------------
    # 判定记录查询：筛选、分页、检索、定位都走同一份稳定排序
    # ------------------------------------------------------------------
    def list_all_decisions(self) -> list[dict[str, Any]]:
        with self._lock:
            self._repair_decisions_locked()
            return [dict(row) for row in sorted(self._decisions, key=lambda row: int(row.get("id", 0)))]

    def list_decisions(
        self,
        *,
        keyword: str | None = None,
        metric_no: str | None = None,
        result: str | None = None,
        recovered: bool | None = None,
        empty_sample: bool | None = None,
        suppress: str | None = None,
        sample_from: str | None = None,
        sample_to: str | None = None,
        version: int | None = None,
        decision_id: int | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self.list_all_decisions()
        if decision_id is not None:
            rows = [row for row in rows if int(row.get("id", 0)) == int(decision_id)]
        if keyword:
            needle = str(keyword).strip()
            rows = [
                row
                for row in rows
                if any(
                    needle in str(row.get(field, ""))
                    for field in ("判定编号", "指标编号", "流作业名", "样本编号", "幂等键")
                )
            ]
        if metric_no:
            rows = [row for row in rows if str(row.get("指标编号")) == str(metric_no).strip()]
        if result:
            needle = str(result).strip()
            rows = [
                row
                for row in rows
                if needle in str(row.get("判定结果", "")) or needle == str(row.get("判定结果代码", ""))
            ]
        if recovered is not None:
            rows = [row for row in rows if bool(row.get("恢复判定")) is recovered]
        if empty_sample is not None:
            rows = [row for row in rows if bool(row.get("空采样")) is empty_sample]
        if suppress:
            needle = str(suppress).strip()
            rows = [row for row in rows if str(row.get("通知抑制结果", "")) == needle]
        if sample_from:
            rows = [row for row in rows if _time_key(row.get("采样时间")) >= _time_key(sample_from)]
        if sample_to:
            rows = [row for row in rows if _time_key(row.get("采样时间")) <= _time_key(sample_to)]
        if version is not None:
            rows = [row for row in rows if int(row.get("版本", 0)) == int(version)]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [dict(row) for row in rows[start:start + size]], total

    def get_decision(self, decision_id: int | str) -> dict[str, Any] | None:
        with self._lock:
            self._repair_decisions_locked()
            text = str(decision_id).strip()
            try:
                numeric_id = int(text)
            except (TypeError, ValueError):
                numeric_id = None
            if numeric_id is not None:
                for row in self._decisions:
                    if int(row.get("id", 0)) == numeric_id:
                        return dict(row)
            for row in self._decisions:
                if str(row.get("判定编号")) == text:
                    return dict(row)
        return None

    def get_decision_by_code(self, code: str) -> dict[str, Any] | None:
        with self._lock:
            self._repair_decisions_locked()
            for row in self._decisions:
                if str(row.get("判定编号")) == str(code):
                    return dict(row)
        return None

    def repair_decisions(self) -> dict[str, Any]:
        """按现有取数逻辑修正错位记录；只补字段、不推进版本、不改变判定结果。"""
        with self._lock:
            fixed = self._repair_decisions_locked(return_fixed=True)
            return {"fixed": len(fixed), "records": fixed}

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _resolve_metric(self, values: Mapping[str, Any]) -> dict[str, Any] | None:
        entry_id = values.get("指标记录ID", values.get("id"))
        if entry_id not in (None, ""):
            try:
                metric = store.find(MODULE, int(entry_id))
            except (TypeError, ValueError):
                metric = None
            if metric is not None:
                return metric
        metric_no = str(values.get("指标编号") or "").strip()
        if metric_no:
            for row in store.rows(MODULE):
                if str(row.get("指标编号")) == metric_no:
                    return row
        return None

    def _sample_key(
        self,
        metric: Mapping[str, Any],
        sample_time: str,
        sample_id: str | None,
        empty: bool,
    ) -> str:
        """判定记录的幂等键：同一指标、同一采样时间、同一样本只允许一个终态。"""
        suffix = sample_id or ("empty" if empty else "sampled")
        return f"{metric.get('指标编号')}|{sample_time}|{suffix}"

    def _find_decision_for_sample(self, metric_no: Any, sample_id: str) -> dict[str, Any] | None:
        """按样本编号找已有的终态判定；空采样重复上报时直接沿用。"""
        self._repair_decisions_locked()
        for row in self._decisions:
            if str(row.get("指标编号")) == str(metric_no) and str(row.get("样本编号")) == str(sample_id):
                return dict(row)
        return None

    def _find_terminal_by_key(self, key: str) -> dict[str, Any] | None:
        self._repair_decisions_locked()
        for row in self._decisions:
            if str(row.get("幂等键")) == key:
                return dict(row)
        return None

    def _resume_plan(
        self, metric: Mapping[str, Any], override: Any = None
    ) -> dict[str, Any]:
        operators = override if override not in (None, "") else metric.get("算子清单")
        if isinstance(operators, str):
            names = [part.strip() for part in re.split(r"[,，;；]", operators) if part.strip()]
            operators = [{"名称": name, "状态": "未完成"} for name in names]
        normalized: list[dict[str, str]] = []
        for item in list(operators or []):
            if isinstance(item, Mapping):
                name = str(item.get("名称") or item.get("name") or "").strip()
                status = str(item.get("状态") or item.get("status") or "").strip()
            else:
                name = str(item).strip()
                status = ""
            if name:
                normalized.append({"名称": name, "状态": status or "未完成"})
        unfinished = [item["名称"] for item in normalized if item["状态"] not in {"已完成", "done", "完成"}]
        completed = [item["名称"] for item in normalized if item["状态"] in {"已完成", "done", "完成"}]
        resume_at = unfinished[0] if unfinished else None
        skipped: list[str] = []
        if resume_at is not None:
            # 采样中断后只从未完成算子继续；起点之前的已完成算子记为已跳过，不重跑。
            for item in normalized:
                if item["名称"] == resume_at:
                    break
                if item["状态"] in {"已完成", "done", "完成"}:
                    skipped.append(item["名称"])
        return {
            "未完成算子": unfinished,
            "恢复起点算子": resume_at,
            "已跳过算子": skipped,
            "算子清单": normalized,
        }

    def _repair_metric(self, metric: dict[str, Any]) -> None:
        code = level_code(metric.get("级别代码"))
        if code is None:
            code = level_code(metric.get("反压级别")) or level_code(metric.get("status")) or "severe"
            metric["级别代码"] = code
        name = LEVEL_NAMES[code]
        if metric.get("反压级别") != name:
            metric["反压级别"] = name
        if metric.get("status") != name:
            metric["status"] = name
        metric.setdefault("version", 1)
        metric.setdefault("连续空采样次数", 0)
        metric.setdefault("空采样样本", [])
        metric.setdefault("最近采样为空", False)

    def _repair_decisions_locked(self, *, return_fixed: bool = False) -> list[dict[str, Any]]:
        fixed: list[dict[str, Any]] = []
        metrics = {str(row.get("指标编号")): row for row in store.rows(MODULE)}
        for record in self._decisions:
            before = dict(record)
            metric_no = str(record.get("指标编号") or "").strip()
            metric = metrics.get(metric_no)
            if metric is not None:
                self._repair_metric(metric)
                if int(record.get("指标记录ID") or 0) != int(metric.get("id", 0)):
                    record["指标记录ID"] = int(metric.get("id", 0))
                if record.get("流作业名") != metric.get("流作业名"):
                    record["流作业名"] = metric.get("流作业名")
                if not record.get("上一级别代码"):
                    record["上一级别代码"] = metric.get("级别代码")
                if not record.get("上一级别"):
                    record["上一级别"] = metric.get("反压级别")
                if not record.get("版本"):
                    record["版本"] = int(metric.get("version") or 1)
            if record.get("空采样") and record.get("通知抑制结果") != "已抑制":
                record["通知抑制结果"] = "已抑制"
                record["通知动作"] = "抑制通知"
                if not record.get("通知抑制原因"):
                    record["通知抑制原因"] = "空采样未取得水位与延迟，不能按已恢复通知"
            if record.get("恢复判定") and record.get("空采样"):
                record["恢复判定"] = False
                record["判定结果"] = f"维持{record.get('上一级别') or '严重反压'}"
                record["判定结果代码"] = "not_recovered"
            if record.get("恢复判定") and record.get("通知抑制结果") == "已抑制":
                record["通知抑制结果"] = "允许通知"
                record["通知抑制原因"] = ""
                record["通知动作"] = "发送恢复通知"
            if before != record:
                fixed.append(dict(record))
        if return_fixed:
            return fixed
        return []
