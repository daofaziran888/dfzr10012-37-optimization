"""反压恢复判定业务规则测试：空采样去重、双阈值恢复、续跑算子与通知收敛。

运行：cd backend && .venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import copy
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from app.seed import SEED_NOTIFICATION_RECORDS, SEED_RECOVERY_DECISIONS, SEED_ROWS
from app.services.backpress import BackpressService
from app.store import store


class BackpressRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._snapshot = copy.deepcopy(store._tables)
        store._tables["backpress"] = [copy.deepcopy(row) for row in SEED_ROWS["backpress"]]
        self.service = BackpressService()
        self.service._decisions = [copy.deepcopy(row) for row in SEED_RECOVERY_DECISIONS]
        self.service._next_decision_id = 3
        self.service.notifier._records = [copy.deepcopy(row) for row in SEED_NOTIFICATION_RECORDS]
        self.service.notifier._next_id = 3

    def tearDown(self) -> None:
        store._tables = self._snapshot

    # ------------------------------------------------------------------
    # 验收样例
    # ------------------------------------------------------------------
    def test_back_0002_empty_sample_keeps_severe(self) -> None:
        metric = self.service.get_entry(2)
        self.assertEqual(metric["缓冲区水位"], "40%")
        self.assertEqual(metric["消费延迟"], "6s")
        result = self.service.evaluate_recovery(
            {
                "指标编号": "BACK-0002",
                "样本编号": "sample-37",
                "采样时间": "2026-09-16T10:20:00+08:00",
                "采样结果": "empty",
                "version": 2,
            }
        )
        self.assertTrue(result["ok"])
        record = result["entry"]
        self.assertEqual(record["上一级别"], "严重反压")
        self.assertEqual(record["判定后级别"], "严重反压")
        self.assertEqual(record["判定结果"], "维持严重反压")
        self.assertFalse(record["恢复判定"])
        self.assertEqual(record["通知抑制结果"], "已抑制")
        self.assertIn("sample-37", record["空采样原因"])
        self.assertEqual(self.service.get_entry(2)["status"], "严重反压")

    def test_back_0003_notification_not_closed_early(self) -> None:
        result = self.service.evaluate_recovery(
            {
                "指标编号": "BACK-0003",
                "样本编号": "sample-140",
                "采样时间": "2026-09-17T10:00:00+08:00",
                "缓冲区水位": "28%",
                "消费延迟": "9s",
                "version": 3,
            }
        )
        record = result["entry"]
        self.assertEqual(record["判定后级别"], "严重反压")
        self.assertEqual(record["通知抑制结果"], "已抑制")
        self.assertFalse(record["恢复判定"])
        self.assertEqual(result["notification"]["record"]["处理状态"], "已抑制")
        self.assertEqual(self.service.get_entry(3)["status"], "严重反压")
        # 标记恢复动作同样不能绕过恢复条件。
        entry, message = self.service.run_action(3, "标记恢复")
        self.assertIsNotNone(entry)
        self.assertIn("维持严重反压", message)
        self.assertEqual(self.service.get_entry(3)["status"], "严重反压")

    # ------------------------------------------------------------------
    # 空采样重复处理
    # ------------------------------------------------------------------
    def test_repeated_empty_sample_does_not_double_count(self) -> None:
        payload = {
            "指标编号": "BACK-0002",
            "样本编号": "sample-37",
            "采样时间": "2026-09-16T10:20:00+08:00",
            "采样结果": "empty",
            "version": 2,
        }
        first = self.service.evaluate_recovery(payload)
        second = self.service.evaluate_recovery(payload)
        self.assertTrue(first["created"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["entry"]["判定编号"], second["entry"]["判定编号"])
        self.assertEqual(self.service.get_entry(2)["连续空采样次数"], 1)
        # 换成新的空采样样本才累计
        third = self.service.evaluate_recovery(
            {**payload, "样本编号": "sample-38", "采样时间": "2026-09-16T10:31:00+08:00"}
        )
        self.assertFalse(third["duplicate"])
        self.assertEqual(self.service.get_entry(2)["连续空采样次数"], 2)

    def test_empty_sample_without_values_is_not_recovery(self) -> None:
        result = self.service.evaluate_recovery(
            {
                "指标编号": "BACK-0001",
                "样本编号": "sample-x",
                "采样时间": "2026-09-19T00:00:00+08:00",
                "缓冲区水位": "",
                "消费延迟": "",
            }
        )
        self.assertTrue(result["entry"]["空采样"])
        self.assertFalse(result["entry"]["恢复判定"])
        self.assertEqual(result["entry"]["判定后级别"], "正常")
        self.assertEqual(result["notification"]["record"]["处理状态"], "已抑制")

    # ------------------------------------------------------------------
    # 恢复条件：水位下限 + 延迟回落 + 延迟下限
    # ------------------------------------------------------------------
    def test_recovery_requires_both_watermarks(self) -> None:
        result = self.service.evaluate_recovery(
            {
                "指标编号": "BACK-0001",
                "样本编号": "sample-200",
                "采样时间": "2026-09-18T08:00:00+08:00",
                "缓冲区水位": "18%",
                "消费延迟": "0.8s",
                "version": 1,
            }
        )
        record = result["entry"]
        self.assertTrue(record["恢复判定"])
        self.assertEqual(record["判定后级别"], "已恢复")
        self.assertEqual(self.service.get_entry(1)["status"], "已恢复")
        # 判定通过只代表允许发送，通知状态要消费侧落终态后才成立
        self.assertEqual(record["通知抑制结果"], "已抑制")
        self.assertTrue(record["待发送恢复通知"])
        self.assertEqual(result["notification"]["record"]["处理状态"], "已抑制")

    def test_lag_must_fall_back(self) -> None:
        result = self.service.evaluate_recovery(
            {
                "指标编号": "BACK-0002",
                "样本编号": "sample-220",
                "采样时间": "2026-09-16T12:00:00+08:00",
                "缓冲区水位": "20%",
                "消费延迟": "9s",  # 高于上次 6s，不回落
                "version": 2,
            }
        )
        record = result["entry"]
        self.assertFalse(record["恢复判定"])
        self.assertFalse(record["延迟回落达标"])
        self.assertEqual(record["判定后级别"], "严重反压")

    # ------------------------------------------------------------------
    # 续跑：只从未完成算子继续
    # ------------------------------------------------------------------
    def test_resume_only_from_unfinished_operator(self) -> None:
        result = self.service.evaluate_recovery(
            {
                "指标编号": "BACK-0003",
                "样本编号": "sample-150",
                "采样时间": "2026-09-17T10:10:00+08:00",
                "缓冲区水位": "20%",
                "消费延迟": "2s",
                "version": 3,
            }
        )
        record = result["entry"]
        self.assertEqual(record["恢复方式"], "从未完成算子继续")
        self.assertEqual(record["恢复起点算子"], "sink-writer")
        self.assertEqual(record["已跳过算子"], ["kafka-source", "reconcile-join"])
        self.assertEqual(record["未完成算子"], ["sink-writer"])

    # ------------------------------------------------------------------
    # 幂等、版本与并发
    # ------------------------------------------------------------------
    def test_stale_version_rejected_until_record_exists(self) -> None:
        stale = self.service.evaluate_recovery(
            {
                "指标编号": "BACK-0002",
                "样本编号": "s1",
                "采样时间": "2026-09-20T00:00:00+08:00",
                "缓冲区水位": "20%",
                "消费延迟": "2s",
                "version": 1,
            }
        )
        self.assertFalse(stale["ok"])
        fresh = self.service.evaluate_recovery(
            {
                "指标编号": "BACK-0002",
                "样本编号": "s1",
                "采样时间": "2026-09-20T00:00:00+08:00",
                "缓冲区水位": "20%",
                "消费延迟": "2s",
                "version": 2,
            }
        )
        self.assertTrue(fresh["ok"])
        converged = self.service.evaluate_recovery(
            {
                "指标编号": "BACK-0002",
                "样本编号": "s1",
                "采样时间": "2026-09-20T00:00:00+08:00",
                "缓冲区水位": "20%",
                "消费延迟": "2s",
                "version": 1,
            }
        )
        self.assertTrue(converged["ok"])
        self.assertTrue(converged["duplicate"])
        self.assertEqual(converged["entry"]["判定编号"], fresh["entry"]["判定编号"])

    def test_concurrent_requests_keep_single_terminal_record(self) -> None:
        payload = {
            "指标编号": "BACK-0002",
            "样本编号": "sample-77",
            "采样时间": "2026-09-16T11:00:00+08:00",
            "采样结果": "empty",
            "version": 2,
        }

        def call(_: int) -> dict:
            return self.service.evaluate_recovery(payload)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(call, range(16)))
        self.assertEqual(sum(1 for item in results if item["created"]), 1)
        self.assertEqual(len({item["entry"]["判定编号"] for item in results}), 1)
        self.assertEqual(self.service.get_entry(2)["连续空采样次数"], 1)
        report = self.service.notifier.conflict_report()
        self.assertTrue(report["consistent"])

    def test_notification_consumer_dedupes_same_decision(self) -> None:
        record = self.service.get_decision("RECD-0001")
        first = self.service.notifier.receive(record)
        second = self.service.notifier.receive(record)
        self.assertTrue(first["deduplicated"])
        self.assertTrue(second["deduplicated"])
        consumed = [row for row in self.service.notifier.list_records(size=100)[0] if row["判定编号"] == "RECD-0001"]
        self.assertEqual(len(consumed), 1)

    # ------------------------------------------------------------------
    # 查询与错位修正
    # ------------------------------------------------------------------
    def test_decision_records_filter_and_paginate_stably(self) -> None:
        items, total = self.service.list_decisions(metric_no="BACK-0003", page=1, size=1)
        self.assertEqual(total, 2)
        self.assertEqual(items[0]["判定编号"], "RECD-0001")
        page2, _ = self.service.list_decisions(metric_no="BACK-0003", page=2, size=1)
        self.assertEqual(page2[0]["判定编号"], "RECD-0002")
        empty_items, empty_total = self.service.list_decisions(empty_sample=True)
        self.assertEqual(empty_total, 1)
        self.assertEqual(empty_items[0]["判定编号"], "RECD-0002")
        self.assertEqual(self.service.get_decision("RECD-0001")["上一级别"], "轻微反压")

    def test_repair_fixes_misaligned_metric_reference(self) -> None:
        misaligned = {
            **self.service.get_decision("RECD-0002"),
            "指标记录ID": 1,
            "流作业名": "错位的作业名",
            "通知抑制结果": "允许通知",
            "通知动作": "发送恢复通知",
        }
        self.service._decisions = [row for row in self.service._decisions if row["判定编号"] != "RECD-0002"]
        self.service._decisions.append(misaligned)
        fixed = self.service.repair_decisions()
        self.assertEqual(fixed["fixed"], 1)
        record = self.service.get_decision("RECD-0002")
        self.assertEqual(record["指标记录ID"], 3)  # 按现有取数逻辑回到 BACK-0003 的记录ID
        self.assertEqual(record["流作业名"], "结算对账实时作业")
        self.assertEqual(record["版本"], 3)  # 沿用存量版本
        self.assertEqual(record["通知抑制结果"], "已抑制")  # 空采样不能放行通知
        self.assertFalse(record["恢复判定"])
        self.assertEqual(record["判定结果"], "维持严重反压")


if __name__ == "__main__":
    unittest.main()
