// tabs.jsx — Home + tab screens + bottom nav + more sheet + history
(function () {
  const { useState } = React;
  const D = window.ZDATA;
  const { fmtN, fmtK } = window.ZUI;
  const { useApp, Screen, Tap, PrimaryButton, ListRow, Monogram, Toggle, Sheet, OptionSheet, BiometricScan } = window;
  const I = (props) => React.createElement(window.ZIcon, props);
  const SB = () => React.createElement(window.StatusBar, null);
  const Avatar = (props) => React.createElement(window.Avatar, props);
  const ZMark = (props) => React.createElement(window.ZMark, props);

  const GRID = [
    { key: 'air', label: 'Airtime', icon: 'airtime', badge: '6% off', go: (n) => n.push('airtime', { initialTab: 'airtime' }) },
    { key: 'data', label: 'Data', icon: 'data', go: (n) => n.push('airtime', { initialTab: 'data' }) },
    { key: 'bet', label: 'Betting', icon: 'dice', go: (n) => n.push('betting') },
    { key: 'tv', label: 'Cable TV', icon: 'tv', go: (n) => n.push('cable') },
    { key: 'save', label: 'Save', icon: 'fixed', go: (n) => n.push('fixedsave') },
    { key: 'loan', label: 'Loan', icon: 'loan', badge: 'Hot', hot: true, go: (n) => n.push('loan') },
    { key: 'exam', label: 'Exams', icon: 'jamb', go: (n) => n.push('exams') },
    { key: 'more', label: 'More', icon: 'more', go: (n, setMore) => setMore(true) },
  ];

  const SVC_COLOR = {
    airtime: '#0FA295', data: '#2D7FF9', dice: '#F5A623', tv: '#7A5CFF', fixed: '#1EA05E',
    loan: '#E8590C', jamb: '#F5760A', bills: '#F59E0B', send: '#0FA295', insurance: '#16A34A',
    remita: '#2D7FF9', movie: '#D6336C', convert: '#0CA5B8', invite: '#7A5CFF', more: '#6E8B86',
  };
  const svcColor = (icon) => SVC_COLOR[icon] || '#0FA295';

  const MORE = [
    { key: 'elec', label: 'Electricity', icon: 'bills', go: (n) => n.push('electricity') },
    { key: 'send', label: 'Send money', icon: 'send', go: (n) => n.push('transfer') },
    { key: 'air', label: 'Airtime', icon: 'airtime', go: (n) => n.push('airtime', { initialTab: 'airtime' }) },
    { key: 'data', label: 'Data', icon: 'data', go: (n) => n.push('airtime', { initialTab: 'data' }) },
    { key: 'tv', label: 'Cable TV', icon: 'tv', go: (n) => n.push('cable') },
    { key: 'bet', label: 'Betting', icon: 'dice', go: (n) => n.push('betting') },
    { key: 'exam', label: 'Exams', icon: 'jamb', go: (n) => n.push('exams') },
    { key: 'ins', label: 'Insurance', icon: 'insurance', go: (n) => n.push('coming', { title: 'Insurance', icon: 'insurance', note: 'Protect your health, car & devices.' }) },
    { key: 'rem', label: 'Remita', icon: 'remita', go: (n) => n.push('coming', { title: 'Remita', icon: 'remita', note: 'Pay government & institution bills.' }) },
    { key: 'movie', label: 'Movie', icon: 'movie', go: (n) => n.push('coming', { title: 'Movie Tickets', icon: 'movie', note: 'Book cinema seats in seconds.' }) },
    { key: 'conv', label: 'Convert', icon: 'convert', go: (n) => n.push('coming', { title: 'Convert Currency', icon: 'convert', note: 'Swap NGN, USD & more.' }) },
    { key: 'invite', label: 'Invite', icon: 'invite', go: (n) => n.push('coming', { title: 'Invite & Earn', icon: 'invite', note: 'Earn ₦500 per friend who joins.' }) },
  ];

  function Card({ children, style }) { return <div style={{ margin: '12px 16px 0', borderRadius: 18, background: 'var(--surface)', boxShadow: 'var(--shadow-card)', padding: 16, ...style }}>{children}</div>; }
  function SectionLabel({ children, action, onAction }) {
    return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
      <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--ink-1)' }}>{children}</div>
      {action && <Tap onClick={onAction}><div style={{ fontSize: 13, fontWeight: 600, color: 'var(--brand)' }}>{action}</div></Tap>}
    </div>;
  }
  function Badge({ children, hot }) {
    return <span style={{ position: 'absolute', top: -7, right: -6, fontSize: 9, fontWeight: 700, padding: '3px 6px', borderRadius: 999, whiteSpace: 'nowrap', color: '#fff', background: hot ? 'var(--z-red)' : 'var(--z-amber)', boxShadow: '0 2px 6px rgba(0,0,0,.18)' }}>{children}</span>;
  }
  function TxnRow({ x, divider, onClick }) {
    const neg = x.amt < 0;
    return <Tap onClick={onClick}><div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 0', borderTop: divider ? '1px solid var(--line)' : 'none' }}>
      <Monogram text={x.mono} color={x.col} size={34} r={11} />
      <div style={{ flex: 1, minWidth: 0 }}><div style={{ fontWeight: 600, fontSize: 13, color: 'var(--ink-1)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{x.t}</div><div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 1 }}>{x.time || 'Just now'}</div></div>
      <div style={{ textAlign: 'right' }}><div className="z-num" style={{ fontWeight: 700, fontSize: 13, color: neg ? (x.col || 'var(--ink-1)') : 'var(--z-lime)' }}>{(neg ? '-' : '+') + fmtN(Math.abs(x.amt))}</div><div style={{ fontSize: 10.5, color: x.status === 'Pending' ? 'var(--z-amber)' : 'var(--ink-3)', marginTop: 2 }}>{x.status || 'Successful'}</div></div>
    </div></Tap>;
  }

  // ---------- BOTTOM NAV ----------
  const WA_GLYPH = 'M.057 24l1.687-6.163a11.867 11.867 0 0 1-1.587-5.946C.16 5.335 5.495 0 12.05 0a11.817 11.817 0 0 1 8.413 3.488 11.824 11.824 0 0 1 3.48 8.414c-.003 6.557-5.338 11.892-11.893 11.892a11.9 11.9 0 0 1-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884-.001 2.225.651 3.891 1.746 5.634l-.999 3.648 3.742-.981zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.768.967-.941 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z';
  function BottomNav() {
    const app = useApp();
    const map = { home: 'home', wallet: 'wallet', card: 'cards', user: 'me' };
    const left = [['home', 'Home'], ['wallet', 'Wallet']];
    const right = [['card', 'Cards'], ['user', 'Me']];
    const tabBtn = ([ic, lb]) => {
      const on = app.tab === map[ic];
      return <Tap key={ic} onClick={() => app.nav.tab(map[ic])}>
        <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5, padding: '2px 12px', color: on ? 'var(--brand)' : 'var(--ink-3)' }}>
          {on && <div style={{ position: 'absolute', top: -2, width: 46, height: 30, borderRadius: 11, background: 'rgba(15,162,149,.13)' }} />}
          <I name={ic} size={22} stroke={on ? 2.1 : 1.7} style={{ position: 'relative' }} />
          <span style={{ fontSize: 11, fontWeight: on ? 600 : 500, position: 'relative' }}>{lb}</span>
        </div>
      </Tap>;
    };
    return (
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, zIndex: 30 }}>
        <div style={{ display: 'flex', justifyContent: 'space-around', alignItems: 'flex-end', padding: '10px 8px 6px', background: 'color-mix(in srgb, var(--surface) 90%, transparent)', backdropFilter: 'blur(18px)', borderTop: '1px solid var(--line)' }}>
          {left.map(tabBtn)}
          <Tap onClick={() => app.nav.push('linkwhatsapp')}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, width: 64 }}>
              <div style={{ marginTop: -30, width: 58, height: 58, borderRadius: '50%', background: '#25D366', display: 'flex', alignItems: 'center', justifyContent: 'center', border: '4px solid var(--surface)', boxShadow: '0 4px 6px rgba(18,140,126,.4)' }}>
                <svg width="27" height="27" viewBox="0 0 24 24" fill="#fff"><path d={WA_GLYPH} /></svg>
              </div>
              <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--brand)' }}>WhatsApp</span>
            </div>
          </Tap>
          {right.map(tabBtn)}
        </div>
        <div style={{ height: 22, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--surface)' }}><div style={{ width: 134, height: 5, borderRadius: 3, background: 'var(--ink-1)', opacity: .85 }} /></div>
      </div>
    );
  }

  function TabRoot({ children }) {
    const app = useApp();
    return <div className="z-screen" style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', background: 'var(--bg-grad)' }}>
      <SB />
      <div style={{ flex: 1, overflowY: 'auto', paddingBottom: app.wide ? 28 : 88 }}>
        <div style={{ maxWidth: app.wide ? 680 : 'none', margin: '0 auto' }}>{children}</div>
      </div>
    </div>;
  }

  // ---------- HOME ----------
  function Home() {
    const app = useApp();
    const [more, setMore] = useState(false);
    const [acctCopied, setAcctCopied] = useState(false);
    const n = app.nav;
    return (
      <TabRoot>
        {/* header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '4px 18px 0' }}>
          <Tap onClick={() => app.nav.tab('me')}><Avatar size={38} ring="var(--brand)" /></Tap>
          <div style={{ flex: 1, fontSize: 18, fontWeight: 800, color: 'var(--ink-1)' }}>Hi, William</div>
          <div style={{ display: 'flex', gap: 16, alignItems: 'center', color: 'var(--ink-1)' }}>
            <Tap onClick={() => n.push('coming', { title: 'Support', icon: 'help', note: 'Chat with Zitch support 24/7.' })}><I name="help" size={22} /></Tap>
            <Tap onClick={() => n.push('coming', { title: 'Scan to pay', icon: 'scan', note: 'Scan any Zitch or bank QR.' })}><I name="scan" size={22} /></Tap>
            <Tap onClick={() => n.push('notifications')}><div style={{ position: 'relative' }}><I name="bell" size={22} /><span style={{ position: 'absolute', top: -6, right: -7, minWidth: 16, height: 16, padding: '0 4px', borderRadius: 9, background: 'var(--z-red)', color: '#fff', fontSize: 10, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>24</span></div></Tap>
          </div>
        </div>
        {/* balance hero */}
        <div style={{ margin: '14px 16px 0', borderRadius: 22, background: 'var(--hero-grad)', padding: '16px 18px 18px', position: 'relative', overflow: 'hidden', boxShadow: '0 16px 36px -18px rgba(0,132,123,.75)' }}>
          <div style={{ position: 'absolute', right: -18, bottom: -22, opacity: .18 }}><ZMark size={120} /></div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, color: 'rgba(255,255,255,.88)', fontSize: 13, fontWeight: 500 }}>
              <span style={{ width: 17, height: 17, borderRadius: 9, background: 'rgba(255,255,255,.22)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name="check" size={11} color="#fff" stroke={2.6} /></span>Available Balance
            </div>
            <Tap onClick={() => n.push('history')}><div style={{ display: 'flex', alignItems: 'center', gap: 3, color: '#fff', fontSize: 12.5, fontWeight: 600 }}>Transaction History<I name="right" size={15} color="#fff" /></div></Tap>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 9 }}>
            <div className="z-num" style={{ color: '#fff', fontSize: 31, fontWeight: 700 }}>{app.showBal ? fmtN(app.balance) : '₦ ••••••'}<span style={{ fontSize: 18, opacity: .7 }}>{app.showBal ? '.00' : ''}</span></div>
            <Tap onClick={() => app.setShowBal(!app.showBal)}><I name={app.showBal ? 'eye' : 'eyeoff'} size={17} color="rgba(255,255,255,.85)" /></Tap>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 12 }}>
            <div style={{ position: 'relative' }}>{acctCopied && <div style={{ position: 'absolute', bottom: '100%', left: 0, marginBottom: 7, display: 'flex', alignItems: 'center', gap: 5, background: 'var(--ink-1)', color: 'var(--bg)', fontSize: 11, fontWeight: 700, padding: '5px 10px', borderRadius: 9, whiteSpace: 'nowrap', boxShadow: '0 8px 20px -8px rgba(0,0,0,.55)' }}><I name="check" size={11} color="var(--z-cyan)" stroke={3} />Account number copied</div>}<Tap onClick={() => { try { navigator.clipboard.writeText('9012345678'); } catch (e) { } setAcctCopied(true); setTimeout(() => setAcctCopied(false), 1300); }}><div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'rgba(255,255,255,.85)' }}><div style={{ minWidth: 0 }}><div style={{ fontSize: 11, fontWeight: 600, color: 'rgba(255,255,255,.72)', lineHeight: 1.25 }}>William Adeyemi</div><div className="z-num" style={{ fontSize: 12.5, fontWeight: 600, color: '#fff', lineHeight: 1.25 }}>9012 345 678 · Providus</div></div><I name="copy" size={14} color="rgba(255,255,255,.82)" /></div></Tap></div>
            <Tap onClick={() => n.push('addmoney')}><div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 14px', borderRadius: 999, background: '#fff', color: 'var(--brand-deep)', fontSize: 13, fontWeight: 700, whiteSpace: 'nowrap' }}><I name="plus" size={15} color="var(--brand-deep)" stroke={2.4} />Add Money</div></Tap>
          </div>
        </div>
        {React.createElement(window.LinkedBanksHome)}
        {/* quick actions */}
        <Card style={{ display: 'flex', justifyContent: 'space-around', padding: '16px 10px' }}>
          {[['send', 'Transfer', () => n.push('transfer')], ['airtime', 'Airtime', () => n.push('airtime', { initialTab: 'airtime' })], ['withdraw', 'Withdraw', () => n.push('transfer')]].map(([ic, lb, go]) => (
            <Tap key={lb} onClick={go} style={{ flex: 1 }}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
                <div style={{ width: 48, height: 48, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--z-teal-50)' }}><I name={ic} size={22} color="var(--brand-deep)" /></div>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-1)' }}>{lb}</span>
              </div>
            </Tap>
          ))}
        </Card>
        {/* services grid */}
        <Card style={{ padding: '20px 14px 18px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', rowGap: 20, columnGap: 6 }}>
            {GRID.map(s => (
              <Tap key={s.key} onClick={() => s.go(n, setMore)}>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 7 }}>
                  <div style={{ position: 'relative' }}>
                    <div style={{ width: 48, height: 48, borderRadius: 16, display: 'flex', alignItems: 'center', justifyContent: 'center', background: svcColor(s.icon) + (app.theme === 'dark' ? '33' : '24') }}><I name={s.icon} size={23} color={svcColor(s.icon)} /></div>
                    {s.badge && <Badge hot={s.hot}>{s.badge}</Badge>}
                  </div>
                  <span style={{ fontSize: 11.5, fontWeight: 500, color: 'var(--ink-2)' }}>{s.label}</span>
                </div>
              </Tap>
            ))}
          </div>
        </Card>
        {/* promo */}
        <Tap onClick={() => n.push('fixedsave')}>
          <div style={{ margin: '14px 16px 0', borderRadius: 20, padding: '16px 18px', background: app.theme === 'dark' ? 'linear-gradient(100deg,#0F332C,#143C34)' : 'linear-gradient(100deg,#E8F7F3,#F0FBF8)', display: 'flex', alignItems: 'center', gap: 14, border: '1px solid var(--line)' }}>
            <div style={{ width: 44, height: 44, borderRadius: 13, background: 'rgba(15,162,149,.16)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><I name="fixed" size={23} color="var(--brand)" /></div>
            <div style={{ flex: 1 }}><div style={{ fontWeight: 700, fontSize: 14, color: 'var(--ink-1)' }}>Fixed Save · 22% p.a</div><div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>Grow your savings, locked &amp; safe</div></div>
            <div style={{ padding: '9px 18px', borderRadius: 999, background: 'var(--brand)', color: '#fff', fontSize: 13, fontWeight: 700 }}>Save</div>
          </div>
        </Tap>
        {/* recent */}
        <div style={{ padding: '20px 18px 0' }}>
          <SectionLabel action="See all" onAction={() => n.push('history')}>Recent activity</SectionLabel>
          {app.txns.slice(0, 4).map((x, i) => <TxnRow key={x.id || i} x={x} divider={i > 0} onClick={() => n.push('txn', { x })} />)}
        </div>
        {/* daily interest strip */}
        <Tap onClick={() => n.push('coming', { title: 'Daily Interest', icon: 'spark', note: 'Earn interest on your balance, paid daily.' })}>
          <div style={{ margin: '16px 16px 0', borderRadius: 16, padding: '11px 14px', background: 'var(--surface)', boxShadow: 'var(--shadow-card)', display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ width: 26, height: 26, borderRadius: 8, background: 'rgba(11,161,43,.14)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><I name="spark" size={16} color="var(--z-lime)" /></div>
            <div style={{ flex: 1, fontSize: 12.5, color: 'var(--ink-2)' }}>Act now — start earning <span style={{ color: 'var(--brand)', fontWeight: 700 }}>daily interest</span></div>
            <I name="right" size={16} color="var(--ink-3)" />
          </div>
        </Tap>
        {more && <MoreSheet onClose={() => setMore(false)} onPick={(m) => { setMore(false); setTimeout(() => m.go(n), 240); }} />}
        {app.detected && <SmartPaste num={app.detected} onClose={() => app.clearDetected()} nav={n} />}
      </TabRoot>
    );
  }

  function SmartPaste({ num, onClose, nav }) {
    const fmt = num.length >= 11 ? num.replace(/(\d{4})(\d{3})(\d{4})/, '$1 $2 $3') : num.replace(/(\d{3})(\d{3})(\d{4})/, '$1 $2 $3');
    const isPhone = num.length >= 11;
    return <Sheet onClose={onClose}>{(close) => (
      <div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, marginBottom: 4 }}>
          <div style={{ width: 52, height: 52, borderRadius: 16, background: 'rgba(15,162,149,.14)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name="copy" size={24} color="var(--brand)" /></div>
          <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--ink-1)' }}>{isPhone ? 'Phone number detected' : 'Account number detected'}</div>
          <div className="z-num" style={{ fontSize: 26, fontWeight: 800, color: 'var(--ink-1)', letterSpacing: '.02em' }}>{fmt}</div>
          <div style={{ fontSize: 13, color: 'var(--ink-3)', textAlign: 'center', maxWidth: 280 }}>We noticed you copied this number. What would you like to do?</div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 18 }}>
          <PrimaryButton label="Send money" icon="send" onClick={() => { close(); setTimeout(() => nav.push('transfer', { initialAcct: isPhone ? num.replace(/^0/, '') : num }), 240); }} />
          {isPhone && <Tap onClick={() => { close(); setTimeout(() => nav.push('airtime', { initialTab: 'airtime', initialPhone: num }), 240); }}>
            <div style={{ textAlign: 'center', padding: '15px', borderRadius: 16, background: 'var(--surface-3)', color: 'var(--ink-1)', fontWeight: 700, fontSize: 15 }}>Buy airtime</div>
          </Tap>}
          <Tap onClick={close}><div style={{ textAlign: 'center', padding: '12px', color: 'var(--ink-3)', fontWeight: 600, fontSize: 14 }}>Not now</div></Tap>
        </div>
        <div style={{ height: 6 }} />
      </div>
    )}</Sheet>;
  }

  function MoreSheet({ onClose, onPick }) {
    return <Sheet onClose={onClose}>{(close) => (
      <div>
        <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--ink-1)', marginBottom: 16 }}>All services</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', rowGap: 20, columnGap: 6, paddingBottom: 8 }}>
          {MORE.map(s => (
            <Tap key={s.key} onClick={() => { onPick(s); }}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 7 }}>
                <div style={{ width: 50, height: 50, borderRadius: 16, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--z-teal-50)' }}><I name={s.icon} size={23} color="var(--brand)" /></div>
                <span style={{ fontSize: 11.5, fontWeight: 500, color: 'var(--ink-2)' }}>{s.label}</span>
              </div>
            </Tap>
          ))}
        </div>
      </div>
    )}</Sheet>;
  }

  // ---------- WALLET (multi-wallet) ----------
  function Wallet() {
    const app = useApp(); const n = app.nav;
    const hdrBtn = { width: 40, height: 40, borderRadius: 13, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--surface)', boxShadow: 'var(--shadow-card)' };
    return <TabRoot>
      {/* header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 18px 2px' }}>
        <div style={{ flex: 1, fontSize: 26, fontWeight: 800, color: 'var(--ink-1)' }}>Wallet</div>
        <Tap onClick={() => app.refreshLinked()}><div style={hdrBtn}><I name="refresh" size={19} color="var(--ink-1)" /></div></Tap>
        <Tap onClick={() => n.push('coming', { title: 'Wallet settings', icon: 'settings', note: 'Rename wallets, set a default & manage limits.' })}><div style={hdrBtn}><I name="settings" size={19} color="var(--ink-1)" /></div></Tap>
      </div>
      {/* primary Zitch wallet card */}
      <div style={{ margin: '14px 16px 0', borderRadius: 22, background: 'linear-gradient(135deg,#23B1A8 0%,#00847B 52%,#004D47 100%)', color: '#fff', padding: '12px 18px 13px', position: 'relative', overflow: 'hidden', boxShadow: '0 16px 34px -20px rgba(0,77,71,.95)' }}>
        <div style={{ position: 'absolute', right: -22, bottom: -26, opacity: .16 }}><ZMark size={132} /></div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.14em', color: 'rgba(255,255,255,.82)' }}>ZITCH WALLET</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10, fontWeight: 700, padding: '3px 9px', borderRadius: 999, background: 'rgba(255,255,255,.16)' }}><span style={{ width: 6, height: 6, borderRadius: 9, background: 'var(--z-cyan)' }} />Primary</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 6 }}>
          <span className="z-num" style={{ fontSize: 27, fontWeight: 800 }}>{app.showBal ? fmtN(app.balance) : '₦ ••••••'}<span style={{ fontSize: 15, opacity: .7 }}>{app.showBal ? '.00' : ''}</span></span>
          <Tap onClick={() => app.setShowBal(!app.showBal)}><I name={app.showBal ? 'eye' : 'eyeoff'} size={17} color="rgba(255,255,255,.85)" /></Tap>
        </div>
        <Tap onClick={() => { try { navigator.clipboard.writeText('9012345678'); } catch (e) { } app.toast('Account number copied'); }}>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, marginTop: 7, padding: '6px 11px', borderRadius: 999, background: 'rgba(255,255,255,.12)' }}>
            <I name="bank" size={14} color="rgba(255,255,255,.9)" />
            <span className="z-num" style={{ fontSize: 12.5, fontWeight: 600, color: 'rgba(255,255,255,.92)' }}>9012 345 678 · Providus</span>
            <I name="copy" size={12} color="rgba(255,255,255,.72)" />
          </div>
        </Tap>
        <div style={{ display: 'flex', gap: 10, marginTop: 11 }}>
          <Tap onClick={() => n.push('addmoney')} style={{ flex: 1 }}><div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, padding: '9px', borderRadius: 14, background: '#fff', color: 'var(--brand-deep)', fontWeight: 700, fontSize: 14 }}><I name="plus" size={16} color="var(--brand-deep)" stroke={2.4} />Add money</div></Tap>
          <Tap onClick={() => n.push('transfer')} style={{ flex: 1 }}><div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, padding: '9px', borderRadius: 14, background: 'rgba(255,255,255,.16)', color: '#fff', fontWeight: 700, fontSize: 14, border: '1px solid rgba(255,255,255,.28)' }}><I name="send" size={15} color="#fff" />Send</div></Tap>
        </div>
      </div>
      {/* connected accounts (Mono-linked banks) */}
      <window.ConnectedAccounts />
      {/* recent activity */}
      <div style={{ padding: '14px 18px 0' }}>
        <SectionLabel action="See all" onAction={() => n.push('history')}>Recent activity</SectionLabel>
        {app.txns.slice(0, 5).map((x, i) => <TxnRow key={x.id || i} x={x} divider={i > 0} onClick={() => n.push('txn', { x })} />)}
      </div>
    </TabRoot>;
  }

  // ---------- LOANS ----------
  function Loans() {
    const app = useApp(); const n = app.nav;
    return <TabRoot>
      <div style={{ padding: '6px 20px 2px', fontSize: 26, fontWeight: 800, color: 'var(--ink-1)' }}>Loans</div>
      <div style={{ margin: '14px 16px 0', borderRadius: 22, background: 'var(--hero-grad)', color: '#fff', padding: 20 }}>
        <div style={{ fontSize: 13, opacity: .85 }}>Available credit</div>
        <div className="z-num" style={{ fontSize: 32, fontWeight: 800, marginTop: 4 }}>₦500,000</div>
        <div style={{ height: 6, borderRadius: 4, background: 'rgba(255,255,255,.25)', marginTop: 14, overflow: 'hidden' }}><div style={{ width: '64%', height: '100%', background: '#fff' }} /></div>
        <div style={{ fontSize: 12, opacity: .85, marginTop: 8 }}>₦320,000 of ₦500,000 limit used</div>
      </div>
      <Card><PrimaryButton label="Get a new loan" icon="loan" onClick={() => n.push('loan')} /></Card>
      <div style={{ padding: '20px 18px 0' }}>
        <SectionLabel>Active loans</SectionLabel>
        <div style={{ borderRadius: 16, background: 'var(--surface)', boxShadow: 'var(--shadow-card)', padding: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div><div style={{ fontWeight: 700, color: 'var(--ink-1)' }}>Quick loan</div><div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 2 }}>Due Jun 26 · 30 days</div></div>
            <div className="z-num" style={{ fontSize: 18, fontWeight: 800, color: 'var(--ink-1)' }}>₦156,750</div>
          </div>
          <div style={{ marginTop: 14 }}><PrimaryButton label="Repay now" onClick={() => n.push('coming', { title: 'Repay loan', icon: 'loan', note: 'Repay ₦156,750 before Jun 26.' })} /></div>
        </div>
      </div>
    </TabRoot>;
  }

  // ---------- CARDS ----------
  function Cards() {
    const app = useApp(); const n = app.nav;
    const [freeze, setFreeze] = useState(false);
    return <TabRoot>
      <div style={{ padding: '6px 20px 2px', fontSize: 26, fontWeight: 800, color: 'var(--ink-1)' }}>Cards</div>
      <div style={{ margin: '16px 16px 0', borderRadius: 22, padding: '20px', height: 200, position: 'relative', overflow: 'hidden', background: freeze ? 'linear-gradient(120deg,#1B463C,#0B2A24)' : 'linear-gradient(120deg,#0C5249,#0FA295 70%,#5CF5EB)', boxShadow: '0 22px 46px -22px rgba(0,0,0,.6)', transition: 'background .3s' }}>
        <div style={{ position: 'absolute', right: -20, bottom: -30, opacity: .22 }}><ZMark size={160} /></div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div style={{ color: 'rgba(255,255,255,.9)', fontSize: 13, fontWeight: 700, letterSpacing: '.1em' }}>ZITCH</div>
          <I name="wallet" size={22} color="#fff" />
        </div>
        <div className="z-num" style={{ color: '#fff', fontSize: 21, letterSpacing: '.14em', marginTop: 46 }}>5061 •••• •••• 2043</div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 16, color: 'rgba(255,255,255,.9)', fontSize: 13 }}><span style={{ whiteSpace: 'nowrap' }}>WILLIAM A.</span><span className="z-num">08/27</span></div>
        {freeze && <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(5,32,28,.4)', color: '#fff', fontWeight: 700, fontSize: 14, letterSpacing: '.1em' }}>❄ FROZEN</div>}
      </div>
      <div style={{ display: 'flex', gap: 10, margin: '16px 16px 0' }}>
        {[['plus', 'Fund', '#16A34A', () => n.push('addmoney')], ['lock', freeze ? 'Unfreeze' : 'Freeze', '#2D7FF9', () => setFreeze(f => !f)], ['eye', 'Details', '#7A5CFF', () => n.push('coming', { title: 'Card details', icon: 'card', note: 'View full card number & CVV.' })]].map(([ic, lb, col, go]) => (
          <Tap key={lb} onClick={go} style={{ flex: 1 }}><div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, padding: '14px 6px', borderRadius: 16, background: 'var(--surface)', boxShadow: 'var(--shadow-card)' }}><div style={{ width: 38, height: 38, borderRadius: 11, display: 'flex', alignItems: 'center', justifyContent: 'center', background: col + '22' }}><I name={ic} size={20} color={col} /></div><span style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)' }}>{lb}</span></div></Tap>
        ))}
      </div>
      <Card><div style={{ display: 'flex', alignItems: 'center', gap: 12 }}><div style={{ width: 44, height: 44, borderRadius: 13, background: 'rgba(15,162,149,.14)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name="plus" size={22} color="var(--brand)" /></div><div style={{ flex: 1 }}><div style={{ fontWeight: 700, color: 'var(--ink-1)' }}>Create a virtual card</div><div style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>For online & USD payments</div></div><I name="right" size={20} color="var(--ink-3)" /></div></Card>
    </TabRoot>;
  }

  // ---------- ME ----------
  function Me() {
    const app = useApp(); const n = app.nav;
    const [enroll, setEnroll] = useState(false);
    const go = (title, icon, note) => () => n.push('coming', { title, icon, note });
    const Badge = ({ t, hot }) => <span style={{ fontSize: 10, fontWeight: 700, padding: '3px 8px', borderRadius: 999, color: '#fff', background: hot ? 'var(--z-red)' : 'var(--z-amber)' }}>{t}</span>;
    const grp1 = [
      ['user', 'Account Details', 'Name, email, phone & photo', null, () => n.push('accountdetails')],
      ['insurance', 'Identity Verification', 'BVN, NIN or selfie · raise limits', { t: 'Verify' }, () => n.push('kyc')],
      ['history', 'Transaction History', null, null, () => n.push('history')],
      ['chart', 'Account Limits', 'View your transaction limits', null, go('Account Limits', 'chart', 'Tier 3 · ₦5,000,000 daily.')],
      ['card', 'Bank Card / Account', 'Add a payment option', null, go('Bank Card / Account', 'card', 'Link your cards & bank accounts.')],
      ['bank', 'My BizPayment', 'Receive payment for business', null, go('My BizPayment', 'bank', 'Accept payments as a merchant.')],
      ['invite', 'Zitch Junior', 'Create an account for your child', { t: 'New' }, go('Zitch Junior', 'invite', 'A safe account for your child or ward.')],
      ['loan', 'Buy Now, Pay Later', 'Shop now, spread the cost', { t: 'Enjoy ₦0' }, go('Buy Now Pay Later', 'loan', 'Split payments over time, interest-free.')],
    ];
    const grp2 = [
      ['insurance', 'Security Center', 'Protect your funds', null, go('Security Center', 'insurance', 'PIN, biometrics, devices & alerts.')],
      ['help', 'Customer Service Center', null, null, go('Support', 'help', 'We reply in minutes, 24/7.')],
      ['gift', 'Invitation', 'Invite friends & earn up to ₦5,600', null, go('Invite & Earn', 'gift', 'Earn ₦500 per friend who joins Zitch.')],
      ['airtime', 'Zitch USSD', 'Bank without internet', null, go('Zitch USSD', 'airtime', 'Dial *xyz# to use Zitch offline.')],
    ];
    const Group = ({ items }) => (
      <Card style={{ padding: '2px 16px' }}>
        {items.map((r, i) => <ListRow key={i} icon={r[0]} iconColor="var(--brand)" title={r[1]} sub={r[2]} divider={i > 0}
          right={<div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>{r[3] && <Badge t={r[3].t} hot={r[3].t === 'New'} />}<I name="right" size={18} color="var(--ink-3)" /></div>} onClick={r[4]} />)}
      </Card>
    );
    return <TabRoot>
      {/* header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '6px 18px 0' }}>
        <Avatar size={50} ring="var(--brand)" />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--ink-1)' }}>Hi, William</div>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 5, marginTop: 4, padding: '3px 9px', borderRadius: 999, background: 'rgba(245,166,35,.16)', color: '#B27400', fontSize: 11.5, fontWeight: 700 }}><I name="check" size={11} color="#B27400" stroke={2.6} />Tier 3</div>
        </div>
        <Tap onClick={go('Settings', 'settings', 'Preferences, language & more.')}><div style={{ width: 40, height: 40, borderRadius: 13, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--surface)', boxShadow: 'var(--shadow-card)' }}><I name="settings" size={20} color="var(--ink-1)" /></div></Tap>
      </div>
      {/* balance */}
      <div style={{ padding: '12px 20px 0' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, color: 'var(--ink-3)', fontSize: 13 }}>Total balance<I name={app.showBal ? 'eye' : 'eyeoff'} size={15} color="var(--ink-3)" /></div>
        <div className="z-num" style={{ fontSize: 30, fontWeight: 800, color: 'var(--ink-1)', marginTop: 2 }}>{app.showBal ? fmtN(app.balance) : '₦ ••••••'}</div>
      </div>
      {/* Bank on WhatsApp */}
      <Tap onClick={() => n.push('linkwhatsapp')}>
        <div style={{ margin: '14px 16px 0', borderRadius: 16, padding: '12px 14px', background: 'var(--surface)', boxShadow: 'var(--shadow-card)', display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ width: 42, height: 42, borderRadius: '50%', background: '#25D366', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="#fff"><path d="M.057 24l1.687-6.163a11.867 11.867 0 0 1-1.587-5.946C.16 5.335 5.495 0 12.05 0a11.817 11.817 0 0 1 8.413 3.488 11.824 11.824 0 0 1 3.48 8.414c-.003 6.557-5.338 11.892-11.893 11.892a11.9 11.9 0 0 1-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884-.001 2.225.651 3.891 1.746 5.634l-.999 3.648 3.742-.981zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.768.967-.941 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z" /></svg>
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14.5, fontWeight: 700, color: 'var(--ink-1)' }}>Bank on WhatsApp</div>
            <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 1 }}>Balance, transfers &amp; bills in your chats</div>
          </div>
          <I name="right" size={18} color="var(--ink-3)" />
        </div>
      </Tap>
      {/* safety tips banner */}
      <Tap onClick={go('Safety Tips', 'insurance', '5 ways to keep your account secure.')}>
        <div style={{ margin: '12px 16px 0', borderRadius: 16, padding: '13px 16px', background: 'var(--hero-grad)', display: 'flex', alignItems: 'center', gap: 12 }}>
          <I name="insurance" size={22} color="#fff" />
          <div style={{ flex: 1 }}><div style={{ fontSize: 14, fontWeight: 700, color: '#fff' }}>5 Safety Tips</div><div style={{ fontSize: 12, color: 'rgba(255,255,255,.85)' }}>Make your account more secure</div></div>
          <div style={{ padding: '7px 16px', borderRadius: 999, background: '#fff', color: 'var(--brand-deep)', fontWeight: 700, fontSize: 12.5 }}>View</div>
        </div>
      </Tap>
      <Group items={grp1} />
      <Group items={grp2} />
      <Card style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px' }}>
        <div style={{ width: 40, height: 40, borderRadius: 12, background: 'rgba(15,162,149,.14)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name="fingerprint" size={20} color="var(--brand)" /></div>
        <div style={{ flex: 1 }}><div style={{ fontWeight: 600, color: 'var(--ink-1)' }}>Face ID / Fingerprint</div><div style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>Sign in &amp; approve payments</div></div>
        <Toggle on={app.biometrics} onChange={(v) => { if (v) { setEnroll(true); } else { app.setBiometrics(false); app.toast('Biometrics turned off'); } }} />
      </Card>
      <Card style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px' }}>
        <div style={{ width: 40, height: 40, borderRadius: 12, background: 'rgba(15,162,149,.14)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name={app.theme === 'dark' ? 'eye' : 'spark'} size={20} color="var(--brand)" /></div>
        <div style={{ flex: 1, fontWeight: 600, color: 'var(--ink-1)' }}>Dark mode</div>
        <Toggle on={app.theme === 'dark'} onChange={(v) => app.setTheme(v ? 'dark' : 'light')} />
      </Card>
      <div style={{ padding: '16px 16px 0' }}>
        <Tap onClick={() => app.nav.reset('splash')}><div style={{ textAlign: 'center', padding: '14px', borderRadius: 16, background: 'rgba(255,59,59,.1)', color: 'var(--z-red)', fontWeight: 700 }}>Log out</div></Tap>
      </div>
      {enroll && <BiometricScan title="Set up Face / Touch ID" subtitle="Scan to enrol your biometrics" faceMode onDone={() => { app.setBiometrics(true); setEnroll(false); app.toast('Biometrics enabled'); }} onClose={() => setEnroll(false)} />}
    </TabRoot>;
  }

  // ---------- HISTORY ----------
  function History() {
    const app = useApp();
    const [filter, setFilter] = useState('All');
    const FILTERS = {
      All: () => true,
      'Money in': (x) => x.amt > 0,
      'Money out': (x) => x.amt < 0,
      Airtime: (x) => x.cat === 'airtime' || x.cat === 'data',
      Bills: (x) => ['tv', 'electricity', 'betting', 'exams'].includes(x.cat),
      Transfers: (x) => x.cat === 'transfer' || x.cat === 'fund',
    };
    const list = app.txns.filter(FILTERS[filter]);
    return <Screen title="Transaction History" onBack={app.nav.pop}>
      <div style={{ display: 'flex', gap: 8, overflowX: 'auto', paddingBottom: 12, marginBottom: 4 }}>
        {Object.keys(FILTERS).map((f) => {
          const on = filter === f;
          return <Tap key={f} onClick={() => setFilter(f)}>
            <div style={{ padding: '8px 16px', borderRadius: 999, fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap', background: on ? 'var(--brand)' : 'var(--surface)', color: on ? '#fff' : 'var(--ink-2)', border: '1.5px solid ' + (on ? 'var(--brand)' : 'var(--line)'), transition: 'all .15s' }}>{f}</div>
          </Tap>;
        })}
      </div>
      {list.length ? list.map((x, i) => <TxnRow key={x.id || i} x={x} divider={i > 0} onClick={() => app.nav.push('txn', { x })} />)
        : <div style={{ textAlign: 'center', color: 'var(--ink-3)', fontSize: 14, padding: '48px 0' }}>No {filter.toLowerCase()} transactions yet</div>}
    </Screen>;
  }

  function TxnDetail({ x }) {
    const app = useApp(); const neg = x.amt < 0;
    return <Screen title="Transaction details" onBack={app.nav.pop}>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: 16 }}>
        <Monogram text={x.mono} color={x.col} size={64} r={20} />
        <div className="z-num" style={{ fontSize: 32, fontWeight: 800, color: neg ? 'var(--ink-1)' : 'var(--z-lime)', marginTop: 14 }}>{(neg ? '-' : '+') + fmtN(Math.abs(x.amt))}</div>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 8, padding: '5px 12px', borderRadius: 999, background: 'rgba(0,181,29,.12)', color: 'var(--z-lime)', fontSize: 12.5, fontWeight: 700 }}><I name="check" size={13} color="var(--z-lime)" />{x.status || 'Successful'}</div>
      </div>
      <div style={{ marginTop: 22, borderRadius: 18, background: 'var(--surface)', boxShadow: 'var(--shadow-card)', padding: '4px 16px 12px' }}>
        {[['Description', x.t], ['Date', x.time || 'Today'], ['Reference', 'ZTCH' + (1000000 + Math.floor(Math.random() * 8999999))], ['Channel', 'Zitch Wallet']].map((r, i) => <window.Row2 key={i} k={r[0]} v={r[1]} />)}
      </div>
      <div style={{ marginTop: 16 }}><PrimaryButton label="Share receipt" icon="share" onClick={() => { }} /></div>
    </Screen>;
  }

  function Notifications() {
    const app = useApp();
    const items = [
      ['gift', 'You earned ₦120 cashback', 'Bonus from airtime purchase', 'var(--z-lime)'],
      ['loan', 'Loan limit increased', 'Your new limit is ₦500,000', 'var(--brand)'],
      ['spark', 'Daily interest paid', '₦84.20 added to your wallet', 'var(--z-amber)'],
      ['bills', 'DSTV due in 2 days', 'Renew Compact Plus to avoid cut-off', 'var(--z-red)'],
    ];
    return <Screen title="Notifications" onBack={app.nav.pop}>
      {items.map((x, i) => <ListRow key={i} icon={x[0]} iconColor={x[3]} title={x[1]} sub={x[2]} divider={i > 0} />)}
    </Screen>;
  }

  function ComingSoon({ title, icon, note }) {
    const app = useApp();
    return <Screen title={title} onBack={app.nav.pop}>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', paddingTop: 70 }}>
        <div style={{ width: 88, height: 88, borderRadius: 28, background: 'rgba(15,162,149,.12)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name={icon || 'spark'} size={40} color="var(--brand)" /></div>
        <div style={{ fontSize: 20, fontWeight: 800, color: 'var(--ink-1)', marginTop: 22 }}>{title}</div>
        <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 8, maxWidth: 260 }}>{note}</div>
        <div style={{ marginTop: 14, padding: '7px 16px', borderRadius: 999, background: 'var(--surface-3)', fontSize: 12.5, fontWeight: 700, color: 'var(--ink-2)' }}>Fully designed in handoff →</div>
      </div>
    </Screen>;
  }

  Object.assign(window, { Home, Wallet, Loans, Cards, Me, History, TxnDetail, Notifications, ComingSoon, BottomNav });
})();
