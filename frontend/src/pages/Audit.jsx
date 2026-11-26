import React, { useEffect, useState } from 'react'
import { api } from '../api'

const KINDS = ['', '全量', '金丝雀', '晋升', '入闸', '流水线', '回滚', '窗口', '设置', '夹具']

export default function Audit() {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const [kind, setKind] = useState('')
  const [keyword, setKeyword] = useState('')
  const [refId, setRefId] = useState('')
  const [applied, setApplied] = useState({})

  const load = (filters = applied) => {
    const q = new URLSearchParams()
    if (filters.kind) q.set('类别', filters.kind)
    if (filters.keyword) q.set('关键词', filters.keyword)
    if (filters.refId) q.set('关联编号', filters.refId)
    const qs = q.toString()
    return api(`/审计${qs ? `?${qs}` : ''}`).then(setRows).catch((e) => setErr(e.message))
  }
  useEffect(() => { load({}).catch(() => {}) }, [])

  const search = () => {
    setErr('')
    const f = { kind, keyword, refId }
    setApplied(f)
    load(f)
  }
  const reset = () => {
    setKind(''); setKeyword(''); setRefId('')
    const f = {}
    setApplied(f)
    setErr('')
    load(f)
  }

  return (
    <div className="panel">
      <h2>审计流水</h2>
      <p className="hint">
        全量放行同笔写入类别「全量」、动作「全量放行」的证据（含当时比例与未满百标记、回执号）；
        比例变更写类别「金丝雀」。可用类别 / 关键词 / 关联发版单编号复核。
      </p>
      {err && <p className="err">{err}</p>}
      <div className="row filter-row">
        <label>类别
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => <option key={k || 'all'} value={k}>{k || '全部'}</option>)}
          </select>
        </label>
        <label>关键词
          <input value={keyword} placeholder="如 全量放行 / 未满百 / 回执"
            onChange={(e) => setKeyword(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') search() }} />
        </label>
        <label>关联编号
          <input value={refId} style={{ minWidth: 100 }} placeholder="发版单数字 id"
            onChange={(e) => setRefId(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') search() }} />
        </label>
        <button type="button" onClick={search}>查询</button>
        <button type="button" className="secondary" onClick={reset}>重置</button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>类别</th><th>动作</th><th>关联</th><th>备注</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} data-kind={r.类别} data-ref={r.关联编号 || ''}>
                <td className="mono">{r.时间}</td>
                <td><span className={`audit-kind audit-kind-${r.类别}`}>{r.类别}</span></td>
                <td>{r.动作}</td>
                <td>{r.关联编号 || '-'}</td>
                <td className="note-cell">{r.备注}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={5} className="muted">没有符合条件的审计记录。</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
