import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { request } from '@/api/client'

const ENDPOINT = '/api/schema'

type Row = Record<string, unknown>

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
  return text(value) || '—'
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

export default function SchemaDetailView() {
  const { entryId } = useParams()
  const [row, setRow] = useState<Row | null>(null)
  const [message, setMessage] = useState('')
  const [errorMessage, setErrorMessage] = useState('')

  const reload = useCallback(async () => {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${entryId}`)
      if (!response.ok) throw new Error('接口定义明细读取失败')
      setRow(await response.json())
    } catch (error) {
      setRow(null)
      setErrorMessage(error instanceof Error ? error.message : '接口定义明细读取失败')
    }
  }, [entryId])

  useEffect(() => {
    void reload()
  }, [reload])

  async function recheck() {
    setErrorMessage('')
    setMessage('')
    try {
      const response = await request(`${ENDPOINT}/${entryId}/review`)
      const payload = await response.json()
      if (!response.ok) throw new Error(String(payload.detail ?? '契约复核失败'))
      setMessage(`${payload.message}${payload.issues?.length ? `：${payload.issues.join('；')}` : ''}`)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '契约复核失败')
    }
  }

  return (
    <section className="page" data-module="schema-detail">
      <header className="page-head">
        <div>
          <h2>接口定义详情：{row ? cell(row['定义编号']) : `#${entryId}`}</h2>
          <p className="page-desc">
            字段清单、错误码清单与文档版本这里读到的内容与列表页、导出模块来自同一份台账。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => void recheck()}>复核契约</button>
          <Link className="btn" to="/schema">返回列表</Link>
        </div>
      </header>

      {row && (
        <dl className="detail-grid">
          <dt>定义编号</dt><dd>{cell(row['定义编号'])}</dd>
          <dt>关联服务</dt><dd>{cell(row['关联服务'])}</dd>
          <dt>请求方法</dt><dd>{cell(row['请求方法'])}</dd>
          <dt>文档版本</dt><dd>{cell(row['文档版本'])}</dd>
          <dt>状态</dt><dd>{cell(row['status'])}</dd>
          <dt>参数列表</dt><dd>{cell(row['参数列表'])}</dd>
          <dt>响应示例</dt><dd><code>{cell(row['响应示例'])}</code></dd>
          <dt>错误码说明</dt><dd>{cell(row['错误码说明'])}</dd>
          <dt>错误码清单</dt><dd>{cell(row['错误码清单'])}</dd>
          <dt>调用示例</dt><dd>{cell(row['调用示例'])}</dd>
          <dt>字段清单（{text(row['字段总数']) || 0} 项）</dt><dd>{cell(row['字段清单'])}</dd>
          <dt>旧字段清单</dt><dd>{cell(row['旧字段清单'])}</dd>
          <dt>新增字段</dt><dd>{cell(row['新增字段'])}</dd>
          <dt>移除字段</dt><dd>{cell(row['移除字段'])}</dd>
          <dt>重复字段</dt><dd>{cell(row['重复字段'])}</dd>
          <dt>兼容级别</dt><dd>{cell(row['兼容级别'])}</dd>
          <dt>台账审核状态</dt><dd>{cell(row['台账审核状态'])}</dd>
          <dt>契约复核</dt><dd>{reviewText(row['契约复核'])}</dd>
          <dt>版本仲裁</dt><dd>{cell(row['版本仲裁'])}</dd>
          <dt>版本历史</dt><dd>{cell(row['版本历史'])}</dd>
        </dl>
      )}

      <footer className="page-foot">
        {message && <span className="notice-text">{message}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
