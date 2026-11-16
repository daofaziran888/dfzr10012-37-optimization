# 实时流数据处理中心

面向实时数据处理团队的流式计算与管理中心。覆盖流数据源接入、流处理算子编排、窗口计算配置、状态后端管理、检查点恢复、反压监控、延迟追踪与流作业运维的完整实时计算链路。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 流数据源接入 | `streamin` | 流数据源 | 源编号、源名称、消息队列 |
| 流处理算子 | `operator` | 算子定义 | 算子编号、算子名称、输入流 |
| 窗口计算配置 | `window` | 窗口定义 | 窗口编号、数据流名称、窗口类型 |
| 状态后端管理 | `state` | 后端配置 | 配置编号、后端类型、存储路径 |
| 检查点恢复 | `checkpoint` | 检查点记录 | 记录编号、流作业名、检查点ID |
| 反压监控面板 | `backpress` | 反压指标 | 指标编号、流作业名、反压级别 |
| 反压恢复判定 | `backpress/recovery` | 恢复判定记录 | 判定编号、上一级别、空采样原因、通知抑制结果 |
| 延迟追踪管理 | `latency` | 延迟记录 | 记录编号、流作业名、端到端延迟 |
| 流作业运维 | `jobops` | 作业实例 | 实例编号、作业名称、运行节点 |
| 流数据输出端 | `sink` | 输出配置 | 配置编号、输出类型、目标地址 |
| 流数据格式定义 | `schema` | 格式定义 | 定义编号、流名称、字段名称 |
| 水位线管理 | `watermark` | 水位线配置 | 配置编号、流名称、时间字段 |
| 并行度调优 | `parallel` | 调优记录 | 记录编号、流算子名、当前并行度 |
| 流作业指标采集 | `metric` | 指标采集器 | 采集器编号、作业名称、指标名称 |
| 流告警规则 | `alarm` | 告警规则 | 规则编号、监控指标、告警条件 |
| 自定义函数库 | `udf` | 函数定义 | 函数编号、函数名称、输入参数 |
| 流处理日志中心 | `log` | 日志记录 | 记录编号、流作业名、日志级别 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 反压恢复判定

后端接口在 `backend/app/routers/backpress.py`，规则在 `backend/app/services/backpress.py`
与 `backend/app/services/backpress_notify.py`，页面为 `/backpress/recovery`。

- `POST /api/backpress/recoveries`：读取反压指标的水位、消费延迟与采样时间，
  校验后写入恢复判定数据记录。同一“指标编号 + 采样时间 + 样本编号”只保留一个终态，
  并发的重复请求返回同一份记录。
- `GET /api/backpress/recoveries`：按判定编号、指标编号、空采样、通知抑制结果、
  采样时间区间、版本等条件筛选、检索、分页与定位，排序固定按记录 ID 升序。
- `POST /api/backpress/recoveries/repair`：按现有取数逻辑补正错位记录，
  沿用存量版本，不改判定结果。
- `GET/POST /api/backpress/notifications`：另一个业务入口按判定编号或幂等键接收
  同一份判定记录，消费侧维护自己的处理记录并只落一个终态。

恢复条件（三项同时满足才算恢复）：

1. 缓冲区水位不高于 `30%`；
2. 消费延迟不高于 `3s`；
3. 消费延迟低于上一次采样。

空采样或缺少水位/延迟时维持上一级别、累计连续空采样次数并抑制通知，
不能把“暂无结果”当成“已经恢复”。采样中断后判定记录给出恢复起点算子与已跳过算子，
只从未完成算子继续，已完成的算子不重跑。

测试：`cd backend && .venv/bin/python -m unittest discover -s tests -t . -v`


领域：数据工程
子方向：数仓与数据湖
