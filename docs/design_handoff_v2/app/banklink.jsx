// banklink.jsx — Mono-linked bank accounts: connected-accounts strip, link-bank
// onboarding, simulated Mono Connect widget, move-money sheet (fund in / out),
// account sheet, and the Home "connected banks" total.
(function () {
  const { useState, useEffect } = React;
  const { useApp, PrimaryButton, Sheet, Monogram, Tap, PinSheet, BiometricScan } = window;
  const { fmtK } = window.ZUI;
  const I = (props) => React.createElement(window.ZIcon, props);
  const Row2 = window.Row2;

  // ₦ with 2 decimals + tabular figures; full account number grouped 4-3-3.
  const money2 = (v) => '₦' + Number(v).toLocaleString('en-NG', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // ---------- Link WhatsApp (Bank on WhatsApp) ----------
  const WA_GLYPH = 'M.057 24l1.687-6.163a11.867 11.867 0 0 1-1.587-5.946C.16 5.335 5.495 0 12.05 0a11.817 11.817 0 0 1 8.413 3.488 11.824 11.824 0 0 1 3.48 8.414c-.003 6.557-5.338 11.892-11.893 11.892a11.9 11.9 0 0 1-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884-.001 2.225.651 3.891 1.746 5.634l-.999 3.648 3.742-.981zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.768.967-.941 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z';
  function LinkWhatsApp() {
    const app = useApp();
    const [view, setView] = useState('unlinked');
    const [busy, setBusy] = useState(false);
    const [code] = useState(() => String(Math.floor(100000 + Math.random() * 900000)));
    const phone = '+234 816 ••• 8327';
    const gen = () => { if (busy) return; setBusy(true); setTimeout(() => { setBusy(false); setView('code'); }, 1400); };
    const check = () => { if (busy) return; setBusy(true); setTimeout(() => { setBusy(false); setView('linked'); app.toast && app.toast('WhatsApp connected'); }, 1500); };
    const copyCode = () => { try { navigator.clipboard.writeText('LINK ' + code); } catch (e) {} app.toast && app.toast("Copied 'LINK " + code + "'"); };
    const openWA = () => app.toast && app.toast('Opening WhatsApp…');
    const waCircle = (sz) => <div style={{ width: sz, height: sz, borderRadius: '50%', background: '#25D366', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 14px 30px -10px rgba(37,211,102,.7)', flexShrink: 0 }}><svg width={sz * 0.5} height={sz * 0.5} viewBox="0 0 24 24" fill="#fff"><path d={WA_GLYPH} /></svg></div>;
    const Step = ({ n, t }) => <div style={{ display: 'flex', alignItems: 'center', gap: 13, padding: '11px 0', borderBottom: '1px solid var(--line)' }}><div style={{ width: 28, height: 28, borderRadius: '50%', background: 'rgba(37,211,102,.14)', color: '#128C7E', fontWeight: 800, fontSize: 13, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>{n}</div><div style={{ fontSize: 14, color: 'var(--ink-1)', fontWeight: 500 }}>{t}</div></div>;
    return <div className="z-screen" style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', background: 'var(--bg-grad)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 16px 4px' }}>
        <Tap onClick={() => app.nav.pop()}><div style={{ width: 40, height: 40, borderRadius: 13, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--surface)', boxShadow: 'var(--shadow-card)' }}><I name="left" size={20} color="var(--ink-1)" /></div></Tap>
        <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--ink-1)' }}>Bank on WhatsApp</div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 20px 120px' }}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center' }}>
          {waCircle(76)}
          <div style={{ fontSize: 21, fontWeight: 800, color: 'var(--ink-1)', marginTop: 16 }}>{view === 'linked' ? 'WhatsApp connected' : view === 'code' ? 'Send your link code' : 'Bank on WhatsApp'}</div>
          <div style={{ fontSize: 13.5, color: 'var(--ink-3)', marginTop: 6, lineHeight: 1.5, maxWidth: 280 }}>
            {view === 'linked' ? 'Your account is linked to ' + phone + '. Check balance, transfer & pay bills from your chats.'
              : view === 'code' ? "We've created your code. Send it to our WhatsApp line to finish linking."
              : 'Check your balance, send money and pay bills right inside your WhatsApp chats.'}
          </div>
        </div>
        {view === 'unlinked' && <div style={{ marginTop: 22 }}>
          <Step n={1} t="Generate your secure link code" />
          <Step n={2} t="Send it to Zitch on WhatsApp" />
          <Step n={3} t="Start banking right in the chat" />
        </div>}
        {view === 'code' && <div style={{ marginTop: 22 }}>
          <div style={{ borderRadius: 18, background: 'var(--surface)', boxShadow: 'var(--shadow-card)', border: '1px solid var(--line)', padding: '18px 16px', textAlign: 'center' }}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.12em', color: 'var(--ink-3)' }}>YOUR LINK CODE</div>
            <div className="z-num" style={{ fontSize: 34, fontWeight: 800, letterSpacing: '6px', color: 'var(--ink-1)', marginTop: 8 }}>{code}</div>
            <Tap onClick={copyCode}><div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 12, padding: '8px 14px', borderRadius: 999, background: 'rgba(37,211,102,.12)', color: '#128C7E', fontWeight: 700, fontSize: 12.5 }}><I name="copy" size={14} color="#128C7E" />Copy 'LINK {code}'</div></Tap>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, marginTop: 16, color: 'var(--ink-3)', fontSize: 12.5 }}><I name="refresh" size={14} color="var(--ink-3)" style={{ animation: 'zspin .9s linear infinite' }} />Waiting for the code…</div>
        </div>}
      </div>
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, padding: '12px 16px 16px', background: 'var(--surface)', borderTop: '1px solid var(--line)' }}>
        {view === 'unlinked' && <PrimaryButton label={busy ? 'Generating…' : 'Generate link code'} onClick={gen} />}
        {view === 'code' && <div>
          <PrimaryButton label="Open WhatsApp" onClick={openWA} />
          <Tap onClick={check}><div style={{ textAlign: 'center', padding: '12px 0 2px', color: 'var(--brand)', fontWeight: 700, fontSize: 13.5 }}>{busy ? 'Checking…' : "I've sent it — check now"}</div></Tap>
        </div>}
        {view === 'linked' && <div>
          <PrimaryButton label="Open WhatsApp" onClick={openWA} />
          <Tap onClick={() => setView('unlinked')}><div style={{ textAlign: 'center', padding: '12px 0 2px', color: 'var(--z-red)', fontWeight: 700, fontSize: 13.5 }}>Unlink WhatsApp</div></Tap>
        </div>}
      </div>
    </div>;
  }
  window.LinkWhatsApp = LinkWhatsApp;
  const fmtAcct = (s) => (/^\d{10}$/.test(String(s || '')) ? String(s).replace(/(\d{4})(\d{3})(\d{3})/, '$1 $2 $3') : String(s || ''));

  const chipPrimary = { height: 34, borderRadius: 10, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 3, padding: '0 3px', minWidth: 0, background: 'rgba(15,162,149,.12)', color: 'var(--brand-deep)', fontWeight: 700, fontSize: 11.5 };
  const chipGhost2 = { height: 34, borderRadius: 10, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 3, padding: '0 3px', minWidth: 0, background: 'var(--surface)', border: '1.5px solid var(--z-teal-200)', color: 'var(--brand-deep)', fontWeight: 700, fontSize: 11.5 };
  const chipLabel = { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' };

  function SLabel({ children, action, onAction }) {
    return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 6 }}>
      <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--ink-1)', minWidth: 0 }}>{children}</div>
      {action && <Tap onClick={onAction}><div style={{ fontSize: 13, fontWeight: 700, color: 'var(--brand)', whiteSpace: 'nowrap', flexShrink: 0 }}>{action}</div></Tap>}
    </div>;
  }

  // ---------- Linked bank card (compact, full account, dual-direction fund) ----------
  function LinkedBankCard({ a, onIn, onOut, onRefresh, onOpen, refreshing }) {
    const reauth = a.status === 'reauth' || a.balance == null;
    const tag = a.tag || a.bank;
    return (
      <div style={{ width: 280, flexShrink: 0, borderRadius: 18, background: 'radial-gradient(140px 110px at 114% -14%, ' + a.color + '2b, transparent 60%), radial-gradient(150px 120px at -14% 120%, ' + a.color + '14, transparent 58%), var(--surface)', boxShadow: 'var(--shadow-card)', border: '1px solid var(--line)', padding: '11px 15px', display: 'flex', flexDirection: 'column', justifyContent: 'space-between', gap: 9, minHeight: 108, scrollSnapAlign: 'start', position: 'relative', overflow: 'hidden' }}>
        <svg width="96" height="96" viewBox="0 0 24 24" fill="none" style={{ position: 'absolute', right: -18, bottom: -22, opacity: .06, pointerEvents: 'none' }}><path d="M4 3h13l-9 9h9l-13 9h13" stroke={a.color} strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" /></svg>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Monogram text={a.short} color={a.color} size={34} r={11} />
          <Tap onClick={onOpen} style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--ink-1)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.bank}</div>
            <div className="z-num" style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 1 }}>{fmtAcct(a.acct)}</div>
          </Tap>
          <Tap onClick={onRefresh}><div style={{ width: 28, height: 28, borderRadius: 9, background: 'var(--surface-3)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name="refresh" size={14} color="var(--brand)" style={{ animation: refreshing ? 'zspin .8s linear infinite' : 'none' }} /></div></Tap>
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
          {reauth
            ? <span style={{ display: 'flex', alignItems: 'center', gap: 5, color: 'var(--z-amber)', fontSize: 11, fontWeight: 700 }}><span style={{ width: 7, height: 7, borderRadius: 9, background: 'var(--z-amber)' }} />Reconnect to view</span>
            : <span className="z-num" style={{ fontSize: 15, fontWeight: 800, color: 'var(--ink-1)' }}>{refreshing ? '—' : money2(a.balance)}</span>}
          <span style={{ fontSize: 10, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>{refreshing ? 'Refreshing…' : (reauth ? 'Expired' : (a.updated || '').replace('Updated ', ''))}</span>
        </div>
        {reauth
          ? <Tap onClick={onRefresh}><div style={chipPrimary}>Reconnect</div></Tap>
          : <div style={{ display: 'flex', gap: 7 }}>
              <Tap onClick={onIn} style={{ flex: 1, minWidth: 0 }}><div style={chipPrimary}><I name="deposit" size={12} color="var(--brand-deep)" style={{ flexShrink: 0, animation: 'zArrowIn 1.7s ease-in-out infinite', transformStyle: 'preserve-3d' }} /><span style={chipLabel}>Fund Zitch</span></div></Tap>
              <Tap onClick={onOut} style={{ flex: 1, minWidth: 0 }}><div style={chipGhost2}><I name="withdraw" size={12} color="var(--brand-deep)" style={{ flexShrink: 0, animation: 'zArrowOut 1.7s ease-in-out infinite -.85s', transformStyle: 'preserve-3d' }} /><span style={chipLabel}>{'Fund ' + tag}</span></div></Tap>
            </div>}
      </div>
    );
  }

  function SkelCard() {
    return <div style={{ width: 236, flexShrink: 0, borderRadius: 18, background: 'var(--surface)', boxShadow: 'var(--shadow-card)', border: '1px solid var(--line)', padding: 13, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <div className="z-skel" style={{ width: 34, height: 34, borderRadius: 11 }} />
        <div style={{ flex: 1 }}><div className="z-skel" style={{ height: 11, borderRadius: 6, width: '70%' }} /><div className="z-skel" style={{ height: 9, borderRadius: 6, width: '45%', marginTop: 7 }} /></div>
      </div>
      <div className="z-skel" style={{ height: 18, borderRadius: 7, width: '55%' }} />
      <div className="z-skel" style={{ height: 34, borderRadius: 10 }} />
    </div>;
  }

  function ConnectTile({ full, onClick }) {
    if (full) {
      return <Tap onClick={onClick}>
        <div style={{ borderRadius: 18, border: '2px dashed var(--z-teal-200)', background: 'linear-gradient(180deg, rgba(15,162,149,.06), rgba(15,162,149,.015))', padding: '16px', display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{ width: 46, height: 46, borderRadius: 14, background: 'rgba(15,162,149,.16)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><I name="plus" size={23} color="var(--brand)" stroke={2.2} /></div>
          <div style={{ flex: 1 }}><div style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink-1)' }}>Connect a bank</div><div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 2 }}>See your balances and move money into Zitch in seconds.</div></div>
          <I name="right" size={20} color="var(--brand)" />
        </div>
      </Tap>;
    }
    return <Tap onClick={onClick} style={{ flexShrink: 0, scrollSnapAlign: 'start' }}>
      <div style={{ width: 132, height: '100%', minHeight: 128, borderRadius: 18, border: '2px dashed var(--z-teal-200)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10, textAlign: 'center', padding: 14 }}>
        <div style={{ width: 44, height: 44, borderRadius: 14, background: 'rgba(15,162,149,.14)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name="plus" size={22} color="var(--brand)" stroke={2.2} /></div>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--brand-deep)', lineHeight: 1.2 }}>Connect<br />a bank</div>
      </div>
    </Tap>;
  }

  // from → to pill in the move-money sheet
  function AcctPill({ short, color, label, balance }) {
    return <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 9, padding: '10px 11px', borderRadius: 14, background: 'var(--surface-3)' }}>
      <Monogram text={short} color={color} size={32} r={10} />
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink-1)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</div>
        <div className="z-num" style={{ fontSize: 11, color: 'var(--ink-3)' }}>{balance != null ? money2(balance) : '—'}</div>
      </div>
    </div>;
  }

  // ---------- Connected accounts strip ----------
  function ConnectedAccounts() {
    const app = useApp(); const n = app.nav;
    const [move, setMove] = useState(null); // { account, direction }
    const [manageFor, setManageFor] = useState(null);
    const [refreshingId, setRefreshingId] = useState(null);
    const list = app.linkedAccounts;

    const doRefresh = (a) => {
      if (refreshingId) return;
      setRefreshingId(a.id);
      setManageFor(null);
      setTimeout(() => {
        if (a.status === 'reauth' || a.balance == null) {
          app.refreshBank(a.id, { status: 'active', balance: 60000 + Math.round(Math.random() * 180000), updated: 'Updated just now' });
          app.toast(a.bank + ' reconnected');
        } else {
          const delta = (Math.random() < 0.5 ? -1 : 1) * Math.round(Math.random() * 4000);
          app.refreshBank(a.id, { balance: Math.max(0, a.balance + delta), updated: 'Updated just now' });
        }
        setRefreshingId(null);
      }, 1200);
    };
    const openMove = (account, direction) => setMove({ account, direction });

    return (
      <div style={{ padding: '12px 0 0' }}>
        <div style={{ padding: '0 18px' }}>
          <SLabel action={list.length ? '+ Add' : null} onAction={() => n.push('linkbank')}>Connected accounts</SLabel>
        </div>
        {app.linkedLoading ? (
          <div style={{ display: 'flex', gap: 14, overflowX: 'auto', padding: '2px 16px 4px' }}>{[0, 1].map(i => <SkelCard key={i} />)}</div>
        ) : list.length === 0 ? (
          <div style={{ padding: '0 16px' }}><ConnectTile full onClick={() => n.push('linkbank')} /></div>
        ) : (
          <div style={{ display: 'flex', gap: 14, overflowX: 'auto', alignItems: 'stretch', padding: '2px 16px 4px', scrollSnapType: 'x mandatory', scrollPaddingLeft: 16 }}>
            {list.map(a => <LinkedBankCard key={a.id} a={a} refreshing={refreshingId === a.id} onIn={() => openMove(a, 'in')} onOut={() => openMove(a, 'out')} onRefresh={() => doRefresh(a)} onOpen={() => setManageFor(a)} />)}
            <ConnectTile onClick={() => n.push('linkbank')} />
          </div>
        )}
        {move && <MoveSheet account={move.account} direction={move.direction} onClose={() => setMove(null)} />}
        {manageFor && <AccountSheet account={manageFor} onClose={() => setManageFor(null)} onMove={(d) => { const a = manageFor; setManageFor(null); setTimeout(() => openMove(a, d), 60); }} onRefresh={() => doRefresh(manageFor)} />}
      </div>
    );
  }

  // ---------- Manage account sheet (fund in / fund out / refresh / unlink) ----------
  function AccountSheet({ account, onClose, onMove, onRefresh }) {
    const app = useApp();
    const reauth = account.status === 'reauth' || account.balance == null;
    const tag = account.tag || account.bank;
    const rows = reauth
      ? [['refresh', 'Reconnect bank', 'Restore access to balances', '#0FA295', onRefresh]]
      : [['deposit', 'Fund Zitch wallet', 'Move money in from ' + account.bank, '#16A34A', () => onMove('in')],
         ['withdraw', 'Fund ' + tag, 'Send money out to ' + account.bank, '#0FA295', () => onMove('out')],
         ['refresh', 'Refresh balance', 'Sync the latest balance', '#0FA295', onRefresh]];
    return <Sheet onClose={onClose}>{(close) => {
      const run = (fn) => { close(); setTimeout(fn, 280); };
      return (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
            <Monogram text={account.short} color={account.color} size={48} r={15} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 16, fontWeight: 800, color: 'var(--ink-1)' }}>{account.bank}</div>
              <div className="z-num" style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 1 }}>{account.name} · {fmtAcct(account.acct)}</div>
            </div>
          </div>
          <div style={{ borderRadius: 14, background: 'var(--surface-2)', border: '1.5px solid var(--line)', padding: '13px 16px', marginBottom: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 13, color: 'var(--ink-3)' }}>Available balance</span>
            <span className="z-num" style={{ fontSize: 16, fontWeight: 800, color: reauth ? 'var(--z-amber)' : 'var(--ink-1)' }}>{reauth ? 'Reconnect' : money2(account.balance)}</span>
          </div>
          {rows.map((r, i) => (
            <Tap key={i} onClick={() => run(r[4])}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 13, padding: '12px 4px', borderTop: i > 0 ? '1px solid var(--line)' : 'none' }}>
                <div style={{ width: 40, height: 40, borderRadius: 12, background: r[3] + '1F', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name={r[0]} size={19} color={r[3]} /></div>
                <div style={{ flex: 1 }}><div style={{ fontSize: 14.5, fontWeight: 700, color: 'var(--ink-1)' }}>{r[1]}</div><div style={{ fontSize: 12, color: 'var(--ink-3)' }}>{r[2]}</div></div>
                <I name="right" size={18} color="var(--ink-3)" />
              </div>
            </Tap>
          ))}
          <Tap onClick={() => run(() => { app.unlinkBank(account.id); app.toast(account.bank + ' unlinked'); })}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 13, padding: '12px 4px', borderTop: '1px solid var(--line)' }}>
              <div style={{ width: 40, height: 40, borderRadius: 12, background: 'rgba(255,59,59,.12)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name="unlink" size={19} color="var(--z-red)" /></div>
              <div style={{ flex: 1 }}><div style={{ fontSize: 14.5, fontWeight: 700, color: 'var(--z-red)' }}>Unlink bank</div><div style={{ fontSize: 12, color: 'var(--ink-3)' }}>Remove this connection from Zitch</div></div>
            </div>
          </Tap>
          <div style={{ height: 6 }} />
        </div>
      );
    }}</Sheet>;
  }

  // ---------- Move money: Fund Zitch (in, via Mono) or Fund {bank} (out, PIN) ----------
  function MoveSheet({ account, direction, onClose }) {
    const app = useApp();
    const [amt, setAmt] = useState('');
    const [stage, setStage] = useState('form'); // form | mono | bio | pin
    const amount = Number(amt || 0);
    const inbound = direction === 'in';
    const over = !inbound && amount > app.balance;
    const valid = amount >= 100 && !over;
    const zitch = { short: 'ZW', color: '#0FA295', label: 'Zitch Wallet', balance: app.balance };
    const bankP = { short: account.short, color: account.color, label: account.bank, balance: account.balance };
    const from = inbound ? bankP : zitch;
    const to = inbound ? zitch : bankP;
    const finish = () => {
      onClose();
      if (inbound) {
        app.fund(amount);
        app.addTxn({ mono: account.short, t: 'Funded from ' + account.bank, cat: 'fund', amt: amount, col: account.color });
        if (account.balance != null) app.refreshBank(account.id, { balance: Math.max(0, account.balance - amount), updated: 'Updated just now' });
        const nb = app.balance + amount;
        setTimeout(() => app.nav.success({ title: 'Funding initiated', message: `${money2(amount)} is moving from your ${account.bank} account into Zitch.`,
          rows: [['From', account.bank + ' · ' + fmtAcct(account.acct)], ['To', 'Zitch Wallet'], ['Amount', money2(amount)], ['Fee', '₦0'], ['Via', 'Mono direct debit'], ['New Zitch balance', money2(nb), true]] }), 90);
      } else {
        app.pay(amount, { mono: account.short, t: 'Sent to ' + account.bank, cat: 'transfer', amt: -amount, col: account.color });
        if (account.balance != null) app.refreshBank(account.id, { balance: account.balance + amount, updated: 'Updated just now' });
        const nb = app.balance - amount;
        setTimeout(() => app.nav.success({ title: 'Money sent', message: `${money2(amount)} sent from Zitch to your ${account.bank} account.`,
          rows: [['From', 'Zitch Wallet'], ['To', account.bank + ' · ' + fmtAcct(account.acct)], ['Amount', money2(amount)], ['Fee', '₦0'], ['New Zitch balance', money2(nb), true]] }), 90);
      }
    };
    return (<>
      {stage === 'form' && <Sheet onClose={onClose}>{(close) => (
        <div>
          <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--ink-1)' }}>{inbound ? 'Fund Zitch' : 'Fund ' + (account.tag || account.bank)}</div>
          <div style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 3, marginBottom: 16 }}>{inbound ? `Move money from ${account.bank} into your Zitch wallet.` : `Send money from Zitch to your ${account.bank} account.`}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 18 }}>
            <AcctPill {...from} />
            <div style={{ width: 30, height: 30, borderRadius: 999, background: 'var(--brand)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, boxShadow: '0 6px 14px -6px rgba(0,132,123,.9)' }}><I name="arrowR" size={16} color="#fff" stroke={2.4} /></div>
            <AcctPill {...to} />
          </div>
          <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--ink-2)', marginBottom: 8 }}>Amount</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, height: 58, padding: '0 16px', borderRadius: 15, background: 'var(--surface)', border: '1.5px solid ' + (over ? 'var(--z-red)' : (amount > 0 ? 'var(--brand)' : 'var(--line)')), marginBottom: 10 }}>
            <span style={{ fontWeight: 800, fontSize: 22, color: 'var(--ink-2)' }}>₦</span>
            <input value={amt} onChange={(e) => setAmt(e.target.value.replace(/\D/g, ''))} placeholder="0" inputMode="numeric" className="z-num" style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontFamily: 'var(--font)', fontSize: 24, fontWeight: 800, color: 'var(--ink-1)', minWidth: 0 }} />
          </div>
          {over
            ? <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}><span style={{ fontSize: 12, fontWeight: 700, color: 'var(--z-red)' }}>Over your Zitch balance</span><span className="z-num" style={{ fontSize: 12, color: 'var(--ink-3)' }}>Bal {money2(app.balance)}</span></div>
            : <div style={{ textAlign: 'right', fontSize: 12, color: 'var(--ink-3)', marginBottom: 14 }}>{inbound ? account.bank + ': ' : 'Zitch: '}<span className="z-num" style={{ fontWeight: 700, color: 'var(--ink-2)' }}>{money2(inbound ? (account.balance || 0) : app.balance)}</span></div>}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 8, marginBottom: 18 }}>
            {[5000, 10000, 25000, 50000].map(a => { const on = String(a) === amt; return <Tap key={a} onClick={() => setAmt(String(a))}><div className="z-num" style={{ textAlign: 'center', padding: '10px 4px', borderRadius: 11, fontSize: 13, fontWeight: 700, background: on ? 'var(--brand)' : 'var(--surface)', color: on ? '#fff' : 'var(--ink-1)', border: '1.5px solid ' + (on ? 'var(--brand)' : 'var(--line)') }}>{fmtK(a)}</div></Tap>; })}
          </div>
          {inbound
            ? <PrimaryButton label="Continue with Mono" icon="arrowR" disabled={!valid} onClick={() => setStage('mono')} />
            : <PrimaryButton label={amount > 0 ? 'Send ' + money2(amount) : 'Enter amount'} icon="send" disabled={!valid} onClick={() => setStage(app.biometrics ? 'bio' : 'pin')} />}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 12, color: 'var(--ink-3)', fontSize: 11.5 }}><I name="lock" size={12} color="var(--ink-3)" />{inbound ? 'Secured by Mono · you authorize each debit' : 'Secured by Zitch'}</div>
          <div style={{ height: 6 }} />
        </div>
      )}</Sheet>}
      {stage === 'mono' && <MonoFlow intent="fund" bank={{ id: account.bankId, name: account.bank, short: account.short, color: account.color, tag: account.tag }} amount={amount} onClose={() => { onClose(); app.toast('Funding cancelled'); }} onDone={finish} />}
      {stage === 'bio' && <BiometricScan title={'Send to ' + account.bank} subtitle={'Authorize ' + money2(amount)} onDone={finish} onFallback={() => setStage('pin')} onClose={onClose} />}
      {stage === 'pin' && <PinSheet amount={amount} onDone={finish} onClose={onClose} onBio={app.biometrics ? () => setStage('bio') : null} />}
    </>);
  }

  // ---------- Home summary: total across connected banks ----------
  function LinkedBanksHome() {
    const app = useApp();
    const list = app.linkedAccounts;
    if (!list.length) {
      return <Tap onClick={() => app.nav.push('linkbank')}>
        <div style={{ margin: '12px 16px 0', borderRadius: 16, padding: '13px 15px', background: 'var(--surface)', boxShadow: 'var(--shadow-card)', display: 'flex', alignItems: 'center', gap: 12, border: '1px dashed var(--z-teal-200)' }}>
          <div style={{ width: 38, height: 38, borderRadius: 12, background: 'rgba(15,162,149,.14)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><I name="link" size={19} color="var(--brand)" /></div>
          <div style={{ flex: 1 }}><div style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink-1)' }}>Connect a bank</div><div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 1 }}>See all your balances in one place</div></div>
          <I name="right" size={18} color="var(--ink-3)" />
        </div>
      </Tap>;
    }
    const active = list.filter(a => a.balance != null);
    const total = active.reduce((s, a) => s + a.balance, 0);
    const needs = list.length - active.length;
    return <Tap onClick={() => app.nav.tab('wallet')}>
      <div style={{ margin: '12px 16px 0', borderRadius: 14, padding: '10px 13px', background: 'var(--surface)', boxShadow: 'var(--shadow-card)' }}>
        {/* header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <div style={{ width: 24, height: 24, borderRadius: 8, background: 'rgba(15,162,149,.12)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name="bank" size={14} color="var(--brand)" /></div>
          <div style={{ flex: 1, fontSize: 13, fontWeight: 700, color: 'var(--ink-1)' }}>Connected banks</div>
          {needs ? <span style={{ fontSize: 10.5, fontWeight: 700, color: 'var(--z-amber)', background: 'rgba(245,166,35,.14)', padding: '3px 8px', borderRadius: 999 }}>{needs} to reconnect</span> : null}
          <I name="right" size={17} color="var(--ink-3)" style={{ marginLeft: 6 }} />
        </div>
        {/* total held in linked banks */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 7 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="z-num" style={{ fontSize: 17, fontWeight: 800, color: 'var(--ink-1)' }}>{app.showBal ? window.ZUI.fmtN(total) : '₦ ••••••'}</div>
            <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 2 }}>across {list.length} linked {list.length === 1 ? 'bank' : 'banks'}</div>
          </div>
          <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--line)' }} />
          <div style={{ flex: 1, minWidth: 0, textAlign: 'right' }}>
            <div className="z-num" style={{ fontSize: 17, fontWeight: 800, color: 'var(--brand-deep)' }}>{app.showBal ? window.ZUI.fmtN(app.balance + total) : '••••'}</div>
            <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 2 }}>total with Zitch</div>
          </div>
        </div>
      </div>
    </Tap>;
  }

  // ---------- Link-a-bank onboarding screen ----------
  function LinkBank() {
    const app = useApp();
    const [mono, setMono] = useState(false);
    const Screen = window.Screen;
    const onDone = (code, bank) => {
      setMono(false);
      const acct = '0' + Math.floor(100000000 + Math.random() * 899999999);
      app.linkBank({ id: 'la' + Date.now(), bankId: bank.id, bank: bank.name, short: bank.short, tag: bank.tag || bank.name.split(' ')[0], color: bank.color, acct, name: 'WILLIAM A. ADEYEMI', balance: 40000 + Math.round(Math.random() * 220000), updated: 'Updated just now', status: 'active' });
      app.nav.pop();
      setTimeout(() => app.toast(bank.name + ' connected'), 140);
    };
    const checks = [
      ['eye', 'Read-only by default', 'We only see what you allow — never move money without you.'],
      ['shield', 'Bank-grade, you consent each time', '256-bit encryption. You approve every connection and debit.'],
      ['unlink', 'Unlink anytime', 'Remove a connected bank in one tap, from your wallet.'],
    ];
    return <Screen title="Add a bank" onBack={app.nav.pop} footer={<PrimaryButton label="Connect a bank" icon="link" onClick={() => setMono(true)} />}>
      {/* hero illustration */}
      <div style={{ borderRadius: 24, background: 'var(--surface-3)', padding: '34px 20px 38px', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 22 }}>
        <div style={{ position: 'relative', width: 110, height: 96 }}>
          <div style={{ position: 'absolute', right: 2, top: -4, width: 34, height: 34, borderRadius: '50%', background: 'var(--z-cyan)' }} />
          <div style={{ position: 'absolute', left: -2, bottom: -2, width: 22, height: 22, borderRadius: '50%', background: 'rgba(15,162,149,.32)' }} />
          <div style={{ width: 82, height: 82, borderRadius: 26, background: 'linear-gradient(135deg,#23B1A8,#00847B)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '8px auto 0', boxShadow: '0 16px 30px -12px rgba(0,132,123,.85)' }}>
            <I name="link" size={38} color="#fff" stroke={2} />
          </div>
        </div>
      </div>
      <div style={{ fontSize: 24, fontWeight: 800, color: 'var(--ink-1)', textAlign: 'center', letterSpacing: '-.01em' }}>Link your bank to Zitch</div>
      <div style={{ fontSize: 14, color: 'var(--ink-3)', textAlign: 'center', margin: '8px auto 0', maxWidth: 320, lineHeight: 1.5 }}>See your balances and move money in — securely, with your consent. Powered by Mono.</div>
      <div style={{ marginTop: 24, display: 'flex', flexDirection: 'column', gap: 14 }}>
        {checks.map((c, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 13 }}>
            <div style={{ width: 38, height: 38, borderRadius: 12, background: 'rgba(15,162,149,.12)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><I name={c[0]} size={19} color="var(--brand)" /></div>
            <div style={{ flex: 1, paddingTop: 1 }}>
              <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink-1)' }}>{c[1]}</div>
              <div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 2, lineHeight: 1.45 }}>{c[2]}</div>
            </div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 22, textAlign: 'center', fontSize: 12, color: 'var(--ink-3)' }}>Your bank login never touches Zitch's servers.</div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 8 }}>
        <I name="lock" size={13} color="var(--brand)" /><span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--brand-deep)' }}>Secured by Mono</span>
      </div>
      {mono && <MonoFlow intent="link" onClose={() => { setMono(false); app.toast('Connection cancelled'); }} onDone={onDone} />}
    </Screen>;
  }

  // ---------- Mono Connect (simulated widget) ----------
  function MField({ label, value, onChange, placeholder, pass }) {
    return <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 7 }}>{label}</div>
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} type={pass ? 'password' : 'text'}
        style={{ width: '100%', height: 50, padding: '0 15px', borderRadius: 13, background: 'var(--surface)', border: '1.5px solid var(--line)', outline: 'none', fontFamily: 'var(--font)', fontSize: 15, fontWeight: 600, color: 'var(--ink-1)' }} />
    </div>;
  }

  function MonoFlow({ intent, bank: bankProp, amount, onClose, onDone }) {
    const [bank, setBank] = useState(bankProp || null);
    const [step, setStep] = useState(bankProp ? 'login' : 'pick');
    const [q, setQ] = useState('');
    const [u, setU] = useState('');
    const [p, setP] = useState('');
    const [vis, setVis] = useState(false);
    useEffect(() => { const t = setTimeout(() => setVis(true), 20); return () => clearTimeout(t); }, []);
    useEffect(() => {
      if (step === 'connecting') { const t = setTimeout(() => setStep(intent === 'fund' ? 'authorize' : 'consent'), 1700); return () => clearTimeout(t); }
      if (step === 'success') { const t = setTimeout(() => onDone('CODE-' + Math.random().toString(36).slice(2, 10).toUpperCase(), bank), 1050); return () => clearTimeout(t); }
    }, [step]);
    const close = () => { setVis(false); setTimeout(onClose, 240); };
    const banks = window.ZDATA.BANKS;
    const list = banks.filter(b => b.name.toLowerCase().includes(q.toLowerCase()));

    let body = null;
    if (step === 'pick') {
      body = <div>
        <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--ink-1)' }}>Select your bank</div>
        <div style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 3, marginBottom: 14 }}>Choose the bank you want to connect to Zitch.</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, height: 46, padding: '0 14px', borderRadius: 13, background: 'var(--surface-3)', marginBottom: 8 }}>
          <I name="search" size={18} color="var(--ink-3)" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search banks" style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontFamily: 'var(--font)', fontSize: 15, color: 'var(--ink-1)' }} />
        </div>
        <div>
          {list.map((b, i) => (
            <Tap key={b.id} onClick={() => { setBank(b); setStep('login'); }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 0', borderTop: i > 0 ? '1px solid var(--line)' : 'none' }}>
                <Monogram text={b.short} color={b.color} size={40} r={12} />
                <div style={{ flex: 1, fontSize: 15, fontWeight: 600, color: 'var(--ink-1)' }}>{b.name}</div>
                <I name="right" size={18} color="var(--ink-3)" />
              </div>
            </Tap>
          ))}
          {!list.length && <div style={{ fontSize: 13, color: 'var(--ink-3)', padding: '18px 2px' }}>No banks match “{q}”.</div>}
        </div>
      </div>;
    } else if (step === 'login') {
      body = <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18 }}>
          <Monogram text={bank.short} color={bank.color} size={46} r={14} />
          <div><div style={{ fontSize: 16, fontWeight: 800, color: 'var(--ink-1)' }}>{bank.name}</div><div style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>Internet banking login</div></div>
        </div>
        <MField label="User ID" value={u} onChange={setU} placeholder="Username or account number" />
        <MField label="Password" value={p} onChange={setP} placeholder="••••••••" pass />
        <div style={{ display: 'flex', gap: 8, padding: '12px 14px', borderRadius: 13, background: 'rgba(15,162,149,.08)', margin: '4px 0 16px' }}>
          <I name="lock" size={16} color="var(--brand)" style={{ flexShrink: 0, marginTop: 1 }} />
          <div style={{ fontSize: 12, color: 'var(--brand-deep)', lineHeight: 1.45 }}>Your credentials are encrypted by Mono and never shared with Zitch.</div>
        </div>
        <PrimaryButton label="Log in securely" onClick={() => setStep('connecting')} />
      </div>;
    } else if (step === 'connecting') {
      body = <div style={{ textAlign: 'center', padding: '34px 0 26px' }}>
        <div style={{ width: 64, height: 64, margin: '0 auto 18px', borderRadius: '50%', border: '4px solid var(--surface-3)', borderTopColor: 'var(--brand)', animation: 'zspin .8s linear infinite' }} />
        <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink-1)' }}>Securely connecting…</div>
        <div style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 5 }}>Logging in to {bank.name} and fetching your account.</div>
      </div>;
    } else if (step === 'consent') {
      body = <div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', marginBottom: 18 }}>
          <Monogram text={bank.short} color={bank.color} size={52} r={16} />
          <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--ink-1)', marginTop: 12 }}>Allow Zitch to access</div>
          <div style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 4, maxWidth: 300 }}>Zitch will get read-only access to the following from {bank.name}.</div>
        </div>
        <div style={{ borderRadius: 16, border: '1.5px solid var(--line)', overflow: 'hidden', marginBottom: 18 }}>
          {[['Account balance', 'See available & ledger balance'], ['Account details', 'Name, number & bank'], ['Transaction history', 'Past inflow & outflow']].map((r, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '13px 14px', borderTop: i > 0 ? '1px solid var(--line)' : 'none' }}>
              <div style={{ width: 30, height: 30, borderRadius: 9, background: 'rgba(15,162,149,.14)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><I name="check" size={16} color="var(--brand)" stroke={2.4} /></div>
              <div style={{ flex: 1 }}><div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink-1)' }}>{r[0]}</div><div style={{ fontSize: 12, color: 'var(--ink-3)' }}>{r[1]}</div></div>
            </div>
          ))}
        </div>
        <PrimaryButton label="Allow access" onClick={() => setStep('success')} />
        <Tap onClick={close}><div style={{ textAlign: 'center', padding: '13px', color: 'var(--ink-3)', fontWeight: 700, fontSize: 14, marginTop: 4 }}>Cancel</div></Tap>
      </div>;
    } else if (step === 'authorize') {
      body = <div>
        <div style={{ textAlign: 'center', marginBottom: 4 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink-3)' }}>Authorize debit</div>
          <div className="z-num" style={{ fontSize: 34, fontWeight: 800, color: 'var(--ink-1)', marginTop: 5 }}>{money2(amount)}</div>
        </div>
        <div style={{ borderRadius: 16, border: '1.5px solid var(--line)', padding: '2px 16px', margin: '16px 0 16px' }}>
          <Row2 k="From" v={bank.name} />
          <Row2 k="To" v="Zitch Wallet" />
          <Row2 k="Type" v="One-time debit" />
        </div>
        <div style={{ display: 'flex', gap: 8, padding: '12px 14px', borderRadius: 13, background: 'rgba(15,162,149,.08)', marginBottom: 16 }}>
          <I name="shield" size={16} color="var(--brand)" style={{ flexShrink: 0, marginTop: 1 }} />
          <div style={{ fontSize: 12, color: 'var(--brand-deep)', lineHeight: 1.45 }}>You are authorizing a single debit of {money2(amount)}. Mono will not store these details.</div>
        </div>
        <PrimaryButton label={'Approve ' + money2(amount)} icon="lock" onClick={() => setStep('success')} />
        <Tap onClick={close}><div style={{ textAlign: 'center', padding: '13px', color: 'var(--ink-3)', fontWeight: 700, fontSize: 14, marginTop: 4 }}>Cancel</div></Tap>
      </div>;
    } else if (step === 'success') {
      body = <div style={{ textAlign: 'center', padding: '26px 0 18px' }}>
        <div style={{ width: 84, height: 84, margin: '0 auto', borderRadius: '50%', background: 'rgba(0,181,29,.14)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ width: 58, height: 58, borderRadius: '50%', background: 'var(--z-lime)', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 12px 26px -8px rgba(0,181,29,.6)' }}><I name="check" size={30} color="#fff" stroke={3} /></div>
        </div>
        <div style={{ fontSize: 19, fontWeight: 800, color: 'var(--ink-1)', marginTop: 16 }}>{intent === 'fund' ? 'Authorized' : 'Bank connected'}</div>
        <div style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 5 }}>{intent === 'fund' ? 'Returning you to Zitch…' : bank.name + ' is now linked to your wallet.'}</div>
      </div>;
    }

    return (
      <div onClick={close} style={{ position: 'absolute', inset: 0, zIndex: 80, display: 'flex', alignItems: 'flex-end', background: vis ? 'rgba(2,20,17,.55)' : 'rgba(2,20,17,0)', transition: 'background .25s' }}>
        <div onClick={(e) => e.stopPropagation()} style={{ width: '100%', maxHeight: '94%', overflowY: 'auto', background: 'var(--surface)', borderRadius: '26px 26px 0 0', transform: vis ? 'translateY(0)' : 'translateY(100%)', transition: 'transform .3s var(--ease-spring)', padding: '14px 18px 24px', boxShadow: '0 -10px 40px rgba(0,0,0,.3)' }}>
          <div style={{ width: 40, height: 5, borderRadius: 3, background: 'var(--line)', margin: '0 auto 12px' }} />
          {/* Mono provider chrome */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '0 2px 16px' }}>
            <div style={{ width: 26, height: 26, borderRadius: 8, background: '#101935', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <div style={{ width: 11, height: 11, borderRadius: '50%', border: '2.5px solid var(--z-cyan)' }} />
            </div>
            <span style={{ fontSize: 15, fontWeight: 800, color: 'var(--ink-1)', letterSpacing: '-.01em' }}>Mono</span>
            <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-3)' }}>· secure connect</span>
            <div style={{ flex: 1 }} />
            {(step === 'pick' || step === 'login') && <Tap onClick={close}><div style={{ width: 32, height: 32, borderRadius: 10, background: 'var(--surface-3)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name="x" size={17} color="var(--ink-2)" /></div></Tap>}
          </div>
          {body}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 18, color: 'var(--ink-3)', fontSize: 11.5 }}>
            <I name="lock" size={12} color="var(--ink-3)" />Bank-grade encryption · Secured by Mono
          </div>
        </div>
      </div>
    );
  }

  Object.assign(window, { ConnectedAccounts, LinkedBankCard, ConnectTile, AccountSheet, MoveSheet, AcctPill, LinkedBanksHome, LinkBank, MonoFlow });
})();
