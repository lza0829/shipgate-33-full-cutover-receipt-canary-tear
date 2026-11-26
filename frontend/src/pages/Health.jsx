import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Health() {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const load = () => api('/实例').then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  const setH = async (id, health) => {
    setErr('')
    try {
      await api(`/实例/${id}/健康`, { method: 'POST', body: JSON.stringify({ 健康: health }) })
      load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>健康探针</h2>
      <p className="hint">
        实例切流比例必须与全量落成态同源：全量放行成功后产环境实例一律切到目标版本、切流 100%；
        未满百提前全量的实例行同时带「未满百全量」标与发版单真源比例，供探针页核对。
      </p>
      {err && <p className="err">{err}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>应用</th><th>环境</th><th>版本</th><th>健康</th>
              <th>切流比例</th><th>金丝雀真源</th><th>未满百标</th>
              <th>发版单</th><th>全量回执号</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} data-instance={r.编号}>
                <td>{r.应用}</td>
                <td>{r.环境}</td>
                <td>{r.版本}</td>
                <td>{r.健康}</td>
                <td>
                  <span className={r.切流比例 === 100 ? 'ratio ratio-full' : 'ratio ratio-low'}>
                    {r.切流比例 ?? 100}%
                  </span>
                </td>
                <td>{r.金丝雀比例 === null || r.金丝雀比例 === undefined ? '-' : `${r.金丝雀比例}%`}</td>
                <td>
                  {r.未满百全量
                    ? <span className="badge badge-warn">未满百全量</span>
                    : '-'}
                </td>
                <td>{r.发版单编号 || '-'}</td>
                <td className="mono">{r.全量回执号 || '-'}</td>
                <td>
                  <button type="button" onClick={() => setH(r.编号, '探针绿')}>探针绿</button>{' '}
                  <button type="button" className="danger" onClick={() => setH(r.编号, '探针红')}>探针红</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
