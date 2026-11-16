import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/schema'
const columns = [
  "定义编号",
  "关联服务",
  "请求方法",
  "参数列表",
  "响应示例",
  "错误码说明",
  "调用示例",
  "文档版本"
]
const ledgerColumns = [
  "字段清单",
  "旧字段清单",
  "错误码清单",
  "兼容级别",
  "台账审核状态",
  "契约复核"
]
const actions = [
  "提交审核",
  "发布文档",
  "废弃文档"
]
const statuses = [
  "草稿",
  "待审核",
  "已发布",
  "已废弃"
]
const editableFields = [
  "定义编号",
  "关联服务",
  "请求方法",
  "参数列表",
  "响应示例",
  "错误码说明",
  "调用示例",
  "文档版本"
]

type Row = Record<string, unknown>

type ActionFailure = {
  id: number
  "定义编号"?: string
  "文档版本"?: string
  "失败原因": string
  "状态": string
  "重试次数": number
}

function text(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (Array.isArray(value)) return value.map((item) => text(item)).join('、')
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    if ('code' in record) return `${text(record.code)}${record.message ? `：${text(record.message)}` : ''}`
    if ('文档版本' in record) return text(record['文档版本'])
    return JSON.stringify(value)
  }
  return String(value)
}

function cell(value: unknown): string {
  const rendered = text(value)
  return rendered || '—'
}

function reviewText(review: unknown): string {
  if (!review || typeof review !== 'object') return '未复核'
  const record = review as { ok?: boolean; issues?: unknown[]; warnings?: unknown[] }
  if (record.ok) {
    const warnings = Array.isArray(record.warnings) ? record.warnings.map(text) : []
    return warnings.length ? `通过（提示：${warnings.join('；')}）` : '通过'
  }
  const issues = Array.isArray(record.issues) ? record.issues.map(text) : []
  return `未通过：${issues.join('；') || '原因未知'}`
}

export default function SchemaView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [failures, setFailures] = useState<ActionFailure[]>([])
  const [selected, setSelected] = useState<Row | null>(null)
  const [form, setForm] = useState<Record<string, string>>({})
  const [editingId, setEditingId] = useState<number | null>(null)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('接口定义列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '接口文档列表读取失败')
    }
  }, [filters])

  const reloadFailures = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/failures`)
      if (!response.ok) return
      const payload = await response.json()
      setFailures(payload.items ?? [])
    } catch {
      // 失败记录只是辅助信息，读取失败不覆盖主列表的提示。
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    void reloadFailures()
  }, [reloadFailures])

  async function readResult(response: Response, fallback: string): Promise<{ ok: boolean; message: string; entry?: Row }> {
    if (!response.ok) {
      throw new Error(`${fallback}，接口返回 ${response.status}`)
    }
    const payload = await response.json()
    if (payload.ok === false) {
      throw new Error(String(payload.message ?? fallback))
    }
    return payload
  }

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await readResult(response, '接口文档动作未生效，请稍后重试')
      setNotice(String(payload.message ?? `${action}已完成`))
      await reload()
      if (selected && selected.id === row.id) {
        await openDetail(row)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '接口文档操作失败')
    }
  }

  async function openDetail(row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}`)
      if (!response.ok) {
        throw new Error('接口定义明细读取失败')
      }
      setSelected(await response.json())
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '接口定义明细读取失败')
    }
  }

  async function submitForm() {
    setErrorMessage('')
    setNotice('')
    const payload = { values: form }
    try {
      const response = editingId
        ? await request(`${ENDPOINT}/${editingId}`, { method: 'PUT', body: JSON.stringify(payload) })
        : await request(ENDPOINT, { method: 'POST', body: JSON.stringify(payload) })
      const result = await readResult(response, '接口定义保存失败，请检查响应示例与错误码说明')
      setNotice(String(result.message ?? '接口定义已保存'))
      setForm({})
      setEditingId(null)
      await reload()
      await reloadFailures()
      if (result.entry) setSelected(result.entry)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '接口定义保存失败')
      await reloadFailures()
    }
  }

  async function retryFailure(failure: ActionFailure) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/failures/${failure.id}/retry`, {
        method: 'POST',
        body: JSON.stringify({ values: {} }),
      })
      const result = await readResult(response, '失败记录重试未通过')
      setNotice(String(result.message ?? '失败记录已重试'))
      await reload()
      await reloadFailures()
      if (result.entry) setSelected(result.entry)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '失败记录重试未通过')
      await reloadFailures()
    }
  }

  function startCreate() {
    setEditingId(null)
    setForm({})
    setNotice('')
    setErrorMessage('')
  }

  function startEdit(row: Row) {
    const next: Record<string, string> = {}
    for (const field of editableFields) next[field] = text(row[field])
    setForm(next)
    setEditingId(Number(row.id))
    setNotice('')
    setErrorMessage('')
  }

  const stats = useMemo(() => {
    const published = rows.filter((row) => row.status === '已发布').length
    const reviewing = rows.filter((row) => row.status === '待审核').length
    const aligned = rows.filter((row) => {
      const review = row['契约复核'] as { ok?: boolean } | undefined
      return Boolean(review?.ok)
    }).length
    return [
      { label: '接口定义总数', value: total },
      { label: '已发布文档', value: published },
      { label: '待审核文档', value: reviewing },
      { label: '字段清单已对齐', value: aligned },
    ]
  }, [rows, total])

  return (
    <section className="page" data-module="schema">
      <header className="page-head">
        <div>
          <h2>接口文档管理</h2>
          <p className="page-desc">
            响应示例、错误码清单与文档版本一起保存；字段清单从响应示例解析，发布前必须与台账对齐。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={startCreate}>
            {editingId ? '改为登记新接口' : '登记接口定义'}
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出接口文档清单
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

      <section className="panel" aria-label="接口定义登记与编辑">
        <h3 className="panel-title">{editingId ? `编辑接口定义 #${editingId}` : '登记接口定义'}</h3>
        <p className="panel-hint">
          响应示例里的原始数据决定字段清单；错误码说明与文档版本必须与它同一次提交，缺一项就整份不保存。
        </p>
        <div className="form-grid">
          {editableFields.map((field) => (
            <label key={field} className="form-item">
              <span>{field}</span>
              {field === '响应示例' || field === '参数列表' || field === '错误码说明' ? (
                <textarea
                  rows={3}
                  value={form[field] ?? ''}
                  onChange={(event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))}
                />
              ) : (
                <input
                  value={form[field] ?? ''}
                  onChange={(event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))}
                />
              )}
            </label>
          ))}
        </div>
        <div className="panel-actions">
          <button className="btn primary" type="button" onClick={() => void submitForm()}>
            {editingId ? '保存契约' : '登记并保存契约'}
          </button>
          <button className="btn ghost" type="button" onClick={startCreate}>清空</button>
        </div>
      </section>

      {failures.length > 0 && (
        <section className="panel" aria-label="失败记录">
          <h3 className="panel-title">失败记录：中断后从这里接着重试</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>失败编号</th>
                <th>定义编号</th>
                <th>文档版本</th>
                <th>失败原因</th>
                <th>重试次数</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {failures.map((failure) => (
                <tr key={failure.id}>
                  <td>{failure.id}</td>
                  <td>{cell(failure['定义编号'])}</td>
                  <td>{cell(failure['文档版本'])}</td>
                  <td>{failure['失败原因']}</td>
                  <td>{failure['重试次数']}</td>
                  <td className="row-actions">
                    <button className="link" type="button" onClick={() => void retryFailure(failure)}>按原记录重试</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            {ledgerColumns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{cell(row[column])}</td>)}
              <td>{cell(row['字段清单'])}</td>
              <td>{cell(row['旧字段清单'])}</td>
              <td>{cell(row['错误码清单'])}</td>
              <td>{cell(row['兼容级别'])}</td>
              <td>{cell(row['台账审核状态'])}</td>
              <td>{reviewText(row['契约复核'])}</td>
              <td className="row-actions">
                <button className="link" type="button" onClick={() => void openDetail(row)}>查看详情</button>
                <button className="link" type="button" onClick={() => startEdit(row)}>编辑契约</button>
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
              <td className="empty-state" colSpan={columns.length + ledgerColumns.length + 1}>
                暂无接口文档数据，可先登记接口定义
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {selected && (
        <section className="panel" aria-label="接口定义详情">
          <div className="panel-head">
            <h3 className="panel-title">接口定义详情：{cell(selected['定义编号'])}</h3>
            <button className="btn ghost" type="button" onClick={() => setSelected(null)}>关闭</button>
          </div>
          <dl className="detail-grid">
            <dt>文档版本</dt><dd>{cell(selected['文档版本'])}</dd>
            <dt>台账审核状态</dt><dd>{cell(selected['台账审核状态'])}</dd>
            <dt>参数列表</dt><dd>{cell(selected['参数列表'])}</dd>
            <dt>响应示例</dt><dd><code>{cell(selected['响应示例'])}</code></dd>
            <dt>字段清单（{text(selected['字段总数']) || 0} 项）</dt><dd>{cell(selected['字段清单'])}</dd>
            <dt>旧字段清单</dt><dd>{cell(selected['旧字段清单'])}</dd>
            <dt>错误码清单</dt><dd>{cell(selected['错误码清单'])}</dd>
            <dt>兼容级别</dt><dd>{cell(selected['兼容级别'])}</dd>
            <dt>契约复核</dt><dd>{reviewText(selected['契约复核'])}</dd>
            <dt>版本仲裁</dt><dd>{cell(selected['版本仲裁'])}</dd>
            <dt>版本历史</dt><dd>{cell(selected['版本历史'])}</dd>
          </dl>
        </section>
      )}

      <footer className="page-foot">
        <span>共 {total} 条接口文档记录</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
