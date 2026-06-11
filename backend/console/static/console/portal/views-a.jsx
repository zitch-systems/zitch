// Zitch Admin — views A: Overview, Users & KYC, Transactions, FX & Treasury
// All collections come from /api/admin/bootstrap (applied into window.ZADM);
// every action POSTs to its audited /api/admin/* endpoint before touching UI state.
const { useState } = React;
const D = window.ZADM;

function PageHead({ title, sub, right }) {
  return (
    <div className="page-head">
      <div><h1>{title}</h1>{sub && <p>{sub}</p>}</div>
      {right}
    </div>
  );
}

// ================= OVERVIEW =================
function Overview({ toast }) {
  const k = D.KPIS || {};
  const max = Math.max(1, ...D.VOLUME_14D);
  const today = new Date().toLocaleDateString('en-NG', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
  const waShare = k.users ? Math.round((k.wa_links / k.users) * 1000) / 10 : 0;
  const dayLabel = (offset) => {
    const d = new Date(Date.now() - offset * 86400000);
    return d.toLocaleDateString('en-NG', { month: 'short', day: 'numeric' });
  };
  return (
    <div>
      <PageHead title="Overview" sub={today + ' — live platform data.'} />
      <div className="kpi-grid">
        <Kpi icon="users" label="Registered users" value={(k.users || 0).toLocaleString()} delta={(k.pending_kyc || 0) + ' KYC reviews pending'} />
        <Kpi icon="txns" label="Volume (24h)" value={fmtBig(k.vol24h)} delta={(k.txn24h || 0) + ' transactions'} />
        <Kpi icon="chat" label="Linked WhatsApp numbers" value={(k.wa_links || 0).toLocaleString()} delta={waShare + '% of users'} />
        <Kpi icon="fx" label="NGN wallet liability" value={fmtBig(k.ngn_liability)} delta={(k.flagged || 0) + ' flagged txns'} deltaDir={k.flagged ? 'down' : 'up'} />
      </div>
      <div className="grid-2-1">
        <Card title="Daily volume" sub="Last 14 days · ₦ millions" right={<Badge v={k.flagged ? 'pending' : 'success'}>{(k.active_loans || 0) + ' active loans'}</Badge>}>
          <div className="bars">
            {D.VOLUME_14D.map((v, i) => (
              <div key={i} className="bar-col" title={'₦' + v + 'm'}>
                <div className="bar" style={{ height: (v / max) * 100 + '%', opacity: i === D.VOLUME_14D.length - 1 ? 1 : 0.45 + (i / D.VOLUME_14D.length) * 0.5 }}></div>
              </div>
            ))}
          </div>
          <div className="bars-x"><span>{dayLabel(13)}</span><span>{dayLabel(7)}</span><span>{dayLabel(0)}</span></div>
        </Card>
        <Card title="Provider health" sub="Live integrations">
          <div className="prov-list">
            {D.PROVIDERS.map((p) => (
              <div key={p.name} className="prov-row">
                <div><div className="prov-name">{p.name}</div><div className="prov-role">{p.role}</div></div>
                <div style={{ textAlign: 'right' }}><Badge v={p.status} /><div className="prov-up num">{p.uptime}</div></div>
              </div>
            ))}
          </div>
        </Card>
      </div>
      <Card title="Latest transactions" sub="Across app and WhatsApp" pad={false}>
        {D.TXNS.length ? <TxnTable rows={D.TXNS.slice(0, 6)} compact /> : <Empty text="No transactions yet." />}
      </Card>
    </div>
  );
}

// ================= TRANSACTIONS =================
function TxnTable({ rows, compact, onRow }) {
  return (
    <table className="tbl">
      <thead><tr><th>Reference</th><th>User</th><th>Detail</th><th>Channel</th><th className="r">Amount</th><th>Status</th><th className="r">When</th></tr></thead>
      <tbody>
        {rows.map((t) => (
          <tr key={t.id} className={onRow ? 'click' : ''} onClick={() => onRow && onRow(t)}>
            <td className="mono">{t.id}</td>
            <td>{t.user}</td>
            <td className="dim">{t.desc}</td>
            <td><Badge v={t.channel} /></td>
            <td className={'r num ' + (t.amt > 0 ? 'pos' : '')}>{D.fmtN(t.amt, t.cur)}</td>
            <td><Badge v={t.status} /></td>
            <td className="r dim num">{D.fmtT(t.time)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Transactions({ toast }) {
  const { can } = useRole();
  const [txns, setTxns] = useState(D.TXNS);
  const [q, setQ] = useState('');
  const [type, setType] = useState('all');
  const [sel, setSel] = useState(null);
  const [busy, setBusy] = useState(false);
  const TYPES = ['all', 'transfer', 'fx', 'fund', 'airtime', 'data', 'electricity', 'cable'];
  const rows = txns.filter((t) => (type === 'all' || t.type === type) &&
    (q === '' || (t.id + t.user + t.desc).toLowerCase().includes(q.toLowerCase())));

  const patchTxn = (id, p) => {
    const next = txns.map((t) => (t.id === id ? { ...t, ...p } : t));
    setTxns(next); D.TXNS = next;
  };
  const requery = async () => {
    setBusy(true);
    const r = await doAct(toast, '/txn/requery', { ref: sel.id });
    setBusy(false);
    if (r) {
      patchTxn(sel.id, { status: r.status === 'success' ? 'success' : r.status === 'failed' ? 'failed' : 'pending', canRequery: r.status === 'pending' });
      toast(sel.id + ' requeried — provider says ' + r.status + ' (audit logged)');
      setSel(null);
    }
  };
  const setFlag = async (flagged) => {
    setBusy(true);
    const r = await doAct(toast, '/txn/flag', { ref: sel.id, flagged });
    setBusy(false);
    if (r) {
      patchTxn(sel.id, { status: r.status, flagged });
      toast(sel.id + (flagged ? ' flagged for review' : ' released') + ' — written to audit log');
      setSel(null);
    }
  };

  return (
    <div>
      <PageHead title="Transactions" sub={rows.length + ' shown of ' + txns.length + ' loaded'} right={<SearchBox value={q} onChange={setQ} placeholder="Search reference, user…" />} />
      <div className="chips">
        {TYPES.map((t) => <button key={t} className={'chip' + (type === t ? ' on' : '')} onClick={() => setType(t)}>{t === 'fx' ? 'FX' : t[0].toUpperCase() + t.slice(1)}</button>)}
      </div>
      <Card pad={false}>
        {rows.length ? <TxnTable rows={rows} onRow={setSel} /> : <Empty text="No transactions match." />}
      </Card>
      <Drawer open={!!sel} onClose={() => setSel(null)} title={sel ? sel.id : ''}>
        {sel && (
          <div>
            <div className="kv"><span>User</span><b>{sel.user}</b></div>
            <div className="kv"><span>Detail</span><b>{sel.desc}</b></div>
            <div className="kv"><span>Amount</span><b className="num">{D.fmtN(sel.amt, sel.cur)}</b></div>
            <div className="kv"><span>Fee</span><b className="num">{D.fmtN(sel.fee, 'NGN')}</b></div>
            <div className="kv"><span>Channel</span><Badge v={sel.channel} /></div>
            <div className="kv"><span>Status</span><Badge v={sel.status} /></div>
            {sel.status === 'flagged' && (
              <div className="note warn"><Icon name="alert" size={15} /> Flagged for compliance review. Release returns it to its settled status.</div>
            )}
            {sel.canRequery && (
              <div className="note"><Icon name="refresh" size={15} /> Provider-pending: requery asks the provider for the truth and settles or refunds — the same path as the reconcile cron.</div>
            )}
            <div className="drawer-actions">
              <button className="btn ghost" disabled={!can.money || !sel.canRequery || busy} onClick={requery}>Requery provider</button>
              {sel.status === 'flagged'
                ? <button className="btn primary" disabled={!can.money || busy} onClick={() => setFlag(false)}>Release payment</button>
                : <button className="btn danger" disabled={!can.money || busy} onClick={() => setFlag(true)}>Flag for review</button>}
            </div>
            {!can.money && <p className="rbac-note"><Icon name="lock" size={13} /> Your role can't perform money actions.</p>}
          </div>
        )}
      </Drawer>
    </div>
  );
}

// ================= USERS & KYC =================
function Users({ toast }) {
  const { can } = useRole();
  const [users, setUsers] = useState(D.USERS);
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(null);
  const [busy, setBusy] = useState(false);
  const rows = users.filter((u) => q === '' || (u.name + u.phone + u.email).toLowerCase().includes(q.toLowerCase()));
  const KYC_LABEL = { face: 'Tier 3 · Face', nin: 'Tier 2 · NIN', bvn: 'Tier 1 · BVN', pending: 'Pending' };
  const total = (D.KPIS && D.KPIS.users) || users.length;

  const patchUser = (uid, p) => {
    const next = users.map((u) => (u.uid === uid ? { ...u, ...p } : u));
    setUsers(next); D.USERS = next;
  };
  const setStatus = async (status) => {
    setBusy(true);
    const r = await doAct(toast, '/users/status', { uid: sel.uid, status });
    setBusy(false);
    if (r) {
      patchUser(sel.uid, { status });
      toast(sel.name + (status === 'frozen' ? ' frozen' : ' unfrozen') + ' — written to audit log');
      setSel(null);
    }
  };
  const unlockPin = async () => {
    setBusy(true);
    const r = await doAct(toast, '/users/pin_unlock', { uid: sel.uid });
    setBusy(false);
    if (r) toast('PIN lockout cleared for ' + sel.name + ' (audit logged)');
  };

  return (
    <div>
      <PageHead title="Users & KYC" sub={total.toLocaleString() + ' users · ' + rows.length + ' shown'} right={<SearchBox value={q} onChange={setQ} placeholder="Search name, phone, email…" />} />
      <Card pad={false}>
        {rows.length ? (
          <table className="tbl">
            <thead><tr><th>User</th><th>Contact</th><th>KYC</th><th>WhatsApp</th><th className="r">NGN balance</th><th>Status</th></tr></thead>
            <tbody>
              {rows.map((u) => (
                <tr key={u.id} className="click" onClick={() => setSel(u)}>
                  <td><div className="u-cell"><span className="avatar">{u.name.split(' ').map((w) => w[0]).join('')}</span><div><b>{u.name}</b><div className="dim sm">{u.id} · joined {u.joined}</div></div></div></td>
                  <td className="dim">{u.phone}<div className="sm">{u.email}</div></td>
                  <td><Badge v={u.kyc}>{KYC_LABEL[u.kyc]}</Badge></td>
                  <td><Badge v={u.wa === 'active' ? 'whatsapp' : u.wa}>{u.wa === 'active' ? 'linked' : u.wa}</Badge></td>
                  <td className="r num">{D.fmtN(u.wallets.NGN, 'NGN')}</td>
                  <td><Badge v={u.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty text="No users match." />}
      </Card>
      <Drawer open={!!sel} onClose={() => setSel(null)} title={sel ? sel.name : ''}>
        {sel && (
          <div>
            <div className="kv"><span>KYC level</span><Badge v={sel.kyc}>{KYC_LABEL[sel.kyc]}</Badge></div>
            <div className="kv"><span>Account status</span><Badge v={sel.status} /></div>
            <div className="kv"><span>WhatsApp</span><b>{sel.wa === 'active' ? 'Linked · AI ' + (sel.aiEnabled ? 'on' : 'off') : sel.wa}</b></div>
            <div className="kv"><span>Marketing opt-in</span><b>{sel.marketingOptIn ? 'Yes' : 'No'}</b></div>
            <h4 className="drawer-sec">Currency wallets</h4>
            <div className="wallets">
              {Object.entries(sel.wallets).map(([c, v]) => (
                <div key={c} className={'wallet' + (v > 0 ? '' : ' zero')}><span>{c}</span><b className="num">{D.fmtN(v, c)}</b></div>
              ))}
            </div>
            <div className="drawer-actions">
              <button className="btn ghost" disabled={!can.users || busy} onClick={unlockPin}>Unlock PIN</button>
              {sel.status === 'frozen'
                ? <button className="btn primary" disabled={!can.users || busy} onClick={() => setStatus('active')}>Unfreeze account</button>
                : <button className="btn danger" disabled={!can.users || busy} onClick={() => setStatus('frozen')}>Freeze account</button>}
            </div>
            {!can.users && <p className="rbac-note"><Icon name="lock" size={13} /> Your role can't modify users.</p>}
          </div>
        )}
      </Drawer>
    </div>
  );
}

// ================= FX & TREASURY =================
function Fx({ toast }) {
  const { can } = useRole();
  const liveMargin = (D.RATES[0] && D.RATES[0].margin) || 60;
  const [margin, setMargin] = useState(liveMargin);
  const [applied, setApplied] = useState(liveMargin);
  const [corridors, setCorridors] = useState(() => {
    const out = {};
    D.RATES.forEach((r) => { out[r.pair] = !!r.settle; });
    return out;
  });
  const [busy, setBusy] = useState(false);

  const applyMargin = async () => {
    setBusy(true);
    const r = await doAct(toast, '/fx/margin', { bps: margin });
    setBusy(false);
    if (r) {
      setApplied(r.margin);
      D.RATES = D.RATES.map((x) => ({ ...x, margin: r.margin }));
      toast('fx_margin_bps set to ' + r.margin + ' — applied to every new quote (audit logged)');
    }
  };
  const toggleCorridor = async (pair, v) => {
    const ccy = pair.split('/')[1];
    const r = await doAct(toast, '/fx/corridor', { currency: ccy, enabled: v });
    if (r) {
      setCorridors({ ...corridors, [pair]: v });
      D.RATES = D.RATES.map((x) => (x.pair === pair ? { ...x, settle: v } : x));
      toast(pair + ' settlement ' + (v ? 'enabled' : 'paused') + ' — written to audit log');
    }
  };

  return (
    <div>
      <PageHead title="FX & Treasury" sub="Rates from Fincra · customer rate = provider rate + margin" />
      <div className="grid-2-1">
        <Card title="Corridors & live rates" sub={'Customer margin: ' + applied + ' bps'} pad={false}>
          <table className="tbl">
            <thead><tr><th>Corridor</th><th className="r">Provider rate</th><th className="r">Customer rate</th><th className="r">24h volume</th><th>Settlement</th></tr></thead>
            <tbody>
              {D.RATES.map((r) => {
                const provider = r.provider || 0;
                const cust = provider * (1 + margin / 10000);
                const on = corridors[r.pair];
                return (
                  <tr key={r.pair}>
                    <td><b>{r.flag} {r.pair}</b>{r.pair === 'NGN/CNY' && <div className="sm dim">Quote/display only — settlement blocked</div>}</td>
                    <td className="r num">₦{provider.toLocaleString('en-NG', { minimumFractionDigits: 2 })}</td>
                    <td className="r num"><b>₦{cust.toLocaleString('en-NG', { minimumFractionDigits: 2 })}</b></td>
                    <td className="r num">{r.vol24 ? D.fmtN(r.vol24, 'NGN') : '—'}</td>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <Toggle on={on} disabled={!can.money || r.pair === 'NGN/CNY'} label={r.pair}
                          onChange={(v) => toggleCorridor(r.pair, v)} />
                        {r.pair === 'NGN/CNY' && <Icon name="lock" size={13} style={{ color: '#7B828E' }} />}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
        <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
          <Card title="FX margin" sub="fx_margin_bps — applied to every quote">
            <div className="margin-edit">
              <input type="range" min="0" max="300" step="5" value={margin} disabled={!can.money} onChange={(e) => setMargin(+e.target.value)} />
              <div className="margin-val num">{margin} <span>bps</span></div>
            </div>
            <p className="dim sm" style={{ margin: '10px 0 14px' }}>
              {(D.RATES[0] && D.RATES[0].provider)
                ? 'At ' + margin + ' bps, ₦500,000 → USD yields $' + (500000 / (D.RATES[0].provider * (1 + margin / 10000))).toFixed(2) + ' for the customer.'
                : 'Margin is added to the provider rate on every conversion quote.'}
            </p>
            <button className="btn primary w100" disabled={!can.money || busy || margin === applied} onClick={applyMargin}>
              {busy ? 'Applying…' : margin === applied ? 'Margin applied' : 'Apply margin'}
            </button>
            {!can.money && <p className="rbac-note"><Icon name="lock" size={13} /> Finance or super admin only.</p>}
          </Card>
          <Card title="Float balances" sub="Treasury wallets by provider">
            {D.FLOAT.map((f) => (
              <div key={f.cur} className="kv tight"><span>{f.cur} <em className="dim">· {f.provider}</em></span><b className="num">{f.sym}{f.bal.toLocaleString('en-NG', { minimumFractionDigits: 2 })}</b></div>
            ))}
          </Card>
        </div>
      </div>
      <Card title="Quote safety rules" sub="Enforced by the settlement service — not editable here">
        <div className="rules">
          <div className="rule"><Icon name="check" size={15} /> Quotes are single-use and expiry-checked (60s TTL) — a stale rate is never settled.</div>
          <div className="rule"><Icon name="check" size={15} /> Settlement is atomic: debit source, credit target, ledger pair tagged with currency.</div>
          <div className="rule"><Icon name="check" size={15} /> CNY is corridor-blocked from settlement until a partner is live (quotes only).</div>
        </div>
      </Card>
    </div>
  );
}

Object.assign(window, { PageHead, Overview, Transactions, TxnTable, Users, Fx });
