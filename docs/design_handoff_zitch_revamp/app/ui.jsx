// ui.jsx — Zitch prototype UI kit (sheets, pin pad, receipt, fields, provider grids)
(function () {
  const { useState, useEffect, useRef } = React;
  const I = (props) => React.createElement(window.ZIcon, props);
  if (!window.AppCtx) window.AppCtx = React.createContext(null);
  const useApp = () => React.useContext(window.AppCtx);

  const fmtN = (n) => '₦' + Math.round(n).toLocaleString('en-NG');
  const fmtK = (n) => '₦' + Number(n).toLocaleString('en-NG');

  // press feedback wrapper
  function Tap({ onClick, children, style, className }) {
    const [d, setD] = useState(false);
    return (
      <div className={className}
        onPointerDown={() => setD(true)} onPointerUp={() => setD(false)} onPointerLeave={() => setD(false)}
        onClick={onClick}
        style={{ transition: 'transform .12s, opacity .12s', transform: d ? 'scale(.97)' : 'none', opacity: d ? .85 : 1, cursor: 'pointer', ...style }}>
        {children}
      </div>
    );
  }

  function AppHeader({ title, sub, onBack, right }) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '6px 18px 10px' }}>
        {onBack && (
          <Tap onClick={onBack} style={{ width: 40, height: 40, borderRadius: 13, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--surface)', boxShadow: 'var(--shadow-card)', flexShrink: 0 }}>
            <I name="left" size={20} color="var(--ink-1)" />
          </Tap>
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink-1)' }}>{title}</div>
          {sub && <div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 1 }}>{sub}</div>}
        </div>
        {right}
      </div>
    );
  }

  function PrimaryButton({ label, onClick, disabled, icon }) {
    return (
      <Tap onClick={disabled ? null : onClick}
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, height: 56, borderRadius: 18,
          background: disabled ? 'var(--surface-3)' : 'var(--brand)', color: disabled ? 'var(--ink-3)' : '#fff',
          fontSize: 16, fontWeight: 700, boxShadow: disabled ? 'none' : '0 12px 26px -12px rgba(0,132,123,.8)' }}>
        {icon && <I name={icon} size={19} color={disabled ? 'var(--ink-3)' : '#fff'} />} {label}
      </Tap>
    );
  }

  // fixed bottom action holder inside a screen
  function BottomBar({ children }) {
    const app = useApp();
    return <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, padding: '12px 18px 26px', background: 'linear-gradient(transparent, var(--bg) 30%)' }}><div style={{ maxWidth: app && app.wide ? 564 : 'none', margin: '0 auto' }}>{children}</div></div>;
  }

  function Field({ label, value, placeholder, onChange, prefix, suffix, type, mono, onClick, readOnly }) {
    return (
      <div style={{ marginBottom: 14 }}>
        {label && <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 7 }}>{label}</div>}
        <div onClick={onClick} style={{ display: 'flex', alignItems: 'center', gap: 8, height: 50, padding: '0 15px', borderRadius: 13, background: 'var(--surface)', border: '1.5px solid var(--line)', cursor: onClick ? 'pointer' : 'text' }}>
          {prefix}
          <input value={value} placeholder={placeholder} readOnly={readOnly || !onChange} type={type || 'text'} inputMode={type === 'number' ? 'numeric' : undefined}
            onChange={onChange ? (e) => onChange(e.target.value) : undefined}
            className={mono ? 'z-num' : ''}
            style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontFamily: 'var(--font)', fontSize: 16, fontWeight: 600, color: 'var(--ink-1)', minWidth: 0 }} />
          {suffix}
        </div>
      </div>
    );
  }

  function Segmented({ options, value, onChange }) {
    return (
      <div style={{ display: 'flex', gap: 4, padding: 4, background: 'var(--surface-3)', borderRadius: 14, marginBottom: 18 }}>
        {options.map((o) => (
          <Tap key={o.v} onClick={() => onChange(o.v)} style={{ flex: 1 }}>
            <div style={{ textAlign: 'center', padding: '10px', borderRadius: 11, fontSize: 14, fontWeight: 700,
              background: value === o.v ? 'var(--surface)' : 'transparent', color: value === o.v ? 'var(--brand)' : 'var(--ink-3)',
              boxShadow: value === o.v ? 'var(--shadow-card)' : 'none', transition: 'all .15s' }}>{o.label}</div>
          </Tap>
        ))}
      </div>
    );
  }

  function QuickAmounts({ amounts, value, onPick }) {
    return (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, marginBottom: 16 }}>
        {amounts.map((a) => {
          const on = String(value) === String(a);
          return (
            <Tap key={a} onClick={() => onPick(a)}>
              <div className="z-num" style={{ textAlign: 'center', padding: '13px 6px', borderRadius: 13, fontSize: 15, fontWeight: 700,
                background: on ? 'var(--brand)' : 'var(--surface)', color: on ? '#fff' : 'var(--ink-1)',
                border: '1.5px solid ' + (on ? 'var(--brand)' : 'var(--line)') }}>{fmtK(a)}</div>
            </Tap>
          );
        })}
      </div>
    );
  }

  function Monogram({ text, color, size = 44, r = 14 }) {
    return <div style={{ width: size, height: size, borderRadius: r, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: color + '22', color: color, fontWeight: 800, fontSize: size * .32, letterSpacing: '.01em' }}>{text}</div>;
  }

  // provider/network selector grid
  function ProviderGrid({ items, value, onPick, cols = 4 }) {
    return (
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols},1fr)`, gap: 10, marginBottom: 18 }}>
        {items.map((it) => {
          const on = value === it.id;
          const initials = it.name.replace(/[^A-Za-z0-9 ]/g, '').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
          return (
            <Tap key={it.id} onClick={() => onPick(it.id)}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 7, padding: '12px 4px', borderRadius: 16,
                background: 'var(--surface)', border: '2px solid ' + (on ? 'var(--brand)' : 'var(--line)'), position: 'relative' }}>
                {it.logo
                  ? <div style={{ width: 42, height: 42, borderRadius: 11, background: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden', border: '1px solid #ECECEC', flexShrink: 0 }}>
                      <img src={it.logo} alt={it.name} style={{ maxWidth: '88%', maxHeight: '88%', objectFit: 'contain' }} />
                    </div>
                  : <div style={{ width: 42, height: 42, borderRadius: 12, background: it.color, color: it.fg || '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 800, fontSize: 14 }}>{initials}</div>}
                <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-2)', textAlign: 'center', lineHeight: 1.15 }}>{it.name}</span>
                {on && <span style={{ position: 'absolute', top: 6, right: 6, width: 16, height: 16, borderRadius: 9, background: 'var(--brand)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name="check" size={10} color="#fff" stroke={3} /></span>}
              </div>
            </Tap>
          );
        })}
      </div>
    );
  }

  // list of plans (data / cable)
  function PlanList({ plans, value, onPick }) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {plans.map((p) => {
          const on = value === p.id;
          return (
            <Tap key={p.id} onClick={() => onPick(p.id)}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '14px 16px', borderRadius: 15, background: 'var(--surface)', border: '2px solid ' + (on ? 'var(--brand)' : 'var(--line)') }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink-1)' }}>{p.label}</div>
                  <div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 2 }}>{p.sub}</div>
                </div>
                <div className="z-num" style={{ fontSize: 15, fontWeight: 700, color: on ? 'var(--brand)' : 'var(--ink-1)' }}>{fmtK(p.price)}</div>
              </div>
            </Tap>
          );
        })}
      </div>
    );
  }

  function ListRow({ icon, iconColor, mono, monoColor, title, sub, right, onClick, divider }) {
    return (
      <Tap onClick={onClick}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 0', borderTop: divider ? '1px solid var(--line)' : 'none' }}>
          {mono != null ? <Monogram text={mono} color={monoColor || 'var(--brand)'} /> :
            icon ? <div style={{ width: 44, height: 44, borderRadius: 13, background: (iconColor || 'var(--brand)') + '1f', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><I name={icon} size={21} color={iconColor || 'var(--brand)'} /></div> : null}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 15.5, fontWeight: 600, color: 'var(--ink-1)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{title}</div>
            {sub && <div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 2 }}>{sub}</div>}
          </div>
          {right}
        </div>
      </Tap>
    );
  }

  function Toggle({ on, onChange }) {
    return (
      <div onClick={() => onChange(!on)} style={{ width: 46, height: 28, borderRadius: 999, padding: 3, background: on ? 'var(--brand)' : 'var(--surface-3)', cursor: 'pointer', transition: 'background .2s' }}>
        <div style={{ width: 22, height: 22, borderRadius: 999, background: '#fff', transform: on ? 'translateX(18px)' : 'none', transition: 'transform .2s var(--ease-spring)', boxShadow: '0 1px 3px rgba(0,0,0,.2)' }} />
      </div>
    );
  }

  // ---------- Bottom Sheet ----------
  function Sheet({ children, onClose, pad = true }) {
    const [vis, setVis] = useState(false);
    useEffect(() => { const t = setTimeout(() => setVis(true), 20); return () => clearTimeout(t); }, []);
    const close = () => { setVis(false); setTimeout(onClose, 270); };
    return (
      <div onClick={close} style={{ position: 'absolute', inset: 0, zIndex: 60, display: 'flex', alignItems: 'flex-end',
        background: vis ? 'rgba(2,20,17,.5)' : 'rgba(2,20,17,0)', transition: 'background .27s', backdropFilter: vis ? 'blur(2px)' : 'none' }}>
        <div onClick={(e) => e.stopPropagation()} style={{ width: '100%', maxHeight: '90%', overflowY: 'auto', background: 'var(--surface)', borderRadius: '28px 28px 0 0',
          transform: vis ? 'translateY(0)' : 'translateY(100%)', transition: 'transform .3s var(--ease-spring)', padding: pad ? '10px 18px 26px' : 0, boxShadow: '0 -10px 40px rgba(0,0,0,.25)' }}>
          <div style={{ width: 40, height: 5, borderRadius: 3, background: 'var(--line)', margin: '0 auto 14px' }} />
          {typeof children === 'function' ? children(close) : children}
        </div>
      </div>
    );
  }

  function Row2({ k, v, strong }) {
    return (
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '11px 0', borderTop: '1px solid var(--line)' }}>
        <span style={{ fontSize: 14, color: 'var(--ink-3)' }}>{k}</span>
        <span className="z-num" style={{ fontSize: strong ? 16 : 14, fontWeight: strong ? 800 : 600, color: 'var(--ink-1)' }}>{v}</span>
      </div>
    );
  }

  // ---------- Confirm sheet (OPay-aligned) ----------
  function ConfirmSheet({ title, rows, total, onPay, onClose }) {
    const app = useApp();
    return (
      <Sheet onClose={onClose}>{(close) => (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 4 }}>
            <Tap onClick={close} style={{ marginLeft: -6 }}><div style={{ width: 36, height: 36, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name="x" size={20} color="var(--ink-2)" /></div></Tap>
          </div>
          <div style={{ textAlign: 'center', marginBottom: 18 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink-3)' }}>{title}</div>
            <div className="z-num" style={{ fontSize: 34, fontWeight: 800, color: 'var(--ink-1)', marginTop: 5 }}>{fmtN(total)}</div>
          </div>
          <div style={{ marginBottom: 20 }}>{rows.map((r, i) => <Row2 key={i} k={r[0]} v={r[1]} strong={r[2]} />)}</div>
          {/* sender / payment method */}
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink-1)', marginBottom: 10 }}>Pay with</div>
          <div style={{ borderRadius: 14, background: 'var(--surface-2)', border: '1.5px solid var(--line)', padding: '14px 16px', marginBottom: 18 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div style={{ width: 38, height: 38, borderRadius: 11, background: 'rgba(15,162,149,.14)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><I name="wallet" size={20} color="var(--brand)" /></div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink-1)' }}>Zitch Wallet</div>
                <div className="z-num" style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>William A. · 9012 345 678</div>
              </div>
              <I name="check" size={18} color="var(--brand)" />
            </div>
            <div style={{ borderTop: '1px dashed var(--line)', margin: '12px 0' }} />
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 13.5, color: 'var(--ink-3)' }}>Available balance</span>
              <span className="z-num" style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink-1)' }}>{fmtN(app.balance)}</span>
            </div>
          </div>
          <PrimaryButton label={'Pay ' + fmtN(total)} icon="lock" onClick={() => { close(); setTimeout(onPay, 280); }} />
          <div style={{ height: 8 }} />
        </div>
      )}</Sheet>
    );
  }

  // ---------- PIN sheet ----------
  function PinSheet({ amount, onDone, onClose, onBio }) {
    const [pin, setPin] = useState('');
    const [state, setState] = useState('input'); // input | loading
    const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', 'del'];
    const press = (k) => {
      if (state !== 'input') return;
      if (k === 'del') return setPin(p => p.slice(0, -1));
      if (k === '' || pin.length >= 4) return;
      const np = pin + k; setPin(np);
      if (np.length === 4) { setState('loading'); setTimeout(() => onDone(), 1400); }
    };
    return (
      <Sheet onClose={onClose}>{(close) => (
        <div style={{ textAlign: 'center' }}>
          <div style={{ width: 52, height: 52, borderRadius: 16, margin: '0 auto 12px', background: 'var(--brand)1f', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,162,149,.14)' }}>
            <I name="lock" size={24} color="var(--brand)" />
          </div>
          <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--ink-1)' }}>Enter your PIN</div>
          <div style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>{state === 'loading' ? 'Authorizing payment…' : 'Confirm payment of ' + fmtN(amount)}</div>
          <div style={{ display: 'flex', justifyContent: 'center', gap: 16, margin: '22px 0' }}>
            {[0, 1, 2, 3].map(i => (
              <div key={i} style={{ width: 16, height: 16, borderRadius: 9, transition: 'all .2s',
                background: state === 'loading' ? 'var(--brand)' : (pin.length > i ? 'var(--brand)' : 'transparent'),
                border: '2px solid ' + (pin.length > i ? 'var(--brand)' : 'var(--line)'),
                animation: state === 'loading' ? `zpulse 1s ${i * .12}s infinite` : 'none' }} />
            ))}
          </div>
          {state === 'loading' ? <div style={{ height: 232 }} /> : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 6 }}>
              {keys.map((k, i) => (
                <Tap key={i} onClick={() => k === 'del' ? press(k) : (k === '' ? (onBio && onBio()) : press(k))}>
                  <div style={{ height: 56, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 24, fontWeight: 600, color: 'var(--ink-1)', borderRadius: 14 }}>
                    {k === 'del' ? <I name="left" size={22} color="var(--ink-2)" /> : (k === '' ? (onBio ? <I name="fingerprint" size={28} color="var(--brand)" /> : null) : k)}
                  </div>
                </Tap>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 6, color: 'var(--ink-3)', fontSize: 12.5 }}>
            <I name="lock" size={13} color="var(--ink-3)" /> Secured by Zitch
          </div>
        </div>
      )}</Sheet>
    );
  }

  // ---------- Generic option-picker sheet ----------
  function OptionSheet({ title, items, onPick, onClose, renderItem }) {
    return (
      <Sheet onClose={onClose}>{(close) => (
        <div>
          <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--ink-1)', marginBottom: 6 }}>{title}</div>
          <div>{items.map((it, i) => (
            <div key={it.id || i} onClick={() => { onPick(it); close(); }}>{renderItem(it, i)}</div>
          ))}</div>
        </div>
      )}</Sheet>
    );
  }

  // ---------- Biometric scan (Face ID / fingerprint) ----------
  function BiometricScan({ title, subtitle, faceMode, onDone, onFallback, onClose }) {
    const [state, setState] = useState('scan'); // scan | done
    const run = () => { if (state !== 'scan') return; setState('done'); setTimeout(onDone, 600); };
    useEffect(() => { const t = setTimeout(run, 1500); return () => clearTimeout(t); }, []);
    const done = state === 'done';
    const accent = done ? 'var(--z-lime)' : 'var(--brand)';
    return (
      <Sheet onClose={onClose}>{(close) => (
        <div style={{ textAlign: 'center', paddingBottom: 6 }}>
          <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--ink-1)' }}>{title || 'Confirm with biometrics'}</div>
          {subtitle && <div style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>{subtitle}</div>}
          <div onClick={run} style={{ position: 'relative', width: 132, height: 132, margin: '24px auto 18px', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
            <div style={{ position: 'absolute', inset: 0, borderRadius: '50%', border: '2px solid ' + accent, opacity: .25, animation: state === 'scan' ? 'zpulse 1.4s infinite' : 'none' }} />
            <div style={{ position: 'absolute', inset: 14, borderRadius: '50%', background: (done ? 'rgba(0,181,29,.14)' : 'rgba(15,162,149,.12)'), transition: 'background .3s' }} />
            <div style={{ position: 'relative', transition: 'transform .3s var(--ease-spring)', transform: done ? 'scale(1)' : 'scale(1)' }}>
              {done
                ? <div style={{ width: 64, height: 64, borderRadius: '50%', background: 'var(--z-lime)', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 12px 26px -8px rgba(0,181,29,.6)' }}><I name="check" size={34} color="#fff" stroke={3} /></div>
                : <I name={faceMode ? 'faceid' : 'fingerprint'} size={64} color={accent} stroke={1.6} />}
            </div>
          </div>
          <div style={{ fontSize: 14, fontWeight: 600, color: done ? 'var(--z-lime)' : 'var(--ink-2)' }}>
            {done ? 'Approved' : (faceMode ? 'Scanning your face…' : 'Touch the sensor to authenticate')}
          </div>
          {onFallback && !done && (
            <div onClick={onFallback} style={{ marginTop: 22, fontSize: 13.5, fontWeight: 700, color: 'var(--brand)', cursor: 'pointer' }}>Use PIN instead</div>
          )}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 16, color: 'var(--ink-3)', fontSize: 12 }}>
            <I name="lock" size={12} color="var(--ink-3)" /> Secured by Zitch
          </div>
        </div>
      )}</Sheet>
    );
  }

  // ---------- Success receipt (full screen) ----------
  function SuccessReceipt({ title, message, rows, onDone }) {
    const [show, setShow] = useState(false);
    useEffect(() => { const t = setTimeout(() => setShow(true), 60); return () => clearTimeout(t); }, []);
    return (
      <div className="z-screen" style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', background: 'var(--bg-grad)' }}>
        <div style={{ flex: 1, overflowY: 'auto', padding: '8px 22px 0' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', paddingTop: 56 }}>
            <div style={{ position: 'relative', width: 110, height: 110, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <div style={{ position: 'absolute', inset: 0, borderRadius: '50%', background: 'rgba(0,181,29,.14)', transform: show ? 'scale(1)' : 'scale(.4)', opacity: show ? 1 : 0, transition: 'all .5s var(--ease-spring)' }} />
              <div style={{ width: 78, height: 78, borderRadius: '50%', background: 'var(--z-lime)', display: 'flex', alignItems: 'center', justifyContent: 'center', transform: show ? 'scale(1)' : 'scale(.2)', transition: 'transform .55s .08s var(--ease-spring)', boxShadow: '0 16px 30px -10px rgba(0,181,29,.6)' }}>
                <I name="check" size={40} color="#fff" stroke={3} />
              </div>
            </div>
            <div style={{ fontSize: 24, fontWeight: 800, color: 'var(--ink-1)', marginTop: 22, opacity: show ? 1 : 0, transform: show ? 'none' : 'translateY(8px)', transition: 'all .4s .2s' }}>{title}</div>
            <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 8, maxWidth: 280, opacity: show ? 1 : 0, transition: 'opacity .4s .28s' }}>{message}</div>
          </div>
          {/* receipt card */}
          <div style={{ marginTop: 28, borderRadius: 22, background: 'var(--surface)', boxShadow: 'var(--shadow-card)', padding: '6px 18px 16px', opacity: show ? 1 : 0, transform: show ? 'none' : 'translateY(12px)', transition: 'all .45s .34s' }}>
            {rows.map((r, i) => <Row2 key={i} k={r[0]} v={r[1]} strong={r[2]} />)}
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 16, opacity: show ? 1 : 0, transition: 'opacity .4s .42s' }}>
            {[['download', 'Save'], ['share', 'Share'], ['copy', 'Copy ref']].map(([ic, lb]) => (
              <div key={ic} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, padding: '14px 6px', borderRadius: 16, background: 'var(--surface)', border: '1.5px solid var(--line)' }}>
                <I name={ic} size={20} color="var(--brand)" /><span style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)' }}>{lb}</span>
              </div>
            ))}
          </div>
        </div>
        <BottomBar><PrimaryButton label="Back to Dashboard" onClick={onDone} /></BottomBar>
      </div>
    );
  }

  Object.assign(window, {
    ZUI: { fmtN, fmtK }, useApp, Tap, AppHeader, PrimaryButton, BottomBar, Field, Segmented, QuickAmounts, Monogram,
    ProviderGrid, PlanList, ListRow, Toggle, Sheet, ConfirmSheet, PinSheet, OptionSheet, BiometricScan, SuccessReceipt, Row2,
  });
})();
