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
            <td><Badge v={t.status}>{t.status === 'under_review' ? 'under review' : t.status}</Badge></td>
            <td className="r dim num">{D.fmtT(t.time)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function MoneyReviews({ toast, refresh }) {
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const label = (value) => String(value || '').replace(/_/g, ' ');
  const open = (kind, row) => {
    const choices = row.dispositions || (kind === 'reversal' ? D.REVERSAL_DISPOSITIONS
      : kind === 'card' ? D.CARD_FUNDING_DISPOSITIONS
      : D.FUNDING_REVIEW_DISPOSITIONS);
    const evidence = kind === 'reversal' ? row.provider_reference
      : kind === 'funding' ? (row.evidence_reference || row.provider_reference || '')
      : '';
    const amount = kind === 'reversal'
      ? ((row.observed_amounts || [])[0] || row.original_amount || '')
      : row.amount || '';
    const payoutReferences = kind === 'reversal'
      ? (row.payout_references || (row.payout_reference ? [row.payout_reference] : []))
      : [];
    setForm({
      kind, row, disposition: '', reason: '', evidence, amount,
      // Multiple durable payout associations are deliberately not defaulted:
      // the maker must choose which held payout the evidence resolves.
      payoutReference: payoutReferences.length === 1 ? payoutReferences[0] : '',
      payoutReferences,
      choices: choices || [],
    });
  };
  const update = (key, value) => setForm((old) => ({ ...old, [key]: value }));
  const submit = async () => {
    if (!form.disposition) return toast('⚠ Choose an accounting treatment');
    if (form.kind === 'reversal' && form.payoutReferences.length > 1 && !form.payoutReference) return toast('⚠ Choose the payout this evidence resolves');
    if (form.reason.trim().length < 12) return toast('⚠ Enter a clear reason of at least 12 characters');
    if (form.evidence.trim().length < 4) return toast('⚠ Enter the provider evidence reference you checked');
    if (!form.amount || Number(form.amount) <= 0) return toast('⚠ Enter the amount confirmed by the evidence');
    const payload = {
      reference: form.kind === 'reversal' ? form.payoutReference : form.row.reference,
      evidence_reference: form.evidence.trim(),
      disposition: form.disposition,
      reason: form.reason.trim(),
      confirmed_amount: String(form.amount).trim(),
    };
    setBusy(true);
    try {
      const result = form.kind === 'reversal'
        ? await ZAPI.reversalResolution(payload)
        : form.kind === 'card'
          ? await ZAPI.cardFundingResolution(payload)
          : await ZAPI.fundingResolution(payload);
      toast('Resolution request #' + result.approval_id + ' created — a different finance operator must approve it');
      setForm(null);
      await refresh();
    } catch (e) { toast('⚠ ' + e.message); }
    setBusy(false);
  };
  const empty = !D.REVERSAL_CASES.length && !D.CARD_FUNDING_CASES.length && !D.FUNDING_REVIEW_CASES.length;
  return (
    <div style={{ marginTop: 16 }}>
      <Card title="Money reviews" sub="Provider evidence is held here until one operator proposes a treatment and another approves it" pad={false}>
        {empty && <Empty text="No reversal, card-funding, or wallet-funding cases need review." />}
        {!!D.REVERSAL_CASES.length && (
          <React.Fragment>
            <div className="card-sub" style={{ padding: '14px 16px 4px' }}>Bank returns / payout reversals</div>
            <table className="tbl">
              <thead><tr><th>Evidence</th><th>Payout</th><th>Customer</th><th>Observed amount(s)</th><th>State</th><th></th></tr></thead>
              <tbody>{D.REVERSAL_CASES.map((r) => (
                <tr key={'rev-' + r.id}>
                  <td className="mono">{r.provider_reference}</td>
                  <td className="mono">{(r.payout_references || (r.payout_reference ? [r.payout_reference] : [])).join(', ') || 'unmatched'}</td>
                  <td>{r.customer}</td>
                  <td className="num">{(r.observed_amounts || [r.original_amount]).join(', ')}</td>
                  <td><Badge v={r.state === 'conflict' ? 'flagged' : 'pending'}>{r.state}</Badge></td>
                  <td className="r"><button className="btn primary sm-btn" onClick={() => open('reversal', r)}>Propose resolution</button></td>
                </tr>
              ))}</tbody>
            </table>
          </React.Fragment>
        )}
        {!!D.CARD_FUNDING_CASES.length && (
          <React.Fragment>
            <div className="card-sub" style={{ padding: '14px 16px 4px' }}>Unresolved card funding</div>
            <table className="tbl">
              <thead><tr><th>Reference</th><th>Card</th><th>Customer</th><th>Amount</th><th>Review</th><th></th></tr></thead>
              <tbody>{D.CARD_FUNDING_CASES.map((r) => (
                <tr key={'card-' + r.reference}>
                  <td className="mono">{r.reference}</td>
                  <td>{r.card_last4 ? '•••• ' + r.card_last4 : '—'}</td>
                  <td>{r.customer}</td>
                  <td className="num">{D.fmtN(Number(r.amount), r.currency)}</td>
                  <td><Badge v="pending">{label(r.review_type)}</Badge></td>
                  <td className="r"><button className="btn primary sm-btn" onClick={() => open('card', r)}>Propose resolution</button></td>
                </tr>
              ))}</tbody>
            </table>
          </React.Fragment>
        )}
        {!!D.FUNDING_REVIEW_CASES.length && (
          <React.Fragment>
            <div className="card-sub" style={{ padding: '14px 16px 4px' }}>Wallet funding holds</div>
            <table className="tbl">
              <thead><tr><th>Reference</th><th>Customer</th><th>Requested</th><th>Observed</th><th>Reason</th><th></th></tr></thead>
              <tbody>{D.FUNDING_REVIEW_CASES.map((r) => (
                <tr key={'fund-' + r.reference}>
                  <td className="mono">{r.reference}</td>
                  <td>{r.customer}</td>
                  <td className="num">{D.fmtN(Number(r.amount), 'NGN')}</td>
                  <td className="num">{r.observed_amount ? r.observed_currency + ' ' + r.observed_amount : '—'}</td>
                  <td>{label(r.review_reason)}</td>
                  <td className="r"><button className="btn primary sm-btn" onClick={() => open('funding', r)}>Propose resolution</button></td>
                </tr>
              ))}</tbody>
            </table>
          </React.Fragment>
        )}
      </Card>
      <Drawer open={!!form} onClose={() => !busy && setForm(null)} title="Propose money-review resolution">
        {form && (
          <div>
            <div className="note warn"><Icon name="alert" size={15} /> This does not move money now. A different finance operator must approve the exact evidence and treatment.</div>
            {form.kind === 'reversal' && form.payoutReferences.length > 1 ? (
              <React.Fragment>
                <label className="f-label">Payout to resolve</label>
                <select className="f-input" value={form.payoutReference} onChange={(e) => update('payoutReference', e.target.value)}>
                  <option value="">Choose payout…</option>
                  {form.payoutReferences.map((reference) => <option key={reference} value={reference}>{reference}</option>)}
                </select>
              </React.Fragment>
            ) : (
              <div className="kv"><span>Reference</span><b className="mono">{form.kind === 'reversal' ? (form.payoutReference || 'unmatched') : form.row.reference}</b></div>
            )}
            <label className="f-label">Provider evidence reference</label>
            <input className="f-input" value={form.evidence} onChange={(e) => update('evidence', e.target.value)} placeholder="Statement, trace or settlement reference" />
            <label className="f-label">Confirmed amount</label>
            {form.kind === 'reversal' && (form.row.observed_amounts || []).length > 1
              ? <select className="f-input" value={form.amount} onChange={(e) => update('amount', e.target.value)}>
                  {(form.row.observed_amounts || []).map((amount) => <option key={amount} value={amount}>{amount}</option>)}
                </select>
              : <input className="f-input" type="number" min="0.01" step="0.01" value={form.amount} onChange={(e) => update('amount', e.target.value)} />}
            <label className="f-label">Accounting treatment</label>
            <select className="f-input" value={form.disposition} onChange={(e) => update('disposition', e.target.value)}>
              <option value="">Choose treatment…</option>
              {form.choices.map((choice) => <option key={choice} value={choice}>{label(choice)}</option>)}
            </select>
            <label className="f-label">Reason</label>
            <textarea className="f-input" rows="4" value={form.reason} onChange={(e) => update('reason', e.target.value)} placeholder="What evidence was checked and why this treatment is correct" />
            <div className="drawer-actions">
              <button className="btn ghost" disabled={busy} onClick={() => setForm(null)}>Cancel</button>
              <button className="btn primary" disabled={busy} onClick={submit}>{busy ? 'Submitting…' : 'Send for second approval'}</button>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
}

function Transactions({ toast, refresh }) {
  const { can } = useRole();
  const [q, setQ] = useState('');
  const [type, setType] = useState('all');
  const [sel, setSel] = useState(null);
  const [rows, setRows] = useState(D.TXNS);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const moneyApprovals = (D.APPROVALS || [])
    .filter((r) => r.action !== 'whatsapp.broadcast');
  const TYPES = ['all', 'transfer', 'fx', 'fund', 'airtime', 'data', 'electricity', 'cable'];
  const refetch = (nq, ntype) => ZAPI.load.txns(nq, ntype, false).then(() => setRows(D.TXNS)).catch((e) => toast('⚠ ' + e.message));
  const requery = async () => {
    try {
      const r = await ZAPI.txnRequery(sel.id);
      toast(sel.id + ' requeried — now ' + r.status + ' (audit logged)');
      setSel(null); refetch(q, type);
    } catch (e) { toast('⚠ ' + e.message); }
  };
  const decideApproval = async (row, approve) => {
    setApprovalBusy(true);
    try {
      const result = await ZAPI.approvalDecide(
        row.id, approve,
        approve ? 'Finance evidence checked' : 'Finance request rejected'
      );
      if (!approve) {
        toast('Request #' + row.id + ' rejected');
      } else if (result.status === 'executed') {
        toast('Request #' + row.id + ' approved and executed');
      } else {
        const error = result.result && result.result.error ? ': ' + result.result.error : '';
        toast('⚠ Request #' + row.id + ' was not executed (' + result.status + ')' + error);
      }
      await refresh();
    } catch (e) { toast('⚠ ' + e.message); }
    setApprovalBusy(false);
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
      {can.money && <MoneyReviews toast={toast} refresh={refresh} />}
      {can.money && <div style={{ marginTop: 16 }}>
        <Card title="Pending finance approvals" sub="A different finance operator must decide each held money action" pad={false}>
          {moneyApprovals.length ? (
            <table className="tbl">
              <thead><tr><th>Request</th><th>Action</th><th>Reference / treatment</th><th>Requested by</th><th></th></tr></thead>
              <tbody>{moneyApprovals.map((r) => (
                <tr key={r.id}>
                  <td className="mono">#{r.id}</td>
                  <td className="mono">{r.action}</td>
                  <td>
                    <b className="mono">{(r.payload || {}).reference || (r.payload || {}).evidence_reference || ((r.payload || {}).uid ? 'user #' + (r.payload || {}).uid : '—')}</b>
                    {(r.payload || {}).evidence_reference && (r.payload || {}).reference
                      ? <div className="sm dim">Evidence: {(r.payload || {}).evidence_reference}</div> : null}
                    <div className="sm dim">{(r.payload || {}).disposition || 'manual credit'}</div>
                    {((r.payload || {}).confirmed_amount || (r.payload || {}).amount)
                      ? <div className="sm num">Amount: ₦{(r.payload || {}).confirmed_amount || (r.payload || {}).amount}</div> : null}
                    <div className="sm dim">{r.reason || (r.payload || {}).reason || ''}</div>
                  </td>
                  <td>{r.requested_by}{r.is_own_request ? ' (you)' : ''}</td>
                  <td className="r">
                    <button className="btn ghost sm-btn" disabled={approvalBusy || !r.can_decide}
                      onClick={() => decideApproval(r, false)}>Reject</button>{' '}
                    <button className="btn primary sm-btn" disabled={approvalBusy || !r.can_decide}
                      onClick={() => decideApproval(r, true)}>Approve</button>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          ) : <Empty text="No finance requests are waiting for your role." />}
        </Card>
      </div>}
      <Drawer open={!!sel} onClose={() => setSel(null)} title={sel ? sel.id : ''}>
        {sel && (
          <div>
            <div className="kv"><span>User</span><b>{sel.user}</b></div>
            <div className="kv"><span>Detail</span><b>{sel.desc}</b></div>
            <div className="kv"><span>Amount</span><b className="num">{D.fmtN(sel.amt, sel.cur)}</b></div>
            <div className="kv"><span>Channel</span><Badge v={sel.channel} /></div>
            <div className="kv"><span>Status</span><Badge v={sel.status}>{sel.status === 'under_review' ? 'under review' : sel.status}</Badge></div>
            {sel.underReview
              ? <div className="note warn"><Icon name="alert" size={15} /> Conflicting provider evidence is under finance review. Do not mark this settled or ask the customer to retry.</div>
              : sel.canRequery
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
              {/* The customer normally grants this themselves by replying "ai on".
                  Support needs it too, for anyone who asks on a call — and the
                  panel showed the state with no way to act on it, which reads as
                  a broken integration rather than a consent that is simply off. */}
              {sel.wa === 'active' && (sel.aiEnabled
                ? <button className="btn ghost" disabled={!can.users}
                          onClick={() => act('ai_off', 'smart replies off')}>Turn AI off</button>
                : <button className="btn ghost" disabled={!can.users}
                          onClick={() => act('ai_on', 'smart replies on')}>Turn AI on</button>)}
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
