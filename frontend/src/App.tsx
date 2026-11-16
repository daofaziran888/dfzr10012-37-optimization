import { useState } from 'react'
import { Link, Route, Routes } from 'react-router-dom'

import Overview from './views/Overview'
import StreaminView from './views/streamin'
import OperatorView from './views/operator'
import WindowView from './views/window'
import StateView from './views/state'
import CheckpointView from './views/checkpoint'
import BackpressView from './views/backpress'
import BackpressRecoveryView from './views/backpress/recovery'
import LatencyView from './views/latency'
import JobopsView from './views/jobops'
import SinkView from './views/sink'
import SchemaView from './views/schema'
import WatermarkView from './views/watermark'
import ParallelView from './views/parallel'
import MetricView from './views/metric'
import AlarmView from './views/alarm'
import UdfView from './views/udf'
import LogView from './views/log'

const navItems = [{ label: "运营概览", path: "/" }, { label: "流数据源接入", path: "/streamin" }, { label: "流处理算子", path: "/operator" }, { label: "窗口计算配置", path: "/window" }, { label: "状态后端管理", path: "/state" }, { label: "检查点恢复", path: "/checkpoint" }, { label: "反压监控面板", path: "/backpress" }, { label: "反压恢复判定", path: "/backpress/recovery" }, { label: "延迟追踪管理", path: "/latency" }, { label: "流作业运维", path: "/jobops" }, { label: "流数据输出端", path: "/sink" }, { label: "流数据格式定义", path: "/schema" }, { label: "水位线管理", path: "/watermark" }, { label: "并行度调优", path: "/parallel" }, { label: "流作业指标采集", path: "/metric" }, { label: "流告警规则", path: "/alarm" }, { label: "自定义函数库", path: "/udf" }, { label: "流处理日志中心", path: "/log" }]

export default function App() {
  const [operator] = useState('值班管理员')

  return (
    <div className="app-shell">
      <aside className="app-side">
        <h1 className="app-title">实时流数据处理中心</h1>
        <nav className="nav-list">
          {navItems.map((item) => (
            <Link key={item.path} to={item.path} className="nav-item">
              {item.label}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="app-main">
        <header className="app-head">
          <span className="head-desc">面向实时数据处理团队的流式计算与管理中心。覆盖流数据源接入、流处理算子编排、窗口计算配置、状态后端管理、检查点恢复、反压监控、延迟追踪与流作业运维的完整实时计算链路。</span>
          <span className="head-user">当前值班：{operator}</span>
        </header>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/streamin" element={<StreaminView />} />
          <Route path="/operator" element={<OperatorView />} />
          <Route path="/window" element={<WindowView />} />
          <Route path="/state" element={<StateView />} />
          <Route path="/checkpoint" element={<CheckpointView />} />
          <Route path="/backpress" element={<BackpressView />} />
          <Route path="/backpress/recovery" element={<BackpressRecoveryView />} />
          <Route path="/latency" element={<LatencyView />} />
          <Route path="/jobops" element={<JobopsView />} />
          <Route path="/sink" element={<SinkView />} />
          <Route path="/schema" element={<SchemaView />} />
          <Route path="/watermark" element={<WatermarkView />} />
          <Route path="/parallel" element={<ParallelView />} />
          <Route path="/metric" element={<MetricView />} />
          <Route path="/alarm" element={<AlarmView />} />
          <Route path="/udf" element={<UdfView />} />
          <Route path="/log" element={<LogView />} />
        </Routes>
      </main>
    </div>
  )
}
