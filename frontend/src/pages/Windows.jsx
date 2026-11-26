import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Windows() {
  const [rel, setRel] = useState([])
  const [rb, setRb] = useState([])
  const [env, setEnv] = useState('产')
  const [err, setErr] = useState('')
  const load = async () => {
    setRel(await api('/放行窗'))
    setRb(await api('/回滚窗'))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const toggle = async (kind, status) => {
    setErr('')
    try {
      const path = kind === '放行' ? '/放行窗/切换' : '/回滚窗/切换'
      await api(path, { method: 'POST', body: JSON.stringify({ 环境: env, 状态: status }) })
      load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>放行窗 / 回滚窗</h2>
      {err && <p className="err">{err}</p>}
      <div className="row">
        <label>环境
          <select value={env} onChange={(e) => setEnv(e.target.value)}>
            <option value="测">测</option>
            <option value="预">预</option>
            <option value="产">产</option>
          </select>
        </label>
        <button type="button" onClick={() => toggle('放行', '窗开')}>放行窗开</button>
        <button type="button" className="danger" onClick={() => toggle('放行', '窗关')}>放行窗关</button>
        <button type="button" onClick={() => toggle('回滚', '窗开')}>回滚窗开</button>
        <button type="button" className="danger" onClick={() => toggle('回滚', '窗关')}>回滚窗关</button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>
      <h2>放行窗</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>环境</th><th>开启</th><th>关闭</th><th>状态</th></tr></thead>
          <tbody>{rel.map((w) => <tr key={w.编号}><td>{w.环境}</td><td>{w.开启}</td><td>{w.关闭}</td><td>{w.状态}</td></tr>)}</tbody>
        </table>
      </div>
      <h2>回滚窗</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>环境</th><th>开启</th><th>关闭</th><th>状态</th></tr></thead>
          <tbody>{rb.map((w) => <tr key={w.编号}><td>{w.环境}</td><td>{w.开启}</td><td>{w.关闭}</td><td>{w.状态}</td></tr>)}</tbody>
        </table>
      </div>
    </div>
  )
}
