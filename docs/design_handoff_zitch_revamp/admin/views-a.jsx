// Zitch Admin — views A: Overview, Users & KYC, Transactions, FX & Treasury
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
  const max = Math.max(...D.VOLUME_14D);
  return (
    <div>
      <PageHead title="Overview" sub="Tuesday, June 9 2026 — all systems nominal except one." />
      <div className="kpi-grid">
        <Kpi icon="users" label="Registered users" value="5,213" delta="184 this month" />
        <Kpi icon="txns" label="All-time volume" value="₦1.02bn" delta="₦74m this week" />
        <Kpi icon="chat" label="Linked WhatsApp numbers" value="3,098" delta="59.4% of users" />
        <Kpi icon="fx" label="FX converted (30d)" value="₦66.3m" delta="12.8%" />
      </div>
      <div className="grid-2-1">
        <Card title="Daily volume" sub="Last 14 days · ₦ millions" right={<Badge v="success">98.4% success</Badge>}>
          <div className="bars">
            {D.VOLUME_14D.map((v, i) => (
              <div key={i} className="bar-col" title={'₦' + v + 'm'}>
                <div className="bar" style={{ height: (v / max) * 100 + '%', opacity: i === D.VOLUME_14D.length - 1 ? 1 : 0.45 + (i / D.VOLUME_14D.length) * 0.5 }}></div>
              </div>
            ))}
          </div>
          <div className="bars-x"><span>May 27</span><span>Jun 2</span><span>Jun 9</span></div>
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
        <TxnTable rows={D.TXNS.slice(0, 6)} compact />
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
  const [q, setQ] = useState('');
  const [type, setType] = useState('all');
  const [sel, setSel] = useState(null);
  const TYPES = ['all', 'transfer', 'fx', 'fund', 'airtime', 'data', 'electricity', 'cable'];
  const rows = D.TXNS.filter((t) => (type === 'all' || t.type === type) &&
    (q === '' || (t.id + t.user + t.desc).toLowerCase().includes(q.toLowerCase())));
  return (
    <div>
      <PageHead title="Transactions" sub="₦1.02bn processed · 12 shown" right={<SearchBox value={q} onChange={setQ} placeholder="Search reference, user…" />} />
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
              <div className="note warn"><Icon name="alert" size={15} /> Auto-flagged: velocity &gt; ₦1m in 24h. Review before release.</div>
            )}
            <div className="drawer-actions">
              <button className="btn ghost" disabled={!can.money} onClick={() => { toast('Requery sent to provider for ' + sel.id); setSel(null); }}>Requery provider</button>
              {sel.status === 'flagged'
                ? <button className="btn primary" disabled={!can.money} onClick={() => { toast(sel.id + ' released — written to audit log'); setSel(null); }}>Release payment</button>
                : <button className="btn danger" disabled={!can.money} onClick={() => { toast('Refund initiated for ' + sel.id + ' — written to audit log'); setSel(null); }}>Refund to wallet</button>}
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
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(null);
  const rows = D.USERS.filter((u) => q === '' || (u.name + u.phone + u.email).toLowerCase().includes(q.toLowerCase()));
  const KYC_LABEL = { face: 'Tier 3 · Face', nin: 'Tier 2 · NIN', bvn: 'Tier 1 · BVN', pending: 'Pending' };
  return (
    <div>
      <PageHead title="Users & KYC" sub="5,213 users · 8 shown" right={<SearchBox value={q} onChange={setQ} placeholder="Search name, phone, email…" />} />
      <Card pad={false}>
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
              <button className="btn ghost" disabled={!can.users} onClick={() => toast('PIN reset link sent to ' + sel.name)}>Reset PIN</button>
              {sel.status === 'frozen'
                ? <button className="btn primary" disabled={!can.users} onClick={() => { toast(sel.name + ' unfrozen — written to audit log'); setSel(null); }}>Unfreeze account</button>
                : <button className="btn danger" disabled={!can.users} onClick={() => { toast(sel.name + ' frozen — written to audit log'); setSel(null); }}>Freeze account</button>}
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
  const [margin, setMargin] = useState(60);
  const [corridors, setCorridors] = useState({ 'NGN/USD': true, 'NGN/GBP': true, 'NGN/CAD': true, 'NGN/CNY': false });
  return (
    <div>
      <PageHead title="FX & Treasury" sub="Rates from Fincra · customer rate = provider rate + margin" />
      <div className="grid-2-1">
        <Card title="Corridors & live rates" sub={'Customer margin: ' + margin + ' bps'} pad={false}>
          <table className="tbl">
            <thead><tr><th>Corridor</th><th className="r">Provider rate</th><th className="r">Customer rate</th><th className="r">24h volume</th><th>Settlement</th></tr></thead>
            <tbody>
              {D.RATES.map((r) => {
                const cust = r.provider * (1 + margin / 10000);
                const on = corridors[r.pair];
                return (
                  <tr key={r.pair}>
                    <td><b>{r.flag} {r.pair}</b>{r.pair === 'NGN/CNY' && <div className="sm dim">Quote/display only — settlement blocked</div>}</td>
                    <td className="r num">₦{r.provider.toLocaleString('en-NG', { minimumFractionDigits: 2 })}</td>
                    <td className="r num"><b>₦{cust.toLocaleString('en-NG', { minimumFractionDigits: 2 })}</b></td>
                    <td className="r num">{r.vol24 ? D.fmtN(r.vol24, 'NGN') : '—'}</td>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <Toggle on={on} disabled={!can.money || r.pair === 'NGN/CNY'} label={r.pair}
                          onChange={(v) => { setCorridors({ ...corridors, [r.pair]: v }); toast(r.pair + ' settlement ' + (v ? 'enabled' : 'disabled') + ' — written to audit log'); }} />
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
            <p className="dim sm" style={{ margin: '10px 0 14px' }}>At {margin} bps, ₦500,000 → USD yields ${(500000 / (1474.10 * (1 + margin / 10000))).toFixed(2)} for the customer.</p>
            <button className="btn primary w100" disabled={!can.money} onClick={() => toast('fx_margin_bps set to ' + margin + ' — written to audit log')}>Apply margin</button>
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
