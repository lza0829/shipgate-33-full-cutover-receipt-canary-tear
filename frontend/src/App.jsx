import React from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import Overview from './pages/Overview'
import Apps from './pages/Apps'
import Releases from './pages/Releases'
import Promote from './pages/Promote'
import Windows from './pages/Windows'
import Rollback from './pages/Rollback'
import Health from './pages/Health'
import Reconcile from './pages/Reconcile'
import Settings from './pages/Settings'
import Audit from './pages/Audit'

const links = [
  ['/', '总览'],
  ['/应用', '应用'],
  ['/发版单', '发版单'],
  ['/晋升', '晋升'],
  ['/窗口', '窗口'],
  ['/回滚', '回滚'],
  ['/健康', '健康'],
  ['/对账', '对账'],
  ['/设置', '设置'],
  ['/审计', '审计'],
]

export default function App() {
  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand-block">
          <div className="brand">发版闸</div>
          <div className="brand-sub">入闸 · 晋升 · 放行回滚窗 · 发布对账</div>
        </div>
        <nav className="nav">
          {links.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => (isActive ? 'active' : '')}>
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/应用" element={<Apps />} />
          <Route path="/发版单" element={<Releases />} />
          <Route path="/晋升" element={<Promote />} />
          <Route path="/窗口" element={<Windows />} />
          <Route path="/回滚" element={<Rollback />} />
          <Route path="/健康" element={<Health />} />
          <Route path="/对账" element={<Reconcile />} />
          <Route path="/设置" element={<Settings />} />
          <Route path="/审计" element={<Audit />} />
        </Routes>
      </main>
    </div>
  )
}
