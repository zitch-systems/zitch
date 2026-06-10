// Zitch Admin — app shell: sidebar, topbar, role switcher, routing
const { useState } = React;
function AdminApp() {
  const [view, setView] = useState('overview');
  const [role, setRole] = useState('super_admin');
  const [toasts, setToasts] = useState([]);

  const toast = (text) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3200);
  };

  const NAV = [
    { key: 'overview', label: 'Overview', icon: 'home' },
    { key: 'users', label: 'Users & KYC', icon: 'users' },
    { key: 'kyc', label: 'KYC queue', icon: 'shield' },
    { key: 'txns', label: 'Transactions', icon: 'txns' },
    { key: 'fx', label: 'FX & Treasury', icon: 'fx' },
    { key: 'products', label: 'Products', icon: 'card' },
    { key: 'wa', label: 'WhatsApp', icon: 'chat' },
    { key: 'broadcasts', label: 'Broadcasts', icon: 'megaphone' },
    { key: 'ai', label: 'AI controls', icon: 'spark' },
    { key: 'recon', label: 'Providers & recon', icon: 'refresh' },
    { key: 'audit', label: 'Audit log', icon: 'file' },
    { key: 'settings', label: 'Settings & team', icon: 'gear' },
  ];

  const VIEWS = {
    overview: Overview, users: Users, kyc: KycQueue, txns: Transactions, fx: Fx, products: Products,
    wa: WaInbox, broadcasts: Broadcasts, ai: AiControls, recon: Recon, audit: Audit, settings: Settings,
  };
  const View = VIEWS[view];
  const ctx = { role, can: CAN[role] };

  return (
    <RoleCtx.Provider value={ctx}>
      <div className="shell" data-screen-label={'Admin · ' + view}>
        <aside className="side">
          <div className="side-brand">
            <img src="assets/brand/zitch-ribbon.png" alt="Zitch" />
            <span>ZITCH <em>Admin</em></span>
          </div>
          <nav className="side-nav">
            {NAV.map((n) => (
              <button key={n.key} className={'side-link' + (view === n.key ? ' on' : '')} onClick={() => setView(n.key)}>
                <Icon name={n.icon} size={17} /> {n.label}
                {n.key === 'wa' && <span className="side-pill">1</span>}
              </button>
            ))}
          </nav>
          <div className="side-foot">
            <a className="side-link" href="Zitch Landing v3.html"><Icon name="logout" size={16} /> Back to site</a>
          </div>
        </aside>
        <div className="main">
          <header className="topbar">
            <div className="env-pill"><span className="dot"></span> Production</div>
            <div style={{ flex: 1 }}></div>
            <label className="role-pick">
              <Icon name="shield" size={15} />
              <span className="sm dim">Viewing as</span>
              <select value={role} onChange={(e) => setRole(e.target.value)}>
                {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </label>
            <div className="me"><span className="avatar">AN</span> Amara Nwosu</div>
          </header>
          <main className="content">
            <View toast={toast} />
          </main>
        </div>
        <ToastHost toasts={toasts} />
      </div>
    </RoleCtx.Provider>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<AdminApp />);
