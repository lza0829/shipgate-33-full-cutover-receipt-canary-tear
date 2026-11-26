import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Rollback() {
  const [rows, setRows] = useState([])
  const [rid, setRid] = useState('')
  const [ver, setVer] = useState('1.0.0')
  const [err, setErr] = useState('')
  const load = async () => {
    const all = await api('/发版单')
    const active = all.filter((r) => ['已放行', '晋升中'].includes(r.状态))
    setRows(active)
    if (!rid && active[0]) setRid(String(active[0].编号))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const go = async () => {
    setErr('')
    try {
      await api(`/发版单/${rid}/回滚`, { method: 'POST', body: JSON.stringify({ 目标版本: ver }) })
      load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>回滚</h2>
      {err && <p className="err">{err}</p>}
      <div className="row">
        <label>发版单
          <select value={rid} onChange={(e) => setRid(e.target.value)}>
            {rows.map((r) => <option key={r.编号} value={r.编号}>{r.发版单号} · {r.应用} · {r.当前环境}</option>)}
          </select>
        </label>
        <label>回滚目标版本<input value={ver} onChange={(e) => setVer(e.target.value)} /></label>
        <button type="button" className="danger" onClick={go}>执行回滚</button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>
    </div>
  )
}
