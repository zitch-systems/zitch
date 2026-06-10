// Zitch Admin — views A: Overview, Users & KYC, Transactions, FX & Treasury (live)
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
  const s = D.SUMMARY;
  if (!s) return <Empty text="Loading overview…" />;
  const series = D.VOLUME_14D.length ? D.VOLUME_14D : [0];
  const max = Math.max(...series, 1);
  const today = new Date().toLocaleDateString('en-NG', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
  const xlab = (i) => new Date(Date.now() - (13 - i) * 86400000).toLocaleDateString('en-NG', { month: 'short', day: 'numeric' });
  return (
    <div>
      <PageHead title="Overview" sub={today + ' — live from the ledger.'} />
      <div className="kpi-grid">
        <Kpi icon="users" label="Registered users" value={s.users.toLocaleString()} delta={s.users_month + ' this month'} />
        <Kpi icon="txns" label="All-time volume" value={D.fmtM(s.volume_all)} delta={D.fmtM(s.volume_week) + ' this week'} />
        <Kpi icon="chat" label="Linked WhatsApp numbers" value={s.wa_linked.toLocaleString()} delta={s.users ? (100 * s.wa_linked / s.users).toFixed(1) + '% of users' : '—'} />
        <Kpi icon="fx" label="FX converted (30d)" value={D.fmtM(s.fx_30d)} />
      </div>
      <div className="grid-2-1">
        <Card title="Daily volume" sub="Last 14 days" right={<Badge v="success">{s.success_rate}% success</Badge>}>
          <div className="bars">
            {series.map((v, i) => (
              <div key={i} className="bar-col" title={D.fmtM(v)}>
                <div className="bar" style={{ height: (v / max) * 100 + '%', opacity: i === series.length - 1 ? 1 : 0.45 + (i / series.length) * 0.5 }}></div>
              </div>
            ))}
          </div>
          <div className="bars-x"><span>{xlab(0)}</span><span>{xlab(7)}</span><span>{xlab(13)}</span></div>
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
        {s.latest.length ? <TxnTable rows={s.latest} compact /> : <Empty text="No transactions yet." />}
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

function Transactions({ toast, refresh }) {
  const { can } = useRole();
  const [q, setQ] = useState('');
  const [type, setType] = useState('all');
  const [sel, setSel] = useState(null);
  const [rows, setRows] = useState(D.TXNS);
  const TYPES = ['all', 'transfer', 'fx', 'fund', 'airtime', 'data', 'electricity', 'cable'];
  const refetch = (nq, ntype) => ZAPI.load.txns(nq, ntype).then(() => setRows(D.TXNS)).catch((e) => toast('⚠ ' + e.message));
  const requery = async () => {
    try {
      const r = await ZAPI.txnRequery(sel.id);
      toast(sel.id + ' requeried — now ' + r.status + ' (audit logged)');
      setSel(null); refetch(q, type);
    } catch (e) { toast('⚠ ' + e.message); }
  };
  return (
    <div>
      <PageHead title="Transactions" sub={rows.length + ' shown'} right={<SearchBox value={q} onChange={(v) => { setQ(v); refetch(v, type); }} placeholder="Search reference, user…" />} />
      <div className="chips">
        {TYPES.map((t) => <button key={t} className={'chip' + (type === t ? ' on' : '')} onClick={() => { setType(t); refetch(q, t); }}>{t === 'fx' ? 'FX' : t[0].toUpperCase() + t.slice(1)}</button>)}
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
            <div className="kv"><span>Channel</span><Badge v={sel.channel} /></div>
            <div className="kv"><span>Status</span><Badge v={sel.status} /></div>
            {sel.canRequery
              ? <div className="note warn"><Icon name="alert" size={15} /> Provider timeout — money held PENDING. Requery settles it (success) or refunds it (definitive failure), exactly like the reconcile cron.</div>
              : <div className="note"><Icon name="check" size={15} /> Settled. Failed payouts auto-refund via the reversal webhook; purchases via reconciliation.</div>}
            <div className="drawer-actions">
              <button className="btn primary" disabled={!can.money || !sel.canRequery} onClick={requery}>Requery provider</button>
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
  const [rows, setRows] = useState(D.USERS);
  const KYC_LABEL = { face: 'Tier 3 · Face', nin: 'Tier 2 · NIN', bvn: 'Tier 1 · BVN', pending: 'Pending' };
  const refetch = (nq) => ZAPI.load.users(nq).then(() => setRows(D.USERS)).catch((e) => toast('⚠ ' + e.message));
  const act = async (action, label) => {
    try {
      await ZAPI.userAction(sel.id, action);
      toast(sel.name + ' ' + label + ' (audit logged)');
      setSel(null); refetch(q);
    } catch (e) { toast('⚠ ' + e.message); }
  };
  return (
    <div>
      <PageHead title="Users & KYC" sub={D.USERS_TOTAL.toLocaleString() + ' users · ' + rows.length + ' shown'} right={<SearchBox value={q} onChange={(v) => { setQ(v); refetch(v); }} placeholder="Search name, phone, email…" />} />
      <Card pad={false}>
        {rows.length ? (
          <table className="tbl">
            <thead><tr><th>User</th><th>Contact</th><th>KYC</th><th>WhatsApp</th><th className="r">NGN balance</th><th>Status</th></tr></thead>
            <tbody>
              {rows.map((u) => (
                <tr key={u.id} className="click" onClick={() => setSel(u)}>
                  <td><div className="u-cell"><span className="avatar">{u.name.split(' ').map((w) => w[0]).join('').slice(0, 2)}</span><div><b>{u.name}</b><div className="dim sm">#{u.id} · joined {u.joined}</div></div></div></td>
                  <td className="dim">{u.phone}<div className="sm">{u.email}</div></td>
                  <td><Badge v={u.kyc}>{KYC_LABEL[u.kyc]}</Badge></td>
                  <td><Badge v={u.wa === 'active' ? 'whatsapp' : u.wa}>{u.wa === 'active' ? 'linked' : u.wa}</Badge></td>
                  <td className="r num">{D.fmtN(u.wallets.NGN || 0, 'NGN')}</td>
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
            <div className="kv"><span>Tier</span><b>Tier {sel.tier}</b></div>
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
              <button className="btn ghost" disabled={!can.users} onClick={() => act('unlock_pin', 'PIN unlocked')}>Unlock PIN</button>
              {sel.status === 'frozen'
                ? <button className="btn primary" disabled={!can.users} onClick={() => act('unfreeze', 'unfrozen')}>Unfreeze account</button>
                : <button className="btn danger" disabled={!can.users} onClick={() => act('freeze', 'frozen — sessions revoked')}>Freeze account</button>}
            </div>
            {!can.users && <p className="rbac-note"><Icon name="lock" size={13} /> Your role can't modify users.</p>}
          </div>
        )}
      </Drawer>
    </div>
  );
}

// ================= FX & TREASURY =================
function Fx({ toast, refresh }) {
  const { can } = useRole();
  const [margin, setMargin] = useState(D.FX.margin);
  const applyMargin = async () => {
    try { await ZAPI.fxMargin(margin); toast('fx_margin_bps set to ' + margin + ' (audit logged)'); refresh(); }
    catch (e) { toast('⚠ ' + e.message); }
  };
  const setCorridor = async (pair, v) => {
    try { await ZAPI.fxCorridor(pair.split('/')[1], v); toast(pair + ' settlement ' + (v ? 'enabled' : 'paused') + ' (audit logged)'); refresh(); }
    catch (e) { toast('⚠ ' + e.message); }
  };
  const FLAGS = { 'NGN/USD': '🇺🇸', 'NGN/GBP': '🇬🇧', 'NGN/CAD': '🇨🇦', 'NGN/CNY': '🇨🇳' };
  return (
    <div>
      <PageHead title="FX & Treasury" sub="Rates from Fincra · customer rate = provider rate + margin" />
      <div className="grid-2-1">
        <Card title="Corridors & live rates" sub={'Customer margin: ' + D.FX.margin + ' bps'} pad={false}>
          <table className="tbl">
            <thead><tr><th>Corridor</th><th className="r">Provider rate</th><th className="r">Customer rate</th><th className="r">24h volume</th><th>Settlement</th></tr></thead>
            <tbody>
              {D.FX.rates.map((r) => (
                <tr key={r.pair}>
                  <td><b>{FLAGS[r.pair]} {r.pair}</b>{r.pair === 'NGN/CNY' && <div className="sm dim">Quote/display only — settlement blocked</div>}</td>
                  <td className="r num">{r.provider ? '₦' + r.provider.toLocaleString('en-NG', { minimumFractionDigits: 2 }) : '—'}</td>
                  <td className="r num"><b>{r.customer ? '₦' + r.customer.toLocaleString('en-NG', { minimumFractionDigits: 2 }) : '—'}</b></td>
                  <td className="r num">{r.vol24 ? D.fmtN(r.vol24, 'NGN') : '—'}</td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <Toggle on={r.settle} disabled={!can.money || r.pair === 'NGN/CNY'} label={r.pair}
                        onChange={(v) => setCorridor(r.pair, v)} />
                      {r.pair === 'NGN/CNY' && <Icon name="lock" size={13} style={{ color: '#7B828E' }} />}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
          <Card title="FX margin" sub="fx_margin_bps — applied to every quote">
            <div className="margin-edit">
              <input type="range" min="0" max="300" step="5" value={margin} disabled={!can.money} onChange={(e) => setMargin(+e.target.value)} />
              <div className="margin-val num">{margin} <span>bps</span></div>
            </div>
            <button className="btn primary w100" disabled={!can.money || margin === D.FX.margin} onClick={applyMargin}>Apply margin</button>
            {!can.money && <p className="rbac-note"><Icon name="lock" size={13} /> Finance or super admin only.</p>}
          </Card>
          <Card title="Customer balances" sub="Total user funds by currency (treasury liabilities)">
            {D.FX.float.map((f) => (
              <div key={f.cur} className="kv tight"><span>{f.cur} <em className="dim">· {f.provider}</em></span><b className="num">{D.fmtN(f.bal, f.cur)}</b></div>
            ))}
          </Card>
        </div>
      </div>
      <Card title="Quote safety rules" sub="Enforced by the settlement service — not editable here">
        <div className="rules">
          <div className="rule"><Icon name="check" size={15} /> Quotes are single-use and expiry-checked — a stale rate is never settled.</div>
          <div className="rule"><Icon name="check" size={15} /> Settlement is atomic: debit source, credit target, ledger pair tagged with currency.</div>
          <div className="rule"><Icon name="check" size={15} /> CNY is corridor-blocked from settlement until a partner is live (quotes only).</div>
        </div>
      </Card>
    </div>
  );
}

Object.assign(window, { PageHead, Overview, Transactions, TxnTable, Users, Fx });
