// ui.jsx — Zitch prototype UI kit (sheets, pin pad, receipt, fields, provider grids)
(function () {
  const { useState, useEffect, useRef } = React;
  const I = (props) => React.createElement(window.ZIcon, props);
  if (!window.AppCtx) window.AppCtx = React.createContext(null);
  const useApp = () => React.useContext(window.AppCtx);

  const fmtN = (n) => '₦' + Math.round(n).toLocaleString('en-NG');
  const fmtK = (n) => '₦' + Number(n).toLocaleString('en-NG');

  // ---- Receipt export (image / PDF / share) — self-contained, no external libs ----
  function zDl(blob, name) { const u = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(u), 5000); }
  function zDataURLtoU8(d) { const b = atob(d.split(',')[1]); const u = new Uint8Array(b.length); for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return u; }
  function zRound(x, X, Y, w, h, r) { x.beginPath(); x.moveTo(X + r, Y); x.arcTo(X + w, Y, X + w, Y + h, r); x.arcTo(X + w, Y + h, X, Y + h, r); x.arcTo(X, Y + h, X, Y, r); x.arcTo(X, Y, X + w, Y, r); x.closePath(); }
  function zWrap(x, text, cx, cy, maxW, lh) { const words = String(text).split(' '); let line = '', yy = cy; for (let n = 0; n < words.length; n++) { const test = line + words[n] + ' '; if (x.measureText(test).width > maxW && n > 0) { x.fillText(line.trim(), cx, yy); line = words[n] + ' '; yy += lh; } else line = test; } x.fillText(line.trim(), cx, yy); return yy; }
  function zBadge(x, bx, by, r) {
    const g = x.createLinearGradient(bx - r, by - r, bx + r, by + r);
    g.addColorStop(0, '#0A3C54'); g.addColorStop(1, '#02283A');
    x.fillStyle = g; x.beginPath(); x.arc(bx, by, r, 0, 7); x.fill();
    const h = r * 0.5, t = Math.max(2, r * 0.3);
    const cg = x.createLinearGradient(bx, by - h, bx, by + h);
    cg.addColorStop(0, '#86FBF2'); cg.addColorStop(1, '#27D3C6');
    x.strokeStyle = cg; x.lineWidth = t; x.lineJoin = 'round'; x.lineCap = 'round';
    x.beginPath(); x.moveTo(bx - h, by - h); x.lineTo(bx + h, by - h); x.lineTo(bx - h, by + h); x.lineTo(bx + h, by + h); x.stroke();
  }
  function zReceiptCanvas({ title, message, rows, ref }) {
    const s = 2, W = 384 * s, m = 12 * s, cardW = W - 2 * m, cardR = 20 * s, padX = 30 * s, headH = 176 * s, rowH = 34 * s;
    const list = rows || [];
    const fullRows = list.concat([['Reference', ref || '—']]);
    const y0 = m + headH + 30 * s;
    const refBottom = y0 + fullRows.length * rowH;
    const H = refBottom + 92 * s;
    const c = document.createElement('canvas'); c.width = W; c.height = H;
    const x = c.getContext('2d'); const cx = W / 2;
    const pg = x.createLinearGradient(0, 0, 0, H); pg.addColorStop(0, '#E7F4F1'); pg.addColorStop(1, '#EFF7F5');
    x.fillStyle = pg; x.fillRect(0, 0, W, H);
    x.save(); zRound(x, m, m, cardW, H - 2 * m, cardR); x.fillStyle = '#FFFFFF'; x.shadowColor = 'rgba(6,55,49,.16)'; x.shadowBlur = 26 * s; x.shadowOffsetY = 10 * s; x.fill(); x.restore();
    x.save(); zRound(x, m, m, cardW, H - 2 * m, cardR); x.clip();
    x.translate(cx, (m + headH + refBottom) / 2); x.rotate(-24 * Math.PI / 180);
    x.font = '800 ' + (24 * s) + 'px Manrope, sans-serif'; x.fillStyle = 'rgba(15,162,149,0.05)'; x.textAlign = 'center'; x.textBaseline = 'middle';
    for (let gy = -H; gy < H; gy += 48 * s) { for (let gx = -W; gx < W; gx += 152 * s) x.fillText('ZITCH', gx, gy); }
    x.restore(); x.textBaseline = 'alphabetic';
    x.save(); zRound(x, m, m, cardW, headH, cardR); x.clip();
    const g = x.createLinearGradient(m, m, m + cardW, m + headH); g.addColorStop(0, '#0FA295'); g.addColorStop(1, '#00665E');
    x.fillStyle = g; x.fillRect(m, m, cardW, headH + 20 * s); x.restore();
    zBadge(x, cx, m + 34 * s, 18 * s);
    const cyk = m + 84 * s;
    x.fillStyle = 'rgba(255,255,255,.20)'; x.beginPath(); x.arc(cx, cyk, 26 * s, 0, 7); x.fill();
    x.fillStyle = '#00B51D'; x.beginPath(); x.arc(cx, cyk, 19 * s, 0, 7); x.fill();
    x.strokeStyle = '#fff'; x.lineWidth = 3.6 * s; x.lineCap = 'round'; x.lineJoin = 'round';
    x.beginPath(); x.moveTo(cx - 8 * s, cyk); x.lineTo(cx - 2 * s, cyk + 6 * s); x.lineTo(cx + 9 * s, cyk - 7 * s); x.stroke();
    x.textAlign = 'center'; x.fillStyle = '#fff'; x.font = '800 ' + (20 * s) + 'px Manrope, sans-serif';
    x.fillText(title || 'Successful', cx, m + 130 * s);
    x.font = '500 ' + (11 * s) + 'px Manrope, sans-serif'; x.fillStyle = 'rgba(255,255,255,.92)';
    zWrap(x, message || '', cx, m + 152 * s, cardW - 70 * s, 15 * s);
    let yy = y0;
    fullRows.forEach((r) => {
      x.textAlign = 'left'; x.fillStyle = '#6B7A77'; x.font = '500 ' + (11 * s) + 'px Manrope, sans-serif';
      x.fillText(String(r[0]), padX, yy);
      x.textAlign = 'right'; x.fillStyle = r[2] ? '#0FA295' : '#06231F'; x.font = (r[2] ? '800 ' : '600 ') + (12.5 * s) + 'px Manrope, sans-serif';
      x.fillText(String(r[1]), W - padX, yy);
      x.strokeStyle = '#EAF1EF'; x.lineWidth = 1; x.beginPath(); x.moveTo(padX, yy + 13 * s); x.lineTo(W - padX, yy + 13 * s); x.stroke();
      yy += rowH;
    });
    const fy = refBottom + 26 * s;
    zBadge(x, cx - 34 * s, fy - 4 * s, 9 * s);
    x.textAlign = 'left'; x.fillStyle = '#0FA295'; x.font = '800 ' + (15 * s) + 'px Manrope, sans-serif';
    x.fillText('Zitch', cx - 20 * s, fy + 1 * s);
    x.textAlign = 'center'; x.fillStyle = '#9AA8A5'; x.font = '500 ' + (9.5 * s) + 'px Manrope, sans-serif';
    x.fillText('Secured by Zitch · ' + new Date().toLocaleString(), cx, fy + 28 * s);
    return c;
  }
  window.ZReceiptCanvas = zReceiptCanvas;
  function zJpegToPdf(jpeg, w, h) {
    const parts = []; let len = 0; const offs = []; const enc = (str) => new TextEncoder().encode(str);
    const add = (str) => { const b = typeof str === 'string' ? enc(str) : str; parts.push(b); len += b.length; };
    add('%PDF-1.3\n');
    offs.push(len); add('1 0 obj\n<</Type/Catalog/Pages 2 0 R>>\nendobj\n');
    offs.push(len); add('2 0 obj\n<</Type/Pages/Kids[3 0 R]/Count 1>>\nendobj\n');
    offs.push(len); add('3 0 obj\n<</Type/Page/Parent 2 0 R/MediaBox[0 0 ' + w + ' ' + h + ']/Resources<</XObject<</Im0 5 0 R>>>>/Contents 4 0 R>>\nendobj\n');
    const content = 'q\n' + w + ' 0 0 ' + h + ' 0 0 cm\n/Im0 Do\nQ\n';
    offs.push(len); add('4 0 obj\n<</Length ' + content.length + '>>\nstream\n' + content + 'endstream\nendobj\n');
    offs.push(len); add('5 0 obj\n<</Type/XObject/Subtype/Image/Width ' + w + '/Height ' + h + '/ColorSpace/DeviceRGB/BitsPerComponent 8/Filter/DCTDecode/Length ' + jpeg.length + '>>\nstream\n');
    add(jpeg); add('\nendstream\nendobj\n');
    const xrefPos = len; let xref = 'xref\n0 6\n0000000000 65535 f \n';
    offs.forEach((o) => { xref += String(o).padStart(10, '0') + ' 00000 n \n'; });
    add(xref); add('trailer\n<</Size 6/Root 1 0 R>>\nstartxref\n' + xrefPos + '\n%%EOF');
    const out = new Uint8Array(len); let p = 0; parts.forEach((b) => { out.set(b, p); p += b.length; });
    return new Blob([out], { type: 'application/pdf' });
  }

  // press feedback wrapper
  function Tap({ onClick, children, style, className }) {
    const [d, setD] = useState(false);
    return (
      <div className={className}
        onPointerDown={() => setD(true)} onPointerUp={() => setD(false)} onPointerLeave={() => setD(false)}
        onClick={onClick}
        style={{ transition: 'transform .18s cubic-bezier(.34,1.56,.64,1), opacity .12s', transform: d ? 'perspective(150px) translateZ(-12px) scale(.96)' : 'none', opacity: d ? .92 : 1, cursor: 'pointer', ...style }}>
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
                  ? <div style={{ width: 42, height: 42, borderRadius: 11, overflow: 'hidden', flexShrink: 0, background: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      <img src={it.logo} alt={it.name} style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }} />
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
    const app = useApp();
    const [show, setShow] = useState(false);
    const [menu, setMenu] = useState(null);
    const [ref] = useState(() => 'ZTC' + Math.random().toString(36).slice(2, 10).toUpperCase());
    useEffect(() => { const t = setTimeout(() => setShow(true), 60); return () => clearTimeout(t); }, []);
    const fname = () => 'Zitch-Receipt-' + ref;
    const saveImage = () => { try { zReceiptCanvas({ title, message, rows, ref }).toBlob((b) => { zDl(b, fname() + '.png'); app && app.toast && app.toast('Receipt image saved'); }, 'image/png'); } catch (e) { app && app.toast && app.toast('Could not save receipt', 'error'); } };
    const savePdf = () => { try { const c = zReceiptCanvas({ title, message, rows, ref }); const jpeg = zDataURLtoU8(c.toDataURL('image/jpeg', 0.92)); zDl(zJpegToPdf(jpeg, c.width, c.height), fname() + '.pdf'); app && app.toast && app.toast('Receipt PDF saved'); } catch (e) { app && app.toast && app.toast('Could not save receipt', 'error'); } };
    const doShare = async (blob, filename) => { const file = new File([blob], filename, { type: blob.type }); if (navigator.canShare && navigator.canShare({ files: [file] })) { try { await navigator.share({ files: [file], title: title || 'Zitch receipt', text: message || '' }); return; } catch (e) { return; } } if (navigator.share) { try { await navigator.share({ title: title || 'Zitch receipt', text: (message || '') + ' · Ref ' + ref }); return; } catch (e) {} } zDl(blob, filename); app && app.toast && app.toast('Receipt ready to share'); };
    const shareImage = () => { try { zReceiptCanvas({ title, message, rows, ref }).toBlob((b) => doShare(b, fname() + '.png'), 'image/png'); } catch (e) {} };
    const sharePdf = () => { try { const c = zReceiptCanvas({ title, message, rows, ref }); const jpeg = zDataURLtoU8(c.toDataURL('image/jpeg', 0.92)); doShare(zJpegToPdf(jpeg, c.width, c.height), fname() + '.pdf'); } catch (e) {} };
    const copyRef = () => { try { navigator.clipboard.writeText(ref); } catch (e) {} app && app.toast && app.toast('Reference ' + ref + ' copied'); };
    return (
      <div className="z-screen" style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', background: 'var(--bg-grad)' }}>
        <div style={{ flex: 1, overflowY: 'auto', padding: '8px 22px 0' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', paddingTop: 44 }}>
            <div style={{ marginBottom: 16, opacity: show ? 1 : 0, transform: show ? 'none' : 'scale(.6)', transition: 'all .5s var(--ease-spring)' }}>{React.createElement(window.ZMark, { size: 46, badge: true, glow: true })}</div>
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
          <div style={{ marginTop: 28, borderRadius: 22, background: 'var(--surface)', boxShadow: 'var(--shadow-card)', padding: '6px 18px 16px', position: 'relative', overflow: 'hidden', opacity: show ? 1 : 0, transform: show ? 'none' : 'translateY(12px)', transition: 'all .45s .34s' }}>
            <div style={{ position: 'absolute', right: -16, bottom: -22, opacity: .05, pointerEvents: 'none' }}>{React.createElement(window.ZMark, { size: 132 })}</div>
            {rows.map((r, i) => <Row2 key={i} k={r[0]} v={r[1]} strong={r[2]} />)}
            <Row2 k="Reference" v={ref} />
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 16, opacity: show ? 1 : 0, transition: 'opacity .4s .42s' }}>
            {[['download', 'Save', () => setMenu('save')], ['share', 'Share', () => setMenu('share')], ['copy', 'Copy ref', copyRef]].map(([ic, lb, go]) => (
              <Tap key={ic} onClick={go} style={{ flex: 1 }}>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, padding: '14px 6px', borderRadius: 16, background: 'var(--surface)', border: '1.5px solid var(--line)' }}>
                  <I name={ic} size={20} color="var(--brand)" /><span style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)' }}>{lb}</span>
                </div>
              </Tap>
            ))}
          </div>
          {/* Bank on WhatsApp promo */}
          <Tap onClick={() => app && app.toast && app.toast('Opening WhatsApp banking…')}>
            <div style={{ marginTop: 14, marginBottom: 4, borderRadius: 16, padding: '11px 13px', background: 'linear-gradient(100deg,#0B7F6E 0%,#128C7E 48%,#1FAE5E 100%)', display: 'flex', alignItems: 'center', gap: 11, boxShadow: '0 12px 26px -16px rgba(18,140,126,.9)' }}>
              <div style={{ width: 38, height: 38, borderRadius: '50%', background: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                <svg width="22" height="22" viewBox="0 0 24 24" fill="#25D366"><path d="M.057 24l1.687-6.163a11.867 11.867 0 0 1-1.587-5.946C.16 5.335 5.495 0 12.05 0a11.817 11.817 0 0 1 8.413 3.488 11.824 11.824 0 0 1 3.48 8.414c-.003 6.557-5.338 11.892-11.893 11.892a11.9 11.9 0 0 1-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884-.001 2.225.651 3.891 1.746 5.634l-.999 3.648 3.742-.981zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.768.967-.941 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z"/></svg>
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13.5, fontWeight: 700, color: '#fff' }}>Bank on WhatsApp</div>
                <div style={{ fontSize: 11.5, color: 'rgba(255,255,255,.88)', marginTop: 1 }}>Balance, transfers &amp; bills — right in your chats</div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 3, padding: '6px 11px', borderRadius: 999, background: 'rgba(255,255,255,.18)', color: '#fff', fontSize: 12, fontWeight: 700, whiteSpace: 'nowrap', flexShrink: 0 }}>Chat<I name="right" size={14} color="#fff" /></div>
            </div>
          </Tap>
        </div>
        <BottomBar><PrimaryButton label="Back to Dashboard" onClick={onDone} /></BottomBar>
        {menu && <Sheet onClose={() => setMenu(null)}>{(close) => {
          const isShare = menu === 'share';
          const opts = isShare
            ? [['share', 'Share as image', 'PNG · best for chats & status', () => { shareImage(); close(); }], ['copy', 'Share as PDF', 'Document · best for records', () => { sharePdf(); close(); }]]
            : [['download', 'Save as image', 'PNG · best for chats & status', () => { saveImage(); close(); }], ['copy', 'Save as PDF', 'Document · best for records', () => { savePdf(); close(); }]];
          return (
            <div>
              <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--ink-1)', marginBottom: 3 }}>{isShare ? 'Share receipt' : 'Save receipt'}</div>
              <div style={{ fontSize: 13, color: 'var(--ink-3)', marginBottom: 14 }}>{isShare ? 'Choose a format to share.' : 'Download this receipt to your device.'}</div>
              {opts.map(([ic, t, sub, go]) => (
                <Tap key={t} onClick={go}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '13px 4px', borderBottom: '1px solid var(--line)' }}>
                    <div style={{ width: 42, height: 42, borderRadius: 12, background: 'rgba(15,162,149,.12)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><I name={ic} size={20} color="var(--brand)" /></div>
                    <div style={{ flex: 1, minWidth: 0 }}><div style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink-1)' }}>{t}</div><div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 1 }}>{sub}</div></div>
                    <I name="right" size={18} color="var(--ink-3)" />
                  </div>
                </Tap>
              ))}
            </div>
          );
        }}</Sheet>}
      </div>
    );
  }

  Object.assign(window, {
    ZUI: { fmtN, fmtK }, useApp, Tap, AppHeader, PrimaryButton, BottomBar, Field, Segmented, QuickAmounts, Monogram,
    ProviderGrid, PlanList, ListRow, Toggle, Sheet, ConfirmSheet, PinSheet, OptionSheet, BiometricScan, SuccessReceipt, Row2,
  });
})();
