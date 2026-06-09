// Zitch Admin — views C: KYC queue, Products (Loans / Fixed Save / Cards), Providers & reconciliation
const { useState: useStateC } = React;
const DC = window.ZADM;

// ================= KYC QUEUE =================
function KycQueue({ toast }) {
  const { can } = useRole();
  const [items, setItems] = useStateC(DC.KYCQ);
  const act = (it, ok) => {
    setItems(items.filter((x) => x !== it));
    toast((ok ? 'Approved ' : 'Rejected ') + it.user + ' — ' + it.type.toUpperCase() + ' (audit logged)');
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
                <tr key={it.user}>
                  <td><div className="u-cell"><span className="avatar">{it.user.split(' ').map((w) => w[0]).join('')}</span><div><b>{it.user}</b><div className="dim sm">{it.id}</div></div></div></td>
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
function Products({ toast }) {
  const { can } = useRole();
  const [tab, setTab] = useStateC('loans');
  const [loans, setLoans] = useStateC(DC.LOANS);
  const [cards, setCards] = useStateC(DC.CARDS);
  const patchLoan = (id, status, msg) => { setLoans(loans.map((l) => (l.id === id ? { ...l, status } : l))); toast(msg + ' (audit logged)'); };
  return (
    <div>
      <PageHead title="Products" sub="Loans, Fixed Save maturities and virtual cards." />
      <div className="chips">
        {[['loans', 'Loans'], ['savings', 'Fixed Save'], ['cards', 'Cards']].map(([k, l]) => (
          <button key={k} className={'chip' + (tab === k ? ' on' : '')} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>

      {tab === 'loans' && (
        <Card title="Loan book" sub="₦138,900 outstanding · 1 overdue" pad={false}>
          <table className="tbl">
            <thead><tr><th>Loan</th><th>Borrower</th><th className="r">Principal</th><th>Tenor · rate</th><th className="r">Score</th><th className="r">Outstanding</th><th>Status</th><th className="r">Action</th></tr></thead>
            <tbody>
              {loans.map((l) => (
                <tr key={l.id}>
                  <td className="mono">{l.id}</td>
                  <td><b>{l.user}</b></td>
                  <td className="r num">{DC.fmtN(l.amt, 'NGN')}</td>
                  <td className="dim">{l.tenor} · {l.rate}</td>
                  <td className="r num">{l.score}</td>
                  <td className="r num">{l.outstanding ? DC.fmtN(l.outstanding, 'NGN') : '—'}</td>
                  <td><Badge v={l.status === 'requested' ? 'pending' : l.status === 'active' ? 'human' : l.status === 'overdue' ? 'failed' : 'success'}>{l.status}</Badge></td>
                  <td className="r">
                    {l.status === 'requested' && <button className="btn primary sm-btn" disabled={!can.money} onClick={() => patchLoan(l.id, 'active', 'Loan ' + l.id + ' approved & disbursed')}>Approve &amp; disburse</button>}
                    {l.status === 'overdue' && <button className="btn ghost sm-btn" disabled={!can.money} onClick={() => toast('Repayment reminder sent to ' + l.user + ' via WhatsApp')}>Send reminder</button>}
                    {(l.status === 'active') && <button className="btn ghost sm-btn" disabled={!can.money} onClick={() => patchLoan(l.id, 'repaid', 'Loan ' + l.id + ' marked repaid')}>Mark repaid</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {tab === 'savings' && (
        <div className="grid-2-1">
          <Card title="Fixed Save plans" sub="Matured plans settle on access; the sweep pays out the rest" pad={false}>
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
          </Card>
          <Card title="Maturity sweep" sub="manage.py run_maturities — idempotent per plan">
            <p className="dim sm" style={{ margin: '0 0 14px', lineHeight: 1.6 }}>1 matured plan is awaiting payout (₦216,000 to Adaeze Okonkwo). The nightly <span className="mono sm">zitch-maturities</span> cron also runs this — overlapping runs never double-pay.</p>
            <button className="btn primary w100" disabled={!can.money} onClick={() => toast('Maturity sweep complete — 1 plan paid out ₦216,000 (audit logged)')}><Icon name="check" size={15} /> Run maturities sweep</button>
            {!can.money && <p className="rbac-note"><Icon name="lock" size={13} /> Finance or super admin only.</p>}
          </Card>
        </div>
      )}

      {tab === 'cards' && (
        <Card title="Virtual cards" sub="USD cards funded from currency wallets" pad={false}>
          <table className="tbl">
            <thead><tr><th>Card</th><th>Holder</th><th className="r">Balance</th><th className="r">30-day spend</th><th>Status</th><th className="r">Action</th></tr></thead>
            <tbody>
              {cards.map((c) => (
                <tr key={c.id}>
                  <td className="mono">···· {c.last4} <span className="dim sm">{c.cur}</span></td>
                  <td><b>{c.user}</b></td>
                  <td className="r num">${c.bal.toFixed(2)}</td>
                  <td className="r num">${c.spend30.toFixed(2)}</td>
                  <td><Badge v={c.status} /></td>
                  <td className="r">
                    <button className={'btn sm-btn ' + (c.status === 'frozen' ? 'primary' : 'danger')} disabled={!can.users}
                      onClick={() => { setCards(cards.map((x) => (x.id === c.id ? { ...x, status: x.status === 'frozen' ? 'active' : 'frozen' } : x))); toast('Card ····' + c.last4 + (c.status === 'frozen' ? ' unfrozen' : ' frozen') + ' (audit logged)'); }}>
                      {c.status === 'frozen' ? 'Unfreeze' : 'Freeze'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

// ================= PROVIDERS & RECONCILIATION =================
function Recon({ toast }) {
  const { can } = useRole();
  return (
    <div>
      <PageHead title="Providers & recon" sub="Webhook deliveries, scheduled reconciliation runs, and integration health." />
      <div className="grid-2-1">
        <Card title="Webhook log" sub="All inbound callbacks — HMAC verified before processing" pad={false}>
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
        </Card>
        <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
          <Card title="Reconciliation runs" sub="Nightly crons (paid Render plan)">
            {DC.RECONS.map((r, i) => (
              <div key={i} className="kv tight">
                <span><b className="mono sm">{r.run}</b><em className="dim sm"> · {r.time}</em>{r.note && <div className="sm dim">{r.note}</div>}</span>
                <span className="num sm">{r.checked} checked · <b style={{ color: r.mismatches ? '#9A5B00' : '#0B7A43' }}>{r.mismatches} fixed</b></span>
              </div>
            ))}
            <button className="btn ghost w100" style={{ marginTop: 12 }} disabled={!can.money} onClick={() => toast('VTU reconciliation queued — results land in the audit log')}>Run VTU reconciliation now</button>
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
