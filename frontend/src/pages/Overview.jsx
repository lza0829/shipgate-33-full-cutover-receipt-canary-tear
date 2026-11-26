import React, { useEffect, useState } from 'react'
import { api } from '../api'

function cellBadge(cell) {
  if (cell === '满百全量') return <span className="badge badge-ok">{cell}</span>
  if (cell === '未满百全量') return <span className="badge badge-warn">{cell}</span>
  if (cell === '缺口·未全量') return <span className="badge badge-danger">{cell}</span>
  return <span className="badge badge-muted">{cell || '未全量'}</span>
}

export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/总览').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const d = data.分布 || {}
  const policy = data.策略 || {}
  const early = data.未满百全量 || []
  const splits = data.分裂告警 || []
  const receipts = data.全量回执 || []
  const cells = data.全量格对读 || []
  const side = data.旁侧计数格 || {}
  const gaps = data.缺口未切满 || []
  return (
    <div className="panel">
      <h2>总览</h2>

      <div className="policy-banner">
        <span className="badge badge-policy">全量落成闸 · {policy.策略码 || 'EARLY_FULL_MARKED'}</span>
        <span>{policy.策略 || policy.策略名 || policy.名称 || '允许提前全量·未满百留标可核'}</span>
        <span className="hint-inline">
          切满探针 ∪ 全量事件 ∪ 全量格 ∪ 审计同核：比例满百 + 回执 + 名册一致 + 实例全切 + 审计在，
          全量格才点亮；未切满一律钉缺口，绝不把未满百写成满百全量。
        </span>
      </div>

      <div className="grid">
        {Object.entries(d).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
      </div>

      <h2>旁侧计数格（与全量格同源）</h2>
      <div className="grid">
        <div className="stat">
          <div className="n">{side.已放行 ?? '-'}</div><div className="l">已放行</div>
        </div>
        <div className="stat">
          <div className="n">{side.合法全量 ?? '-'}</div><div className="l">合法全量（满百一致+未满百可核）</div>
        </div>
        <div className="stat stat-warn">
          <div className="n">{side.未满百全量 ?? early.length}</div><div className="l">未满百全量（留标可核）</div>
        </div>
        <div className="stat">
          <div className="n">{side.满百全量 ?? '-'}</div><div className="l">满百全量</div>
        </div>
        <div className={`stat ${(side.缺口未切满 ?? gaps.length) ? 'stat-danger' : ''}`}>
          <div className="n">{side.缺口未切满 ?? gaps.length}</div><div className="l">缺口未切满</div>
        </div>
        <div className="stat">
          <div className="n">{side.切满探针过线 ?? '-'}</div><div className="l">切满探针过线</div>
        </div>
        <div className={`stat ${splits.length ? 'stat-danger' : ''}`}>
          <div className="n">{splits.length}</div><div className="l">分裂告警</div>
        </div>
      </div>

      <h2>全量格 · 切满探针对读</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>发版单号</th><th>应用</th><th>比例百分列</th><th>全量时快照</th>
              <th>切满探针</th><th>切满差额</th><th>全量格</th><th>全量事件</th>
              <th>审计证据</th><th>对账结论</th><th>缺口标</th>
            </tr>
          </thead>
          <tbody>
            {cells.map((r) => (
              <tr key={`cell-${r.编号}`} data-full-cell={r.编号} data-cell={r.全量格}>
                <td>{r.发版单号}</td>
                <td>{r.应用 || '-'}</td>
                <td><span className={String(r.比例百分列) === '100%' ? 'ratio ratio-full' : 'ratio ratio-low'}>{r.比例百分列}</span></td>
                <td>{r.全量时比例 == null ? '-' : `${r.全量时比例}%`}</td>
                <td>
                  {r.切满探针过线
                    ? <span className="badge badge-ok">过线</span>
                    : <span className="badge badge-danger">未过线</span>}
                  {!r.切满探针过线 && (r.切满差分 || []).length > 0 && (
                    <span className="muted"> {(r.切满差分 || []).join('；')}</span>
                  )}
                </td>
                <td>{r.切满差额 == null ? '-' : r.切满差额}</td>
                <td>{cellBadge(r.全量格)}</td>
                <td className="mono">{r.全量事件 || '-'}</td>
                <td>{r.审计证据数 ?? 0}</td>
                <td>{r.对账结论}</td>
                <td className="gap-cell">{Array.isArray(r.缺口标) ? (r.缺口标.join('；') || '-') : '-'}</td>
              </tr>
            ))}
            {cells.length === 0 && <tr><td colSpan={11} className="muted">暂无已放行单。</td></tr>}
          </tbody>
        </table>
      </div>

      <h2>未满百全量</h2>
      {early.length === 0
        ? <p className="muted">暂无未满百提前全量单。</p>
        : (
          <div className="table-wrap">
            <table className="table-warn">
              <thead>
                <tr><th>发版单号</th><th>应用</th><th>比例百分列</th><th>全量时快照</th><th>切满差额</th><th>全量格</th><th>回执号</th></tr>
              </thead>
              <tbody>
                {early.map((r) => (
                  <tr key={`early-${r.编号}`} data-early-full={r.编号}>
                    <td>{r.发版单号}</td>
                    <td>{r.应用 || '-'}</td>
                    <td><span className="ratio ratio-low">{r.比例百分列}</span></td>
                    <td>{r.全量时比例 ?? '-'}%</td>
                    <td>{r.切满差额}</td>
                    <td>{cellBadge(r.全量格)}</td>
                    <td className="mono">{r.全量事件 || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

      {splits.length > 0 && (
        <>
          <h2 className="danger-text">分裂告警</h2>
          <div className="table-wrap">
            <table className="table-danger">
              <thead><tr><th>发版单号</th><th>金丝雀比例</th><th>全量格</th><th>缺口标</th><th>回执号</th><th>处置</th></tr></thead>
              <tbody>
                {splits.map((r) => (
                  <tr key={`split-${r.编号}`}>
                    <td>{r.发版单号}</td>
                    <td><span className="ratio ratio-low">{r.金丝雀比例}%</span></td>
                    <td>{cellBadge(r.全量格)}</td>
                    <td className="gap-cell">{(r.缺口标 || []).join('；')}</td>
                    <td className="mono">{r.全量回执号 || '-'}</td>
                    <td>去「对账」页重放收口</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h2>在途发版</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>发版单号</th><th>应用</th><th>目标版本</th><th>状态</th>
              <th>比例百分列</th><th>全量格</th><th>切满探针</th><th>当前环境</th><th>流水线</th>
            </tr>
          </thead>
          <tbody>
            {(data.在途 || []).map((r) => (
              <tr key={r.编号} data-release={r.编号} data-cell={r.全量格}>
                <td>{r.发版单号}</td><td>{r.应用}</td><td>{r.目标版本}</td><td>{r.状态}</td>
                <td>
                  <span className={r.金丝雀比例 < 100 ? 'ratio ratio-low' : 'ratio ratio-full'}>{r.比例百分列 || `${r.金丝雀比例}%`}</span>
                  {r.未满百全量 && <span className="badge badge-warn">未满百全量</span>}
                </td>
                <td>{cellBadge(r.全量格 || r.全量落成态)}</td>
                <td>
                  {r.状态 === '已放行'
                    ? (r.切满探针 && r.切满探针.过线
                      ? <span className="badge badge-ok">过线</span>
                      : <span className="badge badge-danger">未过线</span>)
                    : <span className="muted">-</span>}
                </td>
                <td>{r.当前环境 || '-'}</td><td>{r.流水线}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>全量回执（最近 20 笔）</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>回执号</th><th>发版单</th><th>全量时比例</th><th>切满差额</th><th>标记</th><th>状态</th><th>名册版本</th><th>切换实例</th><th>时间</th></tr>
          </thead>
          <tbody>
            {receipts.map((r, i) => (
              <tr key={r.回执号 || i}>
                <td className="mono">{r.回执号}</td>
                <td>{r.发版单编号}</td>
                <td>{r.全量时比例}%</td>
                <td>{r.切满差额}</td>
                <td>{r.未满百全量 ? <span className="badge badge-warn">未满百全量</span> : <span className="badge badge-ok">满百</span>}</td>
                <td>{r.回执状态}</td>
                <td className="mono">{r.名册版本 || '-'}</td>
                <td>{r.切换行数}/{r.实例总数}</td>
                <td>{r.时间}</td>
              </tr>
            ))}
            {receipts.length === 0 && <tr><td colSpan={9} className="muted">暂无全量回执。</td></tr>}
          </tbody>
        </table>
      </div>

      <h2>各环境版本</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>应用</th><th>环境</th><th>版本</th><th>更新时间</th></tr></thead>
          <tbody>
            {(data.环境版本 || []).map((v, i) => (
              <tr key={i}><td>{v.应用}</td><td>{v.环境}</td><td>{v.版本}</td><td>{v.更新时间}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
