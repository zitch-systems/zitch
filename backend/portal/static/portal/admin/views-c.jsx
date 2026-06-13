// Zitch Admin — views C: KYC queue, Products (Loans / Fixed Save / Cards), Providers & recon (live)
const { useState: useStateC } = React;
const DC = window.ZADM;

// ================= KYC QUEUE =================
function KycQueue({ toast, refresh }) {
  const { can } = useRole();
  const [items, setItems] = useStateC(DC.KYCQ);
  const act = async (it, approve) => {
    try {
      const r = await ZAPI.kycReview(it.id, approve);
      setItems(items.filter((x) => x !== it));
      toast((approve ? 'Approved ' + it.user + ' — now Tier ' + r.tier : 'Rejected ' + it.user) + ' (audit logged)');
    } catch (e) { toast('⚠ ' + e.message); }
  };
  return (
    <div>
      <PageHead title="KYC queue" sub="Manual reviews — approve to bump the user's tier (caps at 3)." />
      <Card pad={false}>
        {items.length ? (
          <table className="tbl">
            <thead><tr><th>User</th><th>Check</th><th>Tier change</th><th>Verification state</th><th className="r">Joined</th><th className="r">Action</th></tr></thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id}>
                  <td><div className="u-cell"><span className="avatar">{it.user.split(' ').map((w) => w[0]).join('').slice(0, 2)}</span><div><b>{it.user}</b><div className="dim sm">#{it.id}</div></div></div></td>
                  <td><Badge v={it.type}>{it.type.toUpperCase()}</Badge></td>
                  <td className="num">Tier {it.tier}</td>
                  <td className="dim">{it.note}</td>
                  <td className="r dim num">{DC.fmtT(it.submitted)}</td>
                  <td className="r">
                    <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                      <button className="btn danger sm-btn" disabled={!can.users} onClick={() => act(it, false)}>Reject</button>
                      <button className="btn primary sm-btn" disabled={!can.users} onClick={() => act(it, true)}>Approve</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty text="Queue is clear — no pending reviews." />}
      </Card>
      {!can.users && <p className="rbac-note"><Icon name="lock" size={13} /> Finance or super admin only.</p>}
    </div>
  );
}

// ================= PRODUCTS: LOANS / SAVINGS / CARDS =================
function Products({ toast, refresh }) {
  const { can } = useRole();
  const [tab, setTab] = useStateC('loans');
  const remind = async (l) => {
    try { await ZAPI.loanRemind(l.id); toast('Repayment reminder sent to ' + l.user + ' via WhatsApp (audit logged)'); }
    catch (e) { toast('⚠ ' + e.message); }
  };
  const freeze = async (c) => {
    try {
      const r = await ZAPI.cardAction(c.id);
      toast('Card ····' + c.last4 + ' now ' + r.status + ' (audit logged)'); refresh();
    } catch (e) { toast('⚠ ' + e.message); }
  };
  const sweep = async () => {
    try { const r = await ZAPI.runMaturities(); toast('Maturity sweep complete — ' + r.paid_out + ' plan(s) paid out (audit logged)'); refresh(); }
    catch (e) { toast('⚠ ' + e.message); }
  };
  const outstanding = DC.LOANS.reduce((s, l) => s + (l.status !== 'repaid' ? l.outstanding : 0), 0);
  const overdue = DC.LOANS.filter((l) => l.status === 'overdue').length;
  return (
    <div>
      <PageHead title="Products" sub="Loans, Fixed Save maturities and virtual cards." />
      <div className="chips">
        {[['loans', 'Loans'], ['savings', 'Fixed Save'], ['cards', 'Cards']].map(([k, l]) => (
          <button key={k} className={'chip' + (tab === k ? ' on' : '')} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>

      {tab === 'loans' && (
        <Card title="Loan book" sub={DC.fmtN(outstanding, 'NGN') + ' outstanding · ' + overdue + ' overdue'} pad={false}>
          {DC.LOANS.length ? (
            <table className="tbl">
              <thead><tr><th>Loan</th><th>Borrower</th><th className="r">Principal</th><th>Tenor</th><th>Due</th><th className="r">Outstanding</th><th>Status</th><th className="r">Action</th></tr></thead>
              <tbody>
                {DC.LOANS.map((l) => (
                  <tr key={l.id}>
                    <td className="mono">{l.id}</td>
                    <td><b>{l.user}</b></td>
                    <td className="r num">{DC.fmtN(l.amt, 'NGN')}</td>
                    <td className="dim">{l.tenor}</td>
                    <td className="dim">{l.due}</td>
                    <td className="r num">{l.outstanding ? DC.fmtN(l.outstanding, 'NGN') : '—'}</td>
                    <td><Badge v={l.status === 'active' ? 'human' : l.status === 'overdue' ? 'failed' : 'success'}>{l.status}</Badge></td>
                    <td className="r">
                      {l.status === 'overdue' && <button className="btn ghost sm-btn" disabled={!can.money} onClick={() => remind(l)}>Send reminder</button>}
                      {l.status !== 'overdue' && <span className="dim sm">repays in-app</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty text="No loans yet." />}
        </Card>
      )}

      {tab === 'savings' && (
        <div className="grid-2-1">
          <Card title="Fixed Save plans" sub="Matured plans settle on access; the sweep pays out the rest" pad={false}>
            {DC.SAVINGS.length ? (
              <table className="tbl">
                <thead><tr><th>Plan</th><th>Saver</th><th className="r">Principal</th><th>Rate</th><th>Maturity</th><th className="r">Payout</th><th>Status</th></tr></thead>
                <tbody>
                  {DC.SAVINGS.map((s) => (
                    <tr key={s.id}>
                      <td className="mono">{s.id}</td>
                      <td><b>{s.user}</b></td>
                      <td className="r num">{DC.fmtN(s.principal, 'NGN')}</td>
                      <td className="dim">{s.rate}</td>
                      <td className="dim">{s.maturity}</td>
                      <td className="r num">{DC.fmtN(s.payout, 'NGN')}</td>
                      <td><Badge v={s.status === 'matured' ? 'pending' : s.status === 'active' ? 'human' : 'success'}>{s.status}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <Empty text="No Fixed Save plans yet." />}
          </Card>
          <Card title="Maturity sweep" sub="run_maturities — idempotent per plan">
            <p className="dim sm" style={{ margin: '0 0 14px', lineHeight: 1.6 }}>
              {DC.MATURED_DUE
                ? DC.MATURED_DUE + ' matured plan(s) awaiting payout. The nightly cron also runs this — overlapping runs never double-pay.'
                : 'No matured plans awaiting payout. The nightly cron keeps this clear; the button is safe to run anytime.'}
            </p>
            <button className="btn primary w100" disabled={!can.money} onClick={sweep}><Icon name="check" size={15} /> Run maturities sweep</button>
            {!can.money && <p className="rbac-note"><Icon name="lock" size={13} /> Finance or super admin only.</p>}
          </Card>
        </div>
      )}

      {tab === 'cards' && (
        <Card title="Virtual cards" sub="Funded from the Zitch wallet" pad={false}>
          {DC.CARDS.length ? (
            <table className="tbl">
              <thead><tr><th>Card</th><th>Holder</th><th className="r">Balance</th><th>Status</th><th className="r">Action</th></tr></thead>
              <tbody>
                {DC.CARDS.map((c) => (
                  <tr key={c.id}>
                    <td className="mono">···· {c.last4} <span className="dim sm">{c.cur}</span></td>
                    <td><b>{c.user}</b></td>
                    <td className="r num">{DC.fmtN(c.bal, c.cur)}</td>
                    <td><Badge v={c.status} /></td>
                    <td className="r">
                      <button className={'btn sm-btn ' + (c.status === 'frozen' ? 'primary' : 'danger')} disabled={!can.users} onClick={() => freeze(c)}>
                        {c.status === 'frozen' ? 'Unfreeze' : 'Freeze'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty text="No virtual cards yet." />}
        </Card>
      )}
    </div>
  );
}

// ================= PROVIDERS & RECONCILIATION =================
function Recon({ toast, refresh }) {
  const { can } = useRole();
  const webhooks = DC.RECON.rows.filter((r) => r.action.startsWith('webhook.'));
  const runs = DC.RECON.rows.filter((r) => r.action.startsWith('recon.'));
  const runNow = async () => {
    try { const r = await ZAPI.runRecon(); toast('Reconciliation done — ' + r.settled + ' transaction(s) settled (audit logged)'); refresh(); }
    catch (e) { toast('⚠ ' + e.message); }
  };
  return (
    <div>
      <PageHead title="Providers & recon" sub="Webhook deliveries, reconciliation runs, and integration health." />
      <div className="grid-2-1">
        <Card title="Webhook log" sub="Inbound callbacks — HMAC verified before processing" pad={false}>
          {webhooks.length ? (
            <table className="tbl">
              <thead><tr><th>Source</th><th>Event</th><th>Reference</th><th>Signature</th><th className="r">When</th></tr></thead>
              <tbody>
                {webhooks.map((w, i) => (
                  <tr key={i}>
                    <td><b>{w.action.replace('webhook.', '')}</b></td>
                    <td><span className="mono sm">{(w.after && w.after.event) || '—'}</span></td>
                    <td className="mono sm dim">{w.target || '—'}</td>
                    <td><Badge v="success">verified</Badge></td>
                    <td className="r dim num">{DC.fmtT(w.t)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty text="No webhook deliveries recorded yet." />}
        </Card>
        <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
          <Card title="Reconciliation runs" sub="Cron + on-demand; all idempotent">
            {runs.length ? runs.map((r, i) => (
              <div key={i} className="kv tight">
                <span><b className="mono sm">{r.action}</b><em className="dim sm"> · {DC.fmtT(r.t)}</em></span>
                <span className="num sm">{JSON.stringify(r.after)}</span>
              </div>
            )) : <p className="dim sm" style={{ margin: 0 }}>No runs recorded yet — the crons log here from now on.</p>}
            <button className="btn ghost w100" style={{ marginTop: 12 }} disabled={!can.money} onClick={runNow}>Run VTU reconciliation now</button>
            {!can.money && <p className="rbac-note"><Icon name="lock" size={13} /> Finance or super admin only.</p>}
          </Card>
          <Card title="Provider health">
            <div className="prov-list">
              {DC.PROVIDERS.map((p) => (
                <div key={p.name} className="prov-row">
                  <div><div className="prov-name">{p.name}</div><div className="prov-role">{p.role}</div></div>
                  <div style={{ textAlign: 'right' }}><Badge v={p.status} /><div className="prov-up num">{p.uptime}</div></div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { KycQueue, Products, Recon });
