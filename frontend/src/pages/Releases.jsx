import React, { useEffect, useState } from 'react'
import { api } from '../api'

function cellBadge(cell) {
  if (cell === '满百全量') return <span className="badge badge-ok">{cell}</span>
  if (cell === '未满百全量') return <span className="badge badge-warn">{cell}</span>
  if (cell === '缺口·未全量') return <span className="badge badge-danger">{cell}</span>
  return <span className="badge badge-muted">{cell || '未全量'}</span>
}

export default function Releases() {
  const [rows, setRows] = useState([])
  const [apps, setApps] = useState([])
  const [appId, setAppId] = useState('')
  const [ver, setVer] = useState('')
  const [digest, setDigest] = useState('')
  const [ratio, setRatio] = useState('10')
  const [err, setErr] = useState('')
  const [info, setInfo] = useState('')
  const load = async () => {
    setRows(await api('/发版单'))
    const a = await api('/应用')
    setApps(a)
    if (!appId && a[0]) setAppId(String(a[0].编号))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const create = async () => {
    setErr(''); setInfo('')
    try {
      await api('/发版单', {
        method: 'POST',
        body: JSON.stringify({
          应用编号: Number(appId), 目标版本: ver, 制品摘要: digest,
          金丝雀比例: ratio === '' ? null : Number(ratio),
        }),
      })
      setVer(''); setDigest(''); load()
    } catch (e) { setErr(e.message) }
  }
  const pipeline = async (id, status) => {
    setErr(''); setInfo('')
    try {
      await api(`/发版单/${id}/流水线`, { method: 'POST', body: JSON.stringify({ 流水线: status }) })
      load()
    } catch (e) { setErr(e.message) }
  }
  const admit = async (id) => {
    setErr(''); setInfo('')
    try {
      await api(`/发版单/${id}/入闸`, { method: 'POST', body: '{}' })
      load()
    } catch (e) { setErr(e.message) }
  }
  const changeRatio = async (id, value) => {
    setErr(''); setInfo('')
    try {
      const r = await api(`/发版单/${id}/金丝雀比例`, {
        method: 'POST', body: JSON.stringify({ 金丝雀比例: Number(value) }),
      })
      setInfo(`${r.发版单号} 比例已改为 ${r.金丝雀比例}%`)
      load()
    } catch (e) { setErr(e.message) }
  }
  const fullRelease = async (id) => {
    setErr(''); setInfo('')
    try {
      const r = await api(`/发版单/${id}/全量放行`, {
        method: 'POST', body: JSON.stringify({ 备注: '发版单页全量放行' }),
      })
      const rc = r.全量回执 || {}
      setInfo(
        `${r.发版单号} 全量写口返回：全量格 ${r.全量格 || '-'}，切满探针` +
        `${r.切满探针 && r.切满探针.过线 ? '过线' : '未过线（' + ((r.切满探针 && r.切满探针.未过原因) || []).join('；') + '）'}，` +
        `比例百分列 ${r.比例百分列 || r.金丝雀比例 + '%'}，回执 ${rc.回执号 || r.全量回执号 || '-'}`,
      )
      load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>发版单</h2>
      <p className="hint">
        全量落成闸：允许未满百提前全量，但必须挂<span className="badge badge-warn">未满百全量</span>
        可核标；全量格只由切满探针 ∪ 全量事件 ∪ 审计证据点亮，未切满不亮满百。
      </p>
      {err && <p className="err">{err}</p>}
      {info && <p className="ok">{info}</p>}
      <div className="row">
        <label>应用
          <select value={appId} onChange={(e) => setAppId(e.target.value)}>
            {apps.map((a) => <option key={a.编号} value={a.编号}>{a.名称}</option>)}
          </select>
        </label>
        <label>目标版本<input value={ver} onChange={(e) => setVer(e.target.value)} placeholder="1.4.0" /></label>
        <label>制品摘要<input value={digest} onChange={(e) => setDigest(e.target.value)} placeholder="sha256:..." /></label>
        <label>金丝雀比例
          <input type="number" min="0" max="100" value={ratio} style={{ width: 90 }}
            onChange={(e) => setRatio(e.target.value)} />
        </label>
        <button type="button" onClick={create}>申请入闸</button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>发版单号</th><th>应用</th><th>目标版本</th><th>状态</th>
              <th>比例百分列</th><th>全量格</th><th>切满探针</th><th>切满差额</th>
              <th>当前环境</th><th>流水线</th><th>全量回执号</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} data-release={r.编号} data-serial={r.发版单号} data-cell={r.全量格}>
                <td>{r.发版单号}</td>
                <td>{r.应用}</td>
                <td>{r.目标版本}</td>
                <td>{r.状态}</td>
                <td>
                  <span className={r.金丝雀比例 < 100 ? 'ratio ratio-low' : 'ratio ratio-full'}>
                    {r.比例百分列 || `${r.金丝雀比例}%`}
                  </span>
                  {r.未满百全量
                    ? <span className="badge badge-warn">未满百全量</span>
                    : r.金丝雀比例 < 100
                      ? <span className="badge badge-muted">未满百</span>
                      : <span className="badge badge-ok">满百</span>}
                  {!['已放行', '已回滚', '已关闭'].includes(r.状态) && (
                    <span className="ratio-edit">
                      <input
                        type="number" min="0" max="100"
                        aria-label={`金丝雀比例-${r.发版单号}`}
                        defaultValue={r.金丝雀比例}
                        onKeyDown={(e) => { if (e.key === 'Enter') changeRatio(r.编号, e.target.value) }}
                      />
                      <button type="button" className="secondary mini"
                        onClick={(e) => changeRatio(r.编号, e.currentTarget.parentElement.querySelector('input').value)}>
                        改比例
                      </button>
                    </span>
                  )}
                </td>
                <td>{cellBadge(r.全量格 || r.全量落成态)}</td>
                <td>
                  {r.状态 === '已放行'
                    ? (r.切满探针 && r.切满探针.过线
                      ? <span className="badge badge-ok">过线</span>
                      : <span className="badge badge-danger" title={(r.切满探针 && r.切满探针.未过原因 || []).join('；')}>未过线</span>)
                    : <span className="muted">-</span>}
                </td>
                <td>{r.切满差额 ?? '-'}</td>
                <td>{r.当前环境 || '-'}</td>
                <td>{r.流水线}</td>
                <td className="mono">{r.全量回执号 || '-'}</td>
                <td>
                  <button type="button" className="secondary" onClick={() => pipeline(r.编号, '绿')}>流水线绿</button>{' '}
                  <button type="button" className="danger" onClick={() => pipeline(r.编号, '红')}>流水线红</button>{' '}
                  <button type="button" onClick={() => admit(r.编号)}>入闸</button>{' '}
                  <button type="button" className="full-btn" onClick={() => fullRelease(r.编号)}>全量放行</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
