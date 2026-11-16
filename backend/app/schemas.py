"""接口出入参模型：列表分页、动作结果与各模块的明细结构。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    """登记或修改一条业务记录时提交的字段集合。"""

    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None
    # 前端动作按钮按 { action: "..." } 直接提交，这里同时接受顶层写法。
    action: str | None = None


class RecoveryDecisionPayload(BaseModel):
    """恢复判定请求：取数时读取的反压指标水位、消费延迟与采样时间。

    指标可以用 `指标记录ID` 或 `指标编号` 定位；`样本编号` 用于空采样去重，
    `version` 用于沿用存量版本、收敛竞态请求。
    """

    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class RecoveryDecisionResult(BaseModel):
    ok: bool
    message: str
    created: bool = False
    duplicate: bool = False
    entry: dict[str, Any] | None = None
    notification: dict[str, Any] | None = None



class StreaminEntry(BaseModel):
    """流数据源明细结构。"""

    field_0: str | None = None  # 源编号
    field_1: str | None = None  # 源名称
    field_2: str | None = None  # 消息队列
    field_3: str | None = None  # 主题名称
    field_4: str | None = None  # 分区数量
    field_5: str | None = None  # 消费组
    field_6: str | None = None  # 起始位点
    field_7: str | None = None  # 源状态

class OperatorEntry(BaseModel):
    """算子定义明细结构。"""

    field_0: str | None = None  # 算子编号
    field_1: str | None = None  # 算子名称
    field_2: str | None = None  # 输入流
    field_3: str | None = None  # 处理逻辑
    field_4: str | None = None  # 输出流
    field_5: str | None = None  # 并行度
    field_6: str | None = None  # 算子状态
    field_7: str | None = None  # 最近执行

class WindowEntry(BaseModel):
    """窗口定义明细结构。"""

    field_0: str | None = None  # 窗口编号
    field_1: str | None = None  # 数据流名称
    field_2: str | None = None  # 窗口类型
    field_3: str | None = None  # 窗口长度
    field_4: str | None = None  # 滑动步长
    field_5: str | None = None  # 延迟容忍
    field_6: str | None = None  # 触发策略
    field_7: str | None = None  # 窗口状态

class StateEntry(BaseModel):
    """后端配置明细结构。"""

    field_0: str | None = None  # 配置编号
    field_1: str | None = None  # 后端类型
    field_2: str | None = None  # 存储路径
    field_3: str | None = None  # 状态大小
    field_4: str | None = None  # 过期策略
    field_5: str | None = None  # 清理间隔
    field_6: str | None = None  # 快照频率
    field_7: str | None = None  # 配置状态

class CheckpointEntry(BaseModel):
    """检查点记录明细结构。"""

    field_0: str | None = None  # 记录编号
    field_1: str | None = None  # 流作业名
    field_2: str | None = None  # 检查点ID
    field_3: str | None = None  # 状态大小
    field_4: str | None = None  # 保存路径
    field_5: str | None = None  # 创建时间
    field_6: str | None = None  # 恢复耗时
    field_7: str | None = None  # 记录状态

class BackpressEntry(BaseModel):
    """反压指标明细结构。"""

    field_0: str | None = None  # 指标编号
    field_1: str | None = None  # 流作业名
    field_2: str | None = None  # 反压级别
    field_3: str | None = None  # 阻塞算子
    field_4: str | None = None  # 缓冲区水位
    field_5: str | None = None  # 消费延迟
    field_6: str | None = None  # 采样时间
    field_7: str | None = None  # 处理状态

class LatencyEntry(BaseModel):
    """延迟记录明细结构。"""

    field_0: str | None = None  # 记录编号
    field_1: str | None = None  # 流作业名
    field_2: str | None = None  # 端到端延迟
    field_3: str | None = None  # 处理延迟
    field_4: str | None = None  # 网络延迟
    field_5: str | None = None  # 延迟阈值
    field_6: str | None = None  # 超标次数
    field_7: str | None = None  # 记录时间

class JobopsEntry(BaseModel):
    """作业实例明细结构。"""

    field_0: str | None = None  # 实例编号
    field_1: str | None = None  # 作业名称
    field_2: str | None = None  # 运行节点
    field_3: str | None = None  # 启动时间
    field_4: str | None = None  # 运行时长
    field_5: str | None = None  # 异常次数
    field_6: str | None = None  # 重启次数
    field_7: str | None = None  # 实例状态

class SinkEntry(BaseModel):
    """输出配置明细结构。"""

    field_0: str | None = None  # 配置编号
    field_1: str | None = None  # 输出类型
    field_2: str | None = None  # 目标地址
    field_3: str | None = None  # 写入模式
    field_4: str | None = None  # 批处理大小
    field_5: str | None = None  # 刷新间隔
    field_6: str | None = None  # 重试策略
    field_7: str | None = None  # 配置状态

class SchemaEntry(BaseModel):
    """格式定义明细结构。"""

    field_0: str | None = None  # 定义编号
    field_1: str | None = None  # 流名称
    field_2: str | None = None  # 字段名称
    field_3: str | None = None  # 字段类型
    field_4: str | None = None  # 默认值
    field_5: str | None = None  # 可空标识
    field_6: str | None = None  # 格式版本
    field_7: str | None = None  # 定义状态

class WatermarkEntry(BaseModel):
    """水位线配置明细结构。"""

    field_0: str | None = None  # 配置编号
    field_1: str | None = None  # 流名称
    field_2: str | None = None  # 时间字段
    field_3: str | None = None  # 延迟容忍
    field_4: str | None = None  # 空闲超时
    field_5: str | None = None  # 发射间隔
    field_6: str | None = None  # 对齐策略
    field_7: str | None = None  # 配置状态

class ParallelEntry(BaseModel):
    """调优记录明细结构。"""

    field_0: str | None = None  # 记录编号
    field_1: str | None = None  # 流算子名
    field_2: str | None = None  # 当前并行度
    field_3: str | None = None  # 建议并行度
    field_4: str | None = None  # 吞吐提升
    field_5: str | None = None  # 资源成本
    field_6: str | None = None  # 调优时间
    field_7: str | None = None  # 记录状态

class MetricEntry(BaseModel):
    """指标采集器明细结构。"""

    field_0: str | None = None  # 采集器编号
    field_1: str | None = None  # 作业名称
    field_2: str | None = None  # 指标名称
    field_3: str | None = None  # 采集间隔
    field_4: str | None = None  # 聚合方式
    field_5: str | None = None  # 保留天数
    field_6: str | None = None  # 最近采集
    field_7: str | None = None  # 采集器状态

class AlarmEntry(BaseModel):
    """告警规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 监控指标
    field_2: str | None = None  # 告警条件
    field_3: str | None = None  # 告警级别
    field_4: str | None = None  # 通知渠道
    field_5: str | None = None  # 静默周期
    field_6: str | None = None  # 升级策略
    field_7: str | None = None  # 规则状态

class UdfEntry(BaseModel):
    """函数定义明细结构。"""

    field_0: str | None = None  # 函数编号
    field_1: str | None = None  # 函数名称
    field_2: str | None = None  # 输入参数
    field_3: str | None = None  # 返回类型
    field_4: str | None = None  # 函数代码
    field_5: str | None = None  # 测试用例
    field_6: str | None = None  # 注册时间
    field_7: str | None = None  # 函数状态

class LogEntry(BaseModel):
    """日志记录明细结构。"""

    field_0: str | None = None  # 记录编号
    field_1: str | None = None  # 流作业名
    field_2: str | None = None  # 日志级别
    field_3: str | None = None  # 日志内容
    field_4: str | None = None  # 发生时间
    field_5: str | None = None  # 异常类型
    field_6: str | None = None  # 处理状态
    field_7: str | None = None  # 记录状态
