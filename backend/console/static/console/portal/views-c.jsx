// Zitch Admin — views C: KYC queue, Products (Loans / Fixed Save / Cards), Providers & reconciliation
// Reviews, card freezes, reminders and recon runs all POST to their audited
// /api/admin/* endpoints (server-side RBAC) before updating local state.
const { useState: useStateC } = React;
const DC = window.ZADM;

// ================= KYC QUEUE =================
function KycQueue({ toast }) {
  const { can } = useRole();
  const [items, setItems] = useStateC(DC.KYCQ);
  const [busy, setBusy] = useStateC(false);
  const act = async (it, ok) => {
    setBusy(true);
    const r = await doAct(toast, '/kyc/review', { uid: it.uid, decision: ok ? 'approve' : 'reject', type: it.type });
    setBusy(false);
    if (r) {
      const next = items.filter((x) => x !== it);
      setItems(next); DC.KYCQ = next;
      toast((ok ? 'Approved ' : 'Rejected ') + it.user + ' — ' + it.type.toUpperCase() +
        (ok ? ' · now tier ' + r.tier : '') + ' (audit logged)');
    }
  };
  return (
    <div>
      <PageHead title="KYC queue" sub="Manual reviews escalated by Prembly — approve to bump the user's tier." />
      <Card pad={false}>
        {items.length ? (
          <table className="tbl">
            <thead><tr><th>User</th><th>Check</th><th>Tier change</th><th>Reviewer note</th><th className="r">Waiting</th><th className="r">Action</th></tr></thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id}>
                  <td><div className="u-cell"><span className="avatar">{it.user.split(' ').map((w) => w[0]).join('')}</span><div><b>{it.user}</b><div className="dim sm">{it.id}</div></div></div></td>
                  <td><Badge v={it.type}>{it.type.toUpperCase()}</Badge></td>
                  <td className="num">Tier {it.tier}</td>
                  <td className="dim">{it.note}</td>
                  <td className="r dim num">{it.submitted ? DC.fmtT(it.submitted) : '—'}</td>
                  <td className="r">
                    <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                      <button className="btn danger sm-btn" disabled={!can.users || busy} onClick={() => act(it, false)}>Reject</button>
                      <button className="btn primary sm-btn" disabled={!can.users || busy} onClick={() => act(it, true)}>Approve</button>
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
function Products({ toast }) {
  const { can } = useRole();
  const [tab, setTab] = useStateC('loans');
  const [cards, setCards] = useStateC(DC.CARDS);
  const [busy, setBusy] = useStateC(false);
  const k = DC.KPIS || {};
  const loans = DC.LOANS;
  const totalOutstanding = loans.reduce((s, l) => s + (l.outstanding || 0), 0);
  const overdue = loans.filter((l) => l.status === 'overdue').length;
  const maturedDue = k.matured_due || 0;

  const remind = async (l) => {
    setBusy(true);
    const r = await doAct(toast, '/loans/remind', { ref: l.ref || l.id });
    setBusy(false);
    if (r) toast('Repayment reminder sent to ' + l.user + ' via WhatsApp (audit logged)');
  };
  const sweep = async () => {
    setBusy(true);
    const r = await doAct(toast, '/ops/maturities', {});
    setBusy(false);
    if (r) toast('Maturity sweep complete — ' + r.paid_out + ' plan(s) paid out (audit logged)');
  };
  const freezeCard = async (c) => {
    const status = c.status === 'frozen' ? 'active' : 'frozen';
    setBusy(true);
    const r = await doAct(toast, '/cards/freeze', { card_id: c.cid != null ? c.cid : c.id, status });
    setBusy(false);
    if (r) {
      const next = cards.map((x) => (x.id === c.id ? { ...x, status } : x));
      setCards(next); DC.CARDS = next;
      toast('Card ····' + c.last4 + (status === 'frozen' ? ' frozen' : ' unfrozen') + ' (audit logged)');
    }
  };

  return (
    <div>
      <PageHead title="Products" sub="Loans, Fixed Save maturities and virtual cards." />
      <div className="chips">
        {[['loans', 'Loans'], ['savings', 'Fixed Save'], ['cards', 'Cards']].map(([key, l]) => (
          <button key={key} className={'chip' + (tab === key ? ' on' : '')} onClick={() => setTab(key)}>{l}</button>
        ))}
      </div>

      {tab === 'loans' && (
        <Card title="Loan book" sub={DC.fmtN(totalOutstanding, 'NGN') + ' outstanding · ' + overdue + ' overdue'} pad={false}>
          {loans.length ? (
            <table className="tbl">
              <thead><tr><th>Loan</th><th>Borrower</th><th className="r">Principal</th><th>Tenor · rate</th><th className="r">Outstanding</th><th>Status</th><th className="r">Action</th></tr></thead>
              <tbody>
                {loans.map((l) => (
                  <tr key={l.id}>
                    <td className="mono">{l.ref || l.id}</td>
                    <td><b>{l.user}</b></td>
                    <td className="r num">{DC.fmtN(l.amt, 'NGN')}</td>
                    <td className="dim">{l.tenor} · {l.rate}</td>
                    <td className="r num">{l.outstanding ? DC.fmtN(l.outstanding, 'NGN') : '—'}</td>
                    <td><Badge v={l.status === 'active' ? 'human' : l.status === 'overdue' ? 'failed' : 'success'}>{l.status}</Badge></td>
                    <td className="r">
                      {(l.status === 'overdue' || l.status === 'active') &&
                        <button className="btn ghost sm-btn" disabled={!can.money || busy} onClick={() => remind(l)}>Send reminder</button>}
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
              {maturedDue
                ? maturedDue + ' matured plan(s) are awaiting payout. The nightly cron also runs this — overlapping runs never double-pay.'
                : 'No matured plans awaiting payout right now. The nightly cron also runs this — overlapping runs never double-pay.'}
            </p>
            <button className="btn primary w100" disabled={!can.money || busy} onClick={sweep}><Icon name="check" size={15} /> {busy ? 'Running…' : 'Run maturities sweep'}</button>
            {!can.money && <p className="rbac-note"><Icon name="lock" size={13} /> Finance or super admin only.</p>}
          </Card>
        </div>
      )}

      {tab === 'cards' && (
        <Card title="Virtual cards" sub="Cards funded from the NGN wallet" pad={false}>
          {cards.length ? (
            <table className="tbl">
              <thead><tr><th>Card</th><th>Holder</th><th className="r">Balance</th><th className="r">30-day spend</th><th>Status</th><th className="r">Action</th></tr></thead>
              <tbody>
                {cards.map((c) => (
                  <tr key={c.id}>
                    <td className="mono">···· {c.last4} <span className="dim sm">{c.cur}</span></td>
                    <td><b>{c.user}</b></td>
                    <td className="r num">{DC.fmtN(c.bal, c.cur)}</td>
                    <td className="r num">{DC.fmtN(c.spend30 || 0, c.cur)}</td>
                    <td><Badge v={c.status} /></td>
                    <td className="r">
                      <button className={'btn sm-btn ' + (c.status === 'frozen' ? 'primary' : 'danger')} disabled={!can.money || busy}
                        onClick={() => freezeCard(c)}>
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
function Recon({ toast }) {
  const { can } = useRole();
  const [busy, setBusy] = useStateC(false);
  const run = async () => {
    setBusy(true);
    const r = await doAct(toast, '/ops/recon', {});
    setBusy(false);
    if (r) toast('VTU reconciliation: ' + r.checked + ' pending checked, ' + r.settled + ' settled (audit logged)');
  };
  return (
    <div>
      <PageHead title="Providers & recon" sub="Webhook deliveries, scheduled reconciliation runs, and integration health." />
      <div className="grid-2-1">
        <Card title="Webhook log" sub="All inbound callbacks — HMAC verified before processing" pad={false}>
          {DC.WEBHOOKS.length ? (
            <table className="tbl">
              <thead><tr><th>Source</th><th>Event</th><th>Reference</th><th>Signature</th><th className="r">When</th></tr></thead>
              <tbody>
                {DC.WEBHOOKS.map((w, i) => (
                  <tr key={i}>
                    <td><b>{w.src}</b></td>
                    <td><span className="mono sm">{w.event}</span>{w.note && <div className="sm dim">{w.note}</div>}</td>
                    <td className="mono sm dim">{w.ref}</td>
                    <td><Badge v={w.sig === 'verified' ? 'success' : 'draft'}>{w.sig}</Badge></td>
                    <td className="r dim num">{DC.fmtT(w.time)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty text="Webhook deliveries appear in the audit log (webhook.* entries)." />}
        </Card>
        <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
          <Card title="Reconciliation runs" sub="Nightly crons — or run on demand">
            {DC.RECONS.length ? DC.RECONS.map((r, i) => (
              <div key={i} className="kv tight">
                <span><b className="mono sm">{r.run}</b><em className="dim sm"> · {r.time}</em>{r.note && <div className="sm dim">{r.note}</div>}</span>
                <span className="num sm">{r.checked} checked · <b style={{ color: r.mismatches ? '#9A5B00' : '#0B7A43' }}>{r.mismatches} fixed</b></span>
              </div>
            )) : <p className="dim sm" style={{ margin: 0 }}>Past runs are recorded in the audit log as <span className="mono sm">recon.*</span> entries.</p>}
            <button className="btn ghost w100" style={{ marginTop: 12 }} disabled={!can.money || busy} onClick={run}>{busy ? 'Running…' : 'Run VTU reconciliation now'}</button>
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
