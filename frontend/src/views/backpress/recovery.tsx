import { useCallback, useEffect, useState } from 'react'

import { request } from '@/api/client'

const RECOVERY_ENDPOINT = '/api/backpress/recoveries'
const NOTIFICATION_ENDPOINT = '/api/backpress/notifications'

const decisionColumns = [
  "判定编号",
  "指标编号",
  "流作业名",
  "采样时间",
  "样本编号",
  "上一级别",
  "判定结果",
  "判定后级别",
  "空采样",
  "空采样原因",
  "通知抑制结果",
  "恢复方式",
  "恢复起点算子",
  "已跳过算子",
  "版本",
]

const notificationColumns = [
  "消费编号",
  "判定编号",
  "指标编号",
  "上一级别",
  "判定后级别",
  "空采样",
  "通知抑制结果",
  "通知动作",
  "处理状态",
  "接收时间",
]

type Row = Record<string, unknown>

function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.length ? value.join('、') : '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

export default function BackpressRecoveryView() {
  const [decisions, setDecisions] = useState<Row[]>([])
  const [decisionTotal, setDecisionTotal] = useState(0)
  const [notifications, setNotifications] = useState<Row[]>([])
  const [notificationTotal, setNotificationTotal] = useState(0)
  const [conflicts, setConflicts] = useState<string>('')
  const [errorMessage, setErrorMessage] = useState('')
  const [successMessage, setSuccessMessage] = useState('')
  const [page, setPage] = useState(1)
  const [size] = useState(20)
  const [filters, setFilters] = useState<Record<string, string>>({})

  const loadNotifications = useCallback(async () => {
    const response = await request(`${NOTIFICATION_ENDPOINT}?size=50`)
    if (!response.ok) throw new Error('通知处理记录读取失败')
    const payload = await response.json()
    setNotifications(payload.items ?? [])
    setNotificationTotal(payload.total ?? 0)
    const check = await request(`${NOTIFICATION_ENDPOINT}/conflicts`)
    if (check.ok) {
      const report = await check.json()
      setConflicts(
        report.consistent
          ? `判定记录与通知处理记录一致（核对 ${report.checked} 条）`
          : `发现 ${report.conflicts?.length ?? 0} 条核对冲突`,
      )
    }
  }, [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams({ ...filters, page: String(page), size: String(size) })
    try {
      const response = await request(`${RECOVERY_ENDPOINT}?${query.toString()}`)
      if (!response.ok) throw new Error('恢复判定记录读取失败')
      const payload = await response.json()
      setDecisions(payload.items ?? [])
      setDecisionTotal(payload.total ?? 0)
      await loadNotifications()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '恢复判定记录读取失败')
    }
  }, [filters, page, size, loadNotifications])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runRepair() {
    setErrorMessage('')
    setSuccessMessage('')
    try {
      const response = await request(`${RECOVERY_ENDPOINT}/repair`, { method: 'POST' })
      if (!response.ok) throw new Error('错位记录修正失败')
      const payload = await response.json()
      setSuccessMessage(`按现有取数逻辑修正完成，补正 ${payload.fixed ?? 0} 条记录，未改判定结果与版本`)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '错位记录修正失败')
    }
  }

  async function locate(record: Row) {
    setErrorMessage('')
    setSuccessMessage('')
    try {
      const response = await request(`${RECOVERY_ENDPOINT}/${record.id}`)
      if (!response.ok) throw new Error('目标记录定位失败')
      const payload = await response.json()
      setSuccessMessage(
        `已定位 ${payload.判定编号}：上一级别 ${payload.上一级别}，空采样原因 ${payload.空采样原因}，通知抑制结果 ${payload.通知抑制结果}`,
      )
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '目标记录定位失败')
    }
  }

  const totalPages = Math.max(Math.ceil(decisionTotal / size), 1)

  return (
    <section className="page" data-module="backpress-recovery">
      <header className="page-head">
        <div>
          <h2>恢复判定数据记录</h2>
          <p className="page-desc">
            按水位、消费延迟与采样时间校验恢复条件。空采样只累计不判定恢复；恢复必须同时满足水位下限与延迟回落；
            采样中断后从未完成算子继续，通知消费侧同步维护自己的处理记录。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => void runRepair()}>
            修正错位记录
          </button>
          <button className="btn" type="button" onClick={() => void reload()}>
            刷新核对
          </button>
        </div>
      </header>

      <form
        className="filter-bar"
        onSubmit={(event) => {
          event.preventDefault()
          setPage(1)
          void reload()
        }}
      >
        <label className="filter-item">
          <span>检索</span>
          <input
            value={filters.keyword ?? ''}
            placeholder="判定编号 / 指标编号 / 作业名 / 样本编号"
            onChange={(event) => setFilters((prev) => ({ ...prev, keyword: event.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>指标编号</span>
          <input
            value={filters.metric_no ?? ''}
            placeholder="如 BACK-0003"
            onChange={(event) => setFilters((prev) => ({ ...prev, metric_no: event.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>空采样</span>
          <select
            value={filters.empty_sample ?? ''}
            onChange={(event) => setFilters((prev) => ({ ...prev, empty_sample: event.target.value }))}
          >
            <option value="">全部</option>
            <option value="true">仅空采样</option>
            <option value="false">仅有效采样</option>
          </select>
        </label>
        <label className="filter-item">
          <span>通知抑制</span>
          <select
            value={filters.suppress ?? ''}
            onChange={(event) => setFilters((prev) => ({ ...prev, suppress: event.target.value }))}
          >
            <option value="">全部</option>
            <option value="已抑制">已抑制</option>
            <option value="允许通知">允许通知</option>
          </select>
        </label>
        <label className="filter-item">
          <span>采样时间起</span>
          <input
            value={filters.sample_from ?? ''}
            placeholder="2026-09-16T00:00:00+08:00"
            onChange={(event) => setFilters((prev) => ({ ...prev, sample_from: event.target.value }))}
          />
        </label>
        <button className="btn" type="submit">查询</button>
        <button
          className="btn ghost"
          type="button"
          onClick={() => {
            setFilters({})
            setPage(1)
          }}
        >
          重置条件
        </button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {decisionColumns.map((column) => <th key={column}>{column}</th>)}
            <th>定位</th>
          </tr>
        </thead>
        <tbody>
          {decisions.map((row) => (
            <tr key={String(row.id)}>
              {decisionColumns.map((column) => <td key={column}>{text(row[column])}</td>)}
              <td className="row-actions">
                <button className="link" type="button" onClick={() => void locate(row)}>查看</button>
              </td>
            </tr>
          ))}
          {decisions.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={decisionColumns.length + 1}>
                暂无恢复判定记录；空采样不会生成“已恢复”结论
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {decisionTotal} 条恢复判定记录，第 {page} / {totalPages} 页</span>
        <span className="row-actions">
          <button className="link" type="button" disabled={page <= 1} onClick={() => setPage((prev) => Math.max(prev - 1, 1))}>
            上一页
          </button>
          <button
            className="link"
            type="button"
            disabled={page >= totalPages}
            onClick={() => setPage((prev) => Math.min(prev + 1, totalPages))}
          >
            下一页
          </button>
        </span>
      </footer>

      <h3 className="section-title">通知消费侧处理记录</h3>
      <p className="page-desc">{conflicts}</p>
      <table className="data-table">
        <thead>
          <tr>{notificationColumns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {notifications.map((row) => (
            <tr key={String(row.id)}>
              {notificationColumns.map((column) => <td key={column}>{text(row[column])}</td>)}
            </tr>
          ))}
          {notifications.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={notificationColumns.length}>暂无通知处理记录</td>
            </tr>
          )}
        </tbody>
      </table>
      <footer className="page-foot">
        <span>共 {notificationTotal} 条通知处理记录</span>
        {successMessage && <span className="ok-text">{successMessage}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
