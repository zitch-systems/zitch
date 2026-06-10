// Zitch Admin — shared UI primitives
const { useState, useEffect, useMemo, useContext, createContext } = React;

const ICON_PATHS = {
  home: 'M3 10.5 12 3l9 7.5M5 9.5V21h5v-6h4v6h5V9.5',
  users: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
  txns: 'M7 17l-4-4 4-4M3 13h14M17 7l4 4-4 4M21 11H7',
  fx: 'M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6',
  chat: 'M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z',
  megaphone: 'M3 11l18-7v18l-18-7v-4zM7.5 15.5l1 5 3-1-1.2-4.3',
  spark: 'M12 3l1.9 5.7L19.5 10l-5.6 1.3L12 17l-1.9-5.7L4.5 10l5.6-1.3L12 3zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16z',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35',
  x: 'M18 6 6 18M6 6l12 12',
  check: 'M20 6 9 17l-5-5',
  send: 'M22 2 11 13M22 2l-7 20-4-9-9-4 22-7z',
  alert: 'M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01',
  arrow: 'M5 12h14M12 5l7 7-7 7',
  bot: 'M12 8V4M8 8h8a4 4 0 0 1 4 4v4a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4v-4a4 4 0 0 1 4-4zM9 13h.01M15 13h.01',
  user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  pause: 'M10 4H6v16h4zM18 4h-4v16h4z',
  lock: 'M19 11H5a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2zM7 11V7a5 5 0 0 1 10 0v4',
  logout: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9',
  card: 'M2 6a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2zM2 10h20',
  refresh: 'M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15',
};

function Icon({ name, size = 18, sw = 1.8, style }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" style={style} aria-hidden="true">
      {ICON_PATHS[name].split('M').filter(Boolean).map((p, i) => <path key={i} d={'M' + p} />)}
    </svg>
  );
}

const STATUS_COLORS = {
  success: { bg: '#E6F6EE', fg: '#0B7A43' }, active: { bg: '#E6F6EE', fg: '#0B7A43' },
  operational: { bg: '#E6F6EE', fg: '#0B7A43' }, done: { bg: '#E6F6EE', fg: '#0B7A43' },
  delivered: { bg: '#E6F6EE', fg: '#0B7A43' },
  pending: { bg: '#FFF4E0', fg: '#9A5B00' }, degraded: { bg: '#FFF4E0', fg: '#9A5B00' },
  draft: { bg: '#EEF1F4', fg: '#4A4E57' }, paused: { bg: '#EEF1F4', fg: '#4A4E57' }, none: { bg: '#EEF1F4', fg: '#697077' },
  failed: { bg: '#FDEAEA', fg: '#B42318' }, flagged: { bg: '#FDEAEA', fg: '#B42318' }, frozen: { bg: '#FDEAEA', fg: '#B42318' },
  sending: { bg: '#E8F4FB', fg: '#0B6196' }, human: { bg: '#E8F4FB', fg: '#0B6196' }, whatsapp: { bg: '#E6F6EE', fg: '#0B7A43' },
  bot: { bg: '#E7F6F5', fg: '#00847B' }, app: { bg: '#E7F6F5', fg: '#00847B' },
  face: { bg: '#E6F6EE', fg: '#0B7A43' }, nin: { bg: '#E7F6F5', fg: '#00847B' }, bvn: { bg: '#E8F4FB', fg: '#0B6196' },
};
function Badge({ v, children }) {
  const c = STATUS_COLORS[v] || { bg: '#EEF1F4', fg: '#4A4E57' };
  return <span className="badge" style={{ background: c.bg, color: c.fg }}>{children || v}</span>;
}

function Card({ title, sub, right, children, pad = true, style }) {
  return (
    <div className="card" style={style}>
      {(title || right) && (
        <div className="card-head">
          <div>
            <div className="card-title">{title}</div>
            {sub && <div className="card-sub">{sub}</div>}
          </div>
          {right && <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>{right}</div>}
        </div>
      )}
      <div style={pad ? { padding: '0 20px 20px' } : {}}>{children}</div>
    </div>
  );
}

function Kpi({ label, value, delta, deltaDir, icon }) {
  return (
    <div className="card kpi">
      <div className="kpi-ic"><Icon name={icon} size={19} /></div>
      <div>
        <div className="kpi-l">{label}</div>
        <div className="kpi-v num">{value}</div>
        {delta && <div className={'kpi-d ' + (deltaDir === 'down' ? 'down' : 'up')}>{deltaDir === 'down' ? '▼' : '▲'} {delta}</div>}
      </div>
    </div>
  );
}

function Toggle({ on, onChange, disabled, label }) {
  return (
    <button type="button" className={'tgl' + (on ? ' on' : '') + (disabled ? ' dis' : '')} aria-label={label || 'toggle'}
      onClick={() => !disabled && onChange(!on)}>
      <span className="knob"></span>
    </button>
  );
}

function Drawer({ open, onClose, title, children, width = 460 }) {
  if (!open) return null;
  return (
    <div className="drawer-veil" onClick={onClose}>
      <div className="drawer" style={{ width }} onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div className="card-title">{title}</div>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" size={17} /></button>
        </div>
        <div className="drawer-body">{children}</div>
      </div>
    </div>
  );
}

// ---- Role / RBAC ----
const ROLES = ['super_admin', 'finance', 'support', 'read_only'];
const RoleCtx = createContext({ role: 'super_admin', can: () => true });
function useRole() { return useContext(RoleCtx); }
const CAN = {
  super_admin: { wa: true, broadcast: true, money: true, users: true, ai: true, settings: true },
  finance: { wa: false, broadcast: false, money: true, users: true, ai: false, settings: false },
  support: { wa: true, broadcast: true, money: false, users: false, ai: false, settings: false },
  read_only: { wa: false, broadcast: false, money: false, users: false, ai: false, settings: false },
};

// ---- Toast bus ----
function ToastHost({ toasts }) {
  return (
    <div className="toasts">
      {toasts.map((t) => <div key={t.id} className="toast"><Icon name="check" size={15} /> {t.text}</div>)}
    </div>
  );
}

function SearchBox({ value, onChange, placeholder }) {
  return (
    <div className="searchbox">
      <Icon name="search" size={15} />
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder || 'Search…'} />
    </div>
  );
}

function Empty({ text }) {
  return <div className="empty">{text}</div>;
}

Object.assign(window, { Icon, Badge, Card, Kpi, Toggle, Drawer, RoleCtx, useRole, ROLES, CAN, ToastHost, SearchBox, Empty });
