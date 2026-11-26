import React, { useEffect, useState } from 'react'
import { api } from '../api'

function verdictBadge(v) {
  if (v === '全量一致') return <span className="badge badge-ok">{v}</span>
  if (v === '未满百全量可核') return <span className="badge badge-warn">{v}</span>
  if (v === '未全量') return <span className="badge badge-muted">{v}</span>
  return <span className="badge badge-danger">{v}</span>
}

function cellBadge(cell) {
  if (cell === '满百全量') return <span className="badge badge-ok">{cell}</span>
  if (cell === '未满百全量') return <span className="badge badge-warn">{cell}</span>
  if (cell === '缺口·未全量') return <span className="badge badge-danger">{cell}</span>
  return <span className="badge badge-muted">{cell || '未全量'}</span>
}

export default function Reconcile() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [info, setInfo] = useState('')
  const [busy, setBusy] = useState(false)
  const load = () => api('/对账').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])

  const replay = async (rid) => {
    setErr(''); setInfo(''); setBusy(true)
    try {
      const body = rid ? JSON.stringify({ 发版单编号: rid, 修复: true }) : JSON.stringify({ 修复: true })
      const r = await api('/对账/重放', { method: 'POST', body })
      if (rid) {
        const one = (r.结果 || [])[0] || {}
        setInfo(`重放收口完成：${one.发版单号 || rid}；修复项：${(one.修复项 || []).join('、') || '无需修复（本就一致）'}；结论：${one.修复后结论 || '-'}，全量格：${one.修复后全量格 || '-'}`)
      } else {
        setInfo(`批量重放完成：扫描 ${r.扫描数 ?? (r.结果 || []).length} 单，修复 ${r.修复数 ?? 0} 单`)
      }
      await load()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  if (err && !data) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const s = data.发版统计 || {}
  const rows = data.全量对账 || []
  const summary = data.全量对账汇总 || {}
  const cells = data.全量格对读 || []
  const cellSummary = data.全量格对读汇总 || {}
  return (
    <div className="panel">
      <h2>发布对账 · 全量落成</h2>
      <p className="hint">
        读侧可重放：以 <code>releases.canary_ratio</code> 为比例真源，对读全量快照、回执、名册版本、
        实例切流与审计证据。切满探针、全量事件、全量格、审计证据四者同命运：未切满一律钉缺口，
        只有「全量一致」「未满百全量可核」才允许全量格点亮。
      </p>
      {err && <p className="err">{err}</p>}
      {info && <p className="ok">{info}</p>}

      <div className="grid">
        {Object.entries(s).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
        <div className="stat"><div className="n">{data.晋升事件数}</div><div className="l">晋升事件数</div></div>
        {Object.entries(cellSummary).map(([k, v]) => (
          <div className="stat" key={`cs-${k}`}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
      </div>

      <div className="row">
        <button type="button" className="full-btn" disabled={busy} onClick={() => replay(null)}>
          一键重放收口（全部已放行单）
        </button>
        <button type="button" className="secondary" onClick={load}>刷新</button>
      </div>

      <h2>全量格 · 切满探针对读</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>发版单号</th><th>比例百分列</th><th>切满探针</th><th>切满差分</th>
              <th>全量格</th><th>全量事件</th><th>审计证据</th><th>旁侧计数格</th>
              <th>对账结论</th><th>缺口标</th>
            </tr>
          </thead>
          <tbody>
            {cells.map((r) => (
              <tr key={`cell-${r.编号}`} data-cell={r.全量格} data-probe={r.切满探针过线}>
                <td>{r.发版单号}</td>
                <td><span className={r.比例百分列 === '100%' ? 'ratio ratio-full' : 'ratio ratio-low'}>{r.比例百分列}</span></td>
                <td>
                  {r.切满探针过线
                    ? <span className="badge badge-ok">过线</span>
                    : <span className="badge badge-danger">未过线</span>}
                </td>
                <td className="gap-cell">{(r.切满差分 || []).join('；') || '-'}</td>
                <td>{cellBadge(r.全量格)}</td>
                <td className="mono">{r.全量事件 || '-'}</td>
                <td>{r.审计证据数 ?? 0}</td>
                <td>{r.旁侧计数格}</td>
                <td>{verdictBadge(r.对账结论)}</td>
                <td className="gap-cell">{(r.缺口标 || []).join('；') || '-'}</td>
              </tr>
            ))}
            {cells.length === 0 && <tr><td colSpan={10} className="muted">暂无已放行单。</td></tr>}
          </tbody>
        </table>
      </div>

      <h2>全量落成对账</h2>
      {Object.keys(summary).length > 0 && (
        <div className="row summary-row">
          {Object.entries(summary).map(([k, v]) => (
            <span key={k} className="summary-chip">{verdictBadge(k)} <b>{v}</b></span>
          ))}
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>发版单号</th><th>状态</th><th>比例百分列</th><th>全量时快照</th>
              <th>切满探针</th><th>切满差额</th><th>全量格</th>
              <th>对账结论</th><th>未满百可核</th><th>回执</th><th>审计证据</th>
              <th>名册一致</th><th>实例全切</th><th>切流比例</th><th>缺口标</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const bad = !['全量一致', '未满百全量可核', '未全量'].includes(r.对账结论)
              return (
                <tr key={`cut-${r.编号}`} className={bad ? 'row-danger' : ''} data-verdict={r.对账结论}>
                  <td>{r.发版单号}</td>
                  <td>{r.状态}</td>
                  <td><span className={r.真源比例 < 100 ? 'ratio ratio-low' : 'ratio ratio-full'}>{r.真源比例}%</span></td>
                  <td>{r.全量时比例 === null || r.全量时比例 === undefined ? '-' : `${r.全量时比例}%`}</td>
                  <td>
                    {r.切满探针 && r.切满探针.过线
                      ? <span className="badge badge-ok">过线</span>
                      : <span className="badge badge-danger">未过线</span>}
                  </td>
                  <td>{r.切满差额 ?? '-'}</td>
                  <td>{cellBadge(r.全量格)}</td>
                  <td>{verdictBadge(r.对账结论)}</td>
                  <td>{r.未满百可核 ? <span className="badge badge-warn">可核</span> : (r.未满百全量 ? <span className="badge badge-danger">标而不可核</span> : '-')}</td>
                  <td>{r.回执存在 ? <span className="mono" title={r.全量回执号}>{r.全量回执号 ? r.全量回执号.slice(-10) : '有'}</span> : <span className="badge badge-muted">无回执</span>}</td>
                  <td>{r.审计证据数 ?? 0}</td>
                  <td>{r.名册一致 ? <span className="badge badge-ok">一致</span> : <span className="badge badge-danger">不一致</span>}</td>
                  <td>
                    {r.全切
                      ? <span className="badge badge-ok">全切</span>
                      : <span className="badge badge-danger">未全切</span>}
                    <span className="muted"> {r.目标实例数 ?? 0}/{r.实例总数 ?? 0}</span>
                  </td>
                  <td>{r.切流比例 ?? '-'}%</td>
                  <td className="gap-cell">{Array.isArray(r.缺口标) ? (r.缺口标.join('；') || '-') : (r.缺口标 || '-')}</td>
                  <td><button type="button" className="secondary mini" disabled={busy} onClick={() => replay(r.编号)}>重放收口</button></td>
                </tr>
              )
            })}
            {rows.length === 0 && <tr><td colSpan={16} className="muted">暂无发版单。</td></tr>}
          </tbody>
        </table>
      </div>

      <h2>名册与实例</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>应用</th><th>环境</th><th>名册版本</th><th>实例版本们</th><th>红灯数</th></tr></thead>
          <tbody>
            {(data.名册与实例 || []).map((r, i) => (
              <tr key={i}><td>{r.应用}</td><td>{r.环境}</td><td>{r.名册版本}</td><td>{r.实例版本们 || '-'}</td><td>{r.红灯数}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
