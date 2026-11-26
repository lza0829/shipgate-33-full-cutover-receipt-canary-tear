import React, { useEffect, useState } from 'react'
import { api } from '../api'

function cellBadge(cell) {
  if (cell === '满百全量') return <span className="badge badge-ok">{cell}</span>
  if (cell === '未满百全量') return <span className="badge badge-warn">{cell}</span>
  if (cell === '缺口·未全量') return <span className="badge badge-danger">{cell}</span>
  return <span className="badge badge-muted">{cell || '未全量'}</span>
}

export default function Promote() {
  const [rows, setRows] = useState([])
  const [rid, setRid] = useState('')
  const [env, setEnv] = useState('预')
  const [err, setErr] = useState('')
  const [info, setInfo] = useState('')
  const load = async () => {
    const all = await api('/发版单')
    const active = all.filter((r) => ['已入闸', '晋升中'].includes(r.状态))
    setRows(active)
    if (!rid && active[0]) setRid(String(active[0].编号))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const go = async () => {
    setErr(''); setInfo('')
    try {
      const r = await api(`/发版单/${rid}/晋升`, { method: 'POST', body: JSON.stringify({ 目标环境: env }) })
      if (env === '产' || r.状态 === '已放行') {
        const rc = r.全量回执 || {}
        const probe = r.切满探针 || {}
        setInfo(`已走全量落成闸：全量格 ${r.全量格 || '-'}，切满探针 ${probe.过线 ? '过线' : '未过线'}，比例 ${r.比例百分列 || r.金丝雀比例 + '%'}，回执 ${rc.回执号 || '-'}`)
      }
      load()
    } catch (e) { setErr(e.message) }
  }
  const fullRelease = async (id) => {
    setErr(''); setInfo('')
    try {
      const r = await api(`/发版单/${id}/全量放行`, {
        method: 'POST', body: JSON.stringify({ 备注: '晋升页全量放行' }),
      })
      const rc = r.全量回执 || {}
      const probe = r.切满探针 || {}
      setInfo(`${r.发版单号}：全量格 ${r.全量格 || '-'}，切满探针 ${probe.过线 ? '过线' : '未过线'}，回执 ${rc.回执号 || r.全量回执号 || '-'}`)
      load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>环境晋升</h2>
      <p className="hint">
        晋升到「产」与「全量放行」是同一个全量落成写口：未满百可提前全量，但必挂
        <span className="badge badge-warn">未满百全量</span>可核标；晋升到产走同一笔事务，
        不存在“日常晋升旁路”半截。
      </p>
      {err && <p className="err">{err}</p>}
      {info && <p className="ok">{info}</p>}
      <div className="row">
        <label>发版单
          <select value={rid} onChange={(e) => setRid(e.target.value)}>
            {rows.map((r) => <option key={r.编号} value={r.编号}>{r.发版单号} · {r.应用} · {r.当前环境}</option>)}
          </select>
        </label>
        <label>目标环境
          <select value={env} onChange={(e) => setEnv(e.target.value)}>
            <option value="测">测</option>
            <option value="预">预</option>
            <option value="产">产（走全量落成闸）</option>
          </select>
        </label>
        <button type="button" onClick={go}>晋升</button>
        <button type="button" className="full-btn"
          onClick={() => rid && fullRelease(Number(rid))}>全量放行（跳过环境选择）</button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>发版单号</th><th>应用</th><th>目标版本</th><th>状态</th>
              <th>比例百分列</th><th>全量格</th><th>当前环境</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} data-release={r.编号} data-cell={r.全量格}>
                <td>{r.发版单号}</td>
                <td>{r.应用}</td>
                <td>{r.目标版本}</td>
                <td>{r.状态}</td>
                <td>
                  <span className={r.金丝雀比例 < 100 ? 'ratio ratio-low' : 'ratio ratio-full'}>{r.比例百分列 || `${r.金丝雀比例}%`}</span>
                  {r.未满百全量 && <span className="badge badge-warn">未满百全量</span>}
                </td>
                <td>{cellBadge(r.全量格 || r.全量落成态)}</td>
                <td>{r.当前环境}</td>
                <td><button type="button" className="full-btn" onClick={() => fullRelease(r.编号)}>全量放行</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
