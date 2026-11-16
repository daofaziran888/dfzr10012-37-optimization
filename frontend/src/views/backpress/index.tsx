import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/backpress'
const columns = [
  "指标编号",
  "流作业名",
  "反压级别",
  "阻塞算子",
  "缓冲区水位",
  "消费延迟",
  "采样时间",
  "处理状态"
]
const actions = [
  "标记反压",
  "扩容处理",
  "标记恢复"
]
const statuses = [
  "正常",
  "轻微反压",
  "严重反压",
  "已恢复"
]
const stats = [
  {
    "label": "反压指标总数",
    "value": 0
  },
  {
    "label": "正常指标",
    "value": 0
  },
  {
    "label": "严重反压指标",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

export default function BackpressView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => ['指标编号/作业名', '反压级别'], [])
  const levelCodes: Record<string, string> = { 正常: 'normal', 轻微反压: 'mild', 严重反压: 'severe', 已恢复: 'recovered' }

  const reload = useCallback(async () => {
    setErrorMessage('')
    // 后端只认 keyword/status/level，把界面上的条件映射成对应参数。
    const query = new URLSearchParams()
    const keyword = filters['指标编号/作业名']
    if (keyword) query.set('keyword', keyword)
    const level = filters['反压级别']
    if (level) query.set('level', levelCodes[level] ?? level)
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) {
        throw new Error('反压指标列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '反压监控面板列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('反压监控面板动作未生效，请稍后重试')
      }
      const payload = await response.json()
      if (payload.ok === false) {
        setErrorMessage(String(payload.message ?? '动作被业务规则拦下'))
      } else {
        setNotice(String(payload.message ?? '动作已生效'))
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '反压监控面板操作失败')
    }
  }

  return (
    <section className="page" data-module="backpress">
      <header className="page-head">
        <div>
          <h2>反压监控面板管理</h2>
          <p className="page-desc">维护反压监控的级别评估、阻塞定位与延迟分析，为反压监控面板提供可审计的数据工程流程。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('反压指标登记入口尚未接入审批流')}>
            登记反压指标
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出反压监控面板清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无反压监控面板数据，可先登记反压指标</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条反压监控面板记录</span>
        {notice && <span>{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
