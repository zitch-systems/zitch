// auth.jsx — splash, onboarding, sign-in, register, OTP, set-PIN, biometric
(function () {
  const { useState, useEffect } = React;
  const { useApp, Tap, PrimaryButton, BottomBar, Field, BiometricScan } = window;
  const I = (props) => React.createElement(window.ZIcon, props);
  const SB = () => React.createElement(window.StatusBar, null);
  const ZMark = (props) => React.createElement(window.ZMark, props);

  function AuthShell({ children, dark }) {
    return <div className={'z-screen ' + (dark ? 'z-dark' : '')} style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', background: dark ? 'linear-gradient(160deg,#0C4D47,#063A34 60%,#04221F)' : 'var(--bg-grad)' }}>{children}</div>;
  }

  // ---------- SPLASH ----------
  function Splash() {
    const app = useApp();
    useEffect(() => { const t = setTimeout(() => { let on = false; try { on = !!localStorage.getItem('z-onboarded'); } catch (e) { } app.nav.replace(on ? 'lock' : 'onboarding'); }, 1800); return () => clearTimeout(t); }, []);
    return <AuthShell dark>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 18 }}>
        <div style={{ perspective: 560, width: 150, height: 150, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ animation: 'zflip 1.9s cubic-bezier(.5,0,.5,1) infinite' }}><ZMark size={104} badge glow /></div>
        </div>
        <div style={{ fontSize: 30, fontWeight: 800, letterSpacing: '.18em', color: '#fff', marginTop: 4 }}>ZITCH</div>
        <div style={{ fontSize: 14, color: 'rgba(255,255,255,.7)' }}>Pay. Send. Grow.</div>
      </div>
      <div style={{ textAlign: 'center', paddingBottom: 40, color: 'rgba(255,255,255,.45)', fontSize: 12 }}>Secured by Zitch · NDIC insured</div>
    </AuthShell>;
  }

  // ---------- ONBOARDING ----------
  function Onboarding() {
    const app = useApp();
    const [i, setI] = useState(0);
    const slides = [
      { icon: 'send', t: 'Send money instantly', d: 'Free transfers to Zitch and any Nigerian bank, with saved beneficiaries.' },
      { wa: true, t: 'Bank on WhatsApp', d: 'Check your balance, send money and pay bills right inside your WhatsApp chats.' },
      { icon: 'more', t: 'Everything in one app', d: 'Airtime, data, bills, cards, savings & loans — all in one place.' },
    ];
    const s = slides[i]; const last = i === slides.length - 1;
    return <AuthShell>
      <SB />
      <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '0 20px' }}><Tap onClick={() => app.nav.replace('signin')}><span style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink-3)' }}>Skip</span></Tap></div>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: '0 32px' }}>
        <div key={i} style={{ width: 150, height: 150, borderRadius: 44, background: s.wa ? '#25D366' : 'var(--hero-grad)', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: s.wa ? '0 24px 50px -20px rgba(37,211,102,.6)' : '0 24px 50px -20px rgba(0,132,123,.6)' }}>{s.wa ? <svg width="68" height="68" viewBox="0 0 24 24" fill="#fff"><path d="M.057 24l1.687-6.163a11.867 11.867 0 0 1-1.587-5.946C.16 5.335 5.495 0 12.05 0a11.817 11.817 0 0 1 8.413 3.488 11.824 11.824 0 0 1 3.48 8.414c-.003 6.557-5.338 11.892-11.893 11.892a11.9 11.9 0 0 1-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884-.001 2.225.651 3.891 1.746 5.634l-.999 3.648 3.742-.981zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.768.967-.941 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z" /></svg> : <I name={s.icon} size={64} color="#fff" />}</div>
        <div key={'t' + i} style={{ fontSize: 24, fontWeight: 800, color: 'var(--ink-1)', marginTop: 38 }}>{s.t}</div>
        <div style={{ fontSize: 14.5, color: 'var(--ink-3)', marginTop: 12, lineHeight: 1.5, maxWidth: 300 }}>{s.d}</div>
      </div>
      <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginBottom: 24 }}>
        {slides.map((_, k) => <div key={k} style={{ width: k === i ? 24 : 8, height: 8, borderRadius: 999, background: k === i ? 'var(--brand)' : 'var(--line)', transition: 'all .3s' }} />)}
      </div>
      <div style={{ padding: '0 22px 30px' }}><PrimaryButton label={last ? 'Get Started' : 'Next'} onClick={() => last ? app.nav.replace('register') : setI(i + 1)} /></div>
      <div style={{ textAlign: 'center', paddingBottom: 26, marginTop: -14, fontSize: 14, color: 'var(--ink-3)' }}>Already have an account? <span onClick={() => app.nav.replace('signin')} style={{ fontWeight: 700, color: 'var(--brand)', cursor: 'pointer' }}>Sign in</span></div>
    </AuthShell>;
  }

  // ---------- SIGN IN ----------
  function SignIn() {
    const app = useApp();
    const [phone, setPhone] = useState('08145872210');
    const [pw, setPw] = useState('');
    const [bio, setBio] = useState(false);
    return <AuthShell>
      <SB />
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 24px 0' }}>
        <div style={{ marginTop: 14, marginBottom: 10 }}><ZMark size={56} badge glow /></div>
        <div style={{ fontSize: 26, fontWeight: 800, color: 'var(--ink-1)' }}>Welcome back</div>
        <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 6, marginBottom: 24 }}>Sign in to continue to Zitch</div>
        {/* instant biometric */}
        <Tap onClick={() => setBio(true)}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '14px 16px', borderRadius: 16, background: 'var(--hero-grad)', marginBottom: 18, boxShadow: '0 14px 30px -16px rgba(0,132,123,.7)' }}>
            <div style={{ width: 46, height: 46, borderRadius: 14, background: 'rgba(255,255,255,.2)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><I name="faceid" size={26} color="#fff" /></div>
            <div style={{ flex: 1 }}><div style={{ fontSize: 15, fontWeight: 700, color: '#fff' }}>Instant sign in</div><div style={{ fontSize: 12.5, color: 'rgba(255,255,255,.85)' }}>Use Face ID or fingerprint</div></div>
            <I name="fingerprint" size={24} color="#fff" />
          </div>
        </Tap>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '0 0 18px', color: 'var(--ink-3)' }}><div style={{ flex: 1, height: 1, background: 'var(--line)' }} /><span style={{ fontSize: 12, fontWeight: 600 }}>or use password</span><div style={{ flex: 1, height: 1, background: 'var(--line)' }} /></div>
        <Field label="Phone number" type="number" value={phone} onChange={(v) => setPhone(v.replace(/\D/g, '').slice(0, 11))} prefix={<I name="user" size={18} color="var(--ink-3)" />} />
        <Field label="Password" type="password" value={pw} onChange={setPw} placeholder="Enter password" prefix={<I name="lock" size={18} color="var(--ink-3)" />} />
        <div style={{ textAlign: 'right', marginTop: -4 }}><Tap onClick={() => app.toast('Enter your email or phone — a reset link is on the way')}><span style={{ fontSize: 13, fontWeight: 600, color: 'var(--brand)' }}>Forgot password?</span></Tap></div>
      </div>
      <BottomBar>
        <PrimaryButton label="Sign in" onClick={() => app.enterApp()} />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 16, fontSize: 14, color: 'var(--ink-3)' }}>New to Zitch?<Tap onClick={() => app.nav.replace('register')}><span style={{ fontWeight: 700, color: 'var(--brand)' }}>Create account</span></Tap></div>
      </BottomBar>
      {bio && <BiometricScan title="Welcome back, William" subtitle="Sign in with biometrics" faceMode onDone={() => { app.setBiometrics(true); app.enterApp(); }} onFallback={() => setBio(false)} onClose={() => setBio(false)} />}
    </AuthShell>;
  }

  // ---------- REGISTER ----------
  function Register() {
    const app = useApp();
    const [name, setName] = useState('');
    const [phone, setPhone] = useState('');
    const [email, setEmail] = useState('');
    const [busy, setBusy] = useState(false);
    const phoneOk = /^0\d{10}$/.test(phone);
    const emailOk = !email || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
    const [pw, setPw] = useState('');
    const [pw2, setPw2] = useState('');
    const pwRules = [
      { k: 'len', label: '8+ characters', ok: pw.length >= 8 },
      { k: 'upper', label: '1 uppercase', ok: /[A-Z]/.test(pw) },
      { k: 'num', label: '1 number', ok: /\d/.test(pw) },
    ];
    const pwOk = pwRules.every(r => r.ok);
    const matchOk = pw2.length > 0 && pw2 === pw;
    const valid = name.trim().length > 2 && phoneOk && emailOk && pwOk && matchOk;
    const submit = () => { if (!valid || busy) return; setBusy(true); setTimeout(() => app.nav.replace('otp', { phone }), 900); };
    return <AuthShell>
      <SB />
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 24px 0' }}>
        <Tap onClick={() => app.nav.replace('signin')}><div style={{ width: 40, height: 40, borderRadius: 13, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--surface)', boxShadow: 'var(--shadow-card)' }}><I name="left" size={20} color="var(--ink-1)" /></div></Tap>
        <div style={{ fontSize: 26, fontWeight: 800, color: 'var(--ink-1)', marginTop: 18 }}>Create your account</div>
        <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 6, marginBottom: 26 }}>Join 5,000,000+ Nigerians on Zitch</div>
        <Field label="Full name" value={name} onChange={setName} placeholder="William Adeyemi" prefix={<I name="user" size={18} color="var(--ink-3)" />} />
        <Field label="Phone number" type="number" value={phone} onChange={(v) => setPhone(v.replace(/\D/g, '').slice(0, 11))} placeholder="0801 234 5678" prefix={<I name="airtime" size={18} color="var(--ink-3)" />} />
        {phone.length > 0 && !phoneOk && <div style={{ fontSize: 12, color: 'var(--z-red)', margin: '-8px 2px 10px' }}>Enter a valid 11-digit number (e.g. 0801 234 5678)</div>}
        <Field label="Email (optional)" value={email} onChange={setEmail} placeholder="you@email.com" prefix={<I name="remita" size={18} color="var(--ink-3)" />} />
        {email.length > 0 && !emailOk && <div style={{ fontSize: 12, color: 'var(--z-red)', margin: '-8px 2px 10px' }}>Enter a valid email address</div>}
        <Field label="Create password" type="password" value={pw} onChange={setPw} placeholder="Create a strong password" prefix={<I name="lock" size={18} color="var(--ink-3)" />} />
        {pw.length > 0 && <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', margin: '-6px 2px 12px' }}>
          {pwRules.map(r => <div key={r.k} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11.5, fontWeight: 600, color: r.ok ? 'var(--z-lime)' : 'var(--ink-3)' }}><I name={r.ok ? 'check' : 'x'} size={12} color={r.ok ? 'var(--z-lime)' : 'var(--ink-3)'} stroke={2.6} />{r.label}</div>)}
        </div>}
        <Field label="Confirm password" type="password" value={pw2} onChange={setPw2} placeholder="Re-enter your password" prefix={<I name="lock" size={18} color="var(--ink-3)" />} />
        {pw2.length > 0 && !matchOk && <div style={{ fontSize: 12, color: 'var(--z-red)', margin: '-8px 2px 10px' }}>Passwords do not match</div>}
        {matchOk && <div style={{ fontSize: 12, color: 'var(--z-lime)', fontWeight: 600, margin: '-8px 2px 10px', display: 'flex', alignItems: 'center', gap: 5 }}><I name="check" size={12} color="var(--z-lime)" stroke={2.6} />Passwords match</div>}
        <div style={{ fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.5, marginTop: 4 }}>By continuing you agree to Zitch's <span style={{ color: 'var(--brand)', fontWeight: 600 }}>Terms</span> &amp; <span style={{ color: 'var(--brand)', fontWeight: 600 }}>Privacy Policy</span>.</div>
      </div>
      <BottomBar><PrimaryButton label={busy ? 'Sending code…' : 'Continue'} disabled={!valid} onClick={submit} /></BottomBar>
    </AuthShell>;
  }

  // numeric keypad used by OTP & SetPin
  function Keypad({ onKey }) {
    const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', 'del'];
    return <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 8 }}>
      {keys.map((k, i) => <Tap key={i} onClick={() => k !== '' && onKey(k)}>
        <div style={{ height: 58, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 25, fontWeight: 600, color: 'var(--ink-1)', borderRadius: 14, background: k === '' ? 'transparent' : 'var(--surface)' }}>{k === 'del' ? <I name="left" size={22} color="var(--ink-2)" /> : k}</div>
      </Tap>)}
    </div>;
  }

  // ---------- OTP ----------
  function Otp({ phone }) {
    const app = useApp();
    const [code, setCode] = useState('');
    const [secs, setSecs] = useState(24);
    const fmtPhone = (p) => (p && /^0\d{10}$/.test(p)) ? p.replace(/(\d{4})(\d{3})(\d{4})/, '$1 $2 $3') : (p || '0801 234 5678');
    useEffect(() => { if (secs <= 0) return; const t = setTimeout(() => setSecs((s) => s - 1), 1000); return () => clearTimeout(t); }, [secs]);
    useEffect(() => { if (code.length === 6) { const t = setTimeout(() => app.nav.replace('setpin'), 600); return () => clearTimeout(t); } }, [code]);
    return <AuthShell>
      <SB />
      <div style={{ flex: 1, padding: '12px 24px 0', display: 'flex', flexDirection: 'column' }}>
        <Tap onClick={() => app.nav.replace('register')}><div style={{ width: 40, height: 40, borderRadius: 13, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--surface)', boxShadow: 'var(--shadow-card)' }}><I name="left" size={20} color="var(--ink-1)" /></div></Tap>
        <div style={{ fontSize: 24, fontWeight: 800, color: 'var(--ink-1)', marginTop: 18 }}>Verify your number</div>
        <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 6 }}>Enter the 6-digit code sent to <span style={{ fontWeight: 700, color: 'var(--ink-1)' }}>{fmtPhone(phone)}</span></div>
        <div style={{ display: 'flex', gap: 8, margin: '28px 0 18px' }}>
          {[0, 1, 2, 3, 4, 5].map(k => <div key={k} className="z-num" style={{ flex: 1, height: 56, borderRadius: 13, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 23, fontWeight: 800, color: 'var(--ink-1)', background: 'var(--surface)', border: '2px solid ' + (code.length === k ? 'var(--brand)' : 'var(--line)') }}>{code[k] || ''}</div>)}
        </div>
        {code.length === 6
          ? <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13.5, fontWeight: 700, color: 'var(--brand)' }}><I name="refresh" size={14} color="var(--brand)" style={{ animation: 'zspin .8s linear infinite' }} />Verifying your code…</div>
          : (secs > 0
            ? <div style={{ fontSize: 13.5, color: 'var(--ink-3)' }}>Didn't get it? Resend in <span style={{ fontWeight: 700, color: 'var(--ink-1)' }}>0:{String(secs).padStart(2, '0')}</span></div>
            : <Tap onClick={() => { setSecs(24); app.toast('A new code has been sent'); }}><span style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--brand)' }}>Resend code</span></Tap>)}
        <div style={{ flex: 1 }} />
        <div style={{ paddingBottom: 24 }}><Keypad onKey={(k) => setCode(c => k === 'del' ? c.slice(0, -1) : (c.length < 6 ? c + k : c))} /></div>
      </div>
    </AuthShell>;
  }

  // ---------- SET PIN ----------
  function SetPin() {
    const app = useApp();
    const [pin, setPin] = useState('');
    const [confirm, setConfirm] = useState(null);
    const [err, setErr] = useState(false);
    const active = confirm === null ? pin : confirm;
    useEffect(() => {
      if (confirm === null && pin.length === 4) { setTimeout(() => { setConfirm(''); }, 200); }
      if (confirm !== null && confirm.length === 4) {
        if (confirm === pin) setTimeout(() => app.nav.replace('biometric'), 250);
        else { setErr(true); setTimeout(() => { setErr(false); setConfirm(''); }, 700); }
      }
    }, [pin, confirm]);
    return <AuthShell>
      <SB />
      <div style={{ flex: 1, padding: '12px 24px 0', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
        <div style={{ marginTop: 26 }}><ZMark size={44} /></div>
        <div style={{ fontSize: 22, fontWeight: 800, color: 'var(--ink-1)', marginTop: 20 }}>{confirm === null ? 'Create a 4-digit PIN' : 'Confirm your PIN'}</div>
        <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 6, textAlign: 'center' }}>{err ? <span style={{ color: 'var(--z-red)', fontWeight: 700 }}>PINs don't match, try again</span> : 'You\'ll use this to authorize payments'}</div>
        <div style={{ display: 'flex', gap: 18, margin: '30px 0' }}>
          {[0, 1, 2, 3].map(k => <div key={k} style={{ width: 18, height: 18, borderRadius: 10, transition: 'all .15s', background: active.length > k ? (err ? 'var(--z-red)' : 'var(--brand)') : 'transparent', border: '2px solid ' + (active.length > k ? (err ? 'var(--z-red)' : 'var(--brand)') : 'var(--line)') }} />)}
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ width: '100%', paddingBottom: 24 }}><Keypad onKey={(k) => { if (confirm === null) setPin(p => k === 'del' ? p.slice(0, -1) : (p.length < 4 ? p + k : p)); else setConfirm(c => k === 'del' ? c.slice(0, -1) : (c.length < 4 ? c + k : c)); }} /></div>
      </div>
    </AuthShell>;
  }

  // ---------- BIOMETRIC ----------
  function Biometric() {
    const app = useApp();
    const [enroll, setEnroll] = useState(false);
    return <AuthShell>
      <SB />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: '0 32px' }}>
        <div style={{ width: 110, height: 110, borderRadius: '50%', background: 'rgba(15,162,149,.12)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="var(--brand)" strokeWidth="1.6" strokeLinecap="round"><path d="M12 11c0 3 0 5-1 7M7.5 8.5A5 5 0 0 1 17 11c0 4 0 6 .5 7.5M5 11a7 7 0 0 1 11-5.7M9.5 14c0 2.5 0 3.5-.5 5M12 14.5c0 2.5-.3 4-.8 5.3M15 12c0 4 .2 5.5.7 7" /></svg>
        </div>
        <div style={{ fontSize: 23, fontWeight: 800, color: 'var(--ink-1)', marginTop: 30 }}>Enable Face / Touch ID</div>
        <div style={{ fontSize: 14.5, color: 'var(--ink-3)', marginTop: 10, lineHeight: 1.5 }}>Sign in and approve payments faster and more securely with biometrics.</div>
      </div>
      <BottomBar>
        <PrimaryButton label="Enable biometrics" icon="check" onClick={() => setEnroll(true)} />
        <div style={{ textAlign: 'center', marginTop: 14 }}><Tap onClick={() => { app.setBiometrics(false); app.enterApp(); }}><span style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink-3)' }}>Maybe later</span></Tap></div>
      </BottomBar>
      {enroll && <BiometricScan title="Set up Face / Touch ID" subtitle="Scan to enrol your biometrics" faceMode onDone={() => { app.setBiometrics(true); app.enterApp(); }} onClose={() => setEnroll(false)} />}
    </AuthShell>;
  }

  // ---------- LOCK (returning user — auto biometric) ----------
  function Lock() {
    const app = useApp();
    const [bio, setBio] = useState(true);
    useEffect(() => { if (!app.biometrics) app.nav.replace('signin'); }, []);
    return <AuthShell>
      <SB />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: '0 32px', gap: 16 }}>
        <ZMark size={64} badge />
        <div>
          <div style={{ fontSize: 22, fontWeight: 800, color: 'var(--ink-1)' }}>Welcome back, William</div>
          <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 6 }}>Unlock with Face ID or fingerprint</div>
        </div>
        <Tap onClick={() => setBio(true)}><div style={{ width: 76, height: 76, borderRadius: '50%', background: 'rgba(15,162,149,.12)', display: 'flex', alignItems: 'center', justifyContent: 'center', marginTop: 8 }}><I name="fingerprint" size={38} color="var(--brand)" /></div></Tap>
      </div>
      <BottomBar>
        <div style={{ textAlign: 'center' }}><Tap onClick={() => app.nav.replace('signin')}><span style={{ fontSize: 14, fontWeight: 700, color: 'var(--brand)' }}>Use password instead</span></Tap></div>
      </BottomBar>
      {bio && <BiometricScan title="Welcome back, William" subtitle="Unlock Zitch with biometrics" faceMode onDone={() => app.enterApp()} onFallback={() => { setBio(false); app.nav.replace('signin'); }} onClose={() => setBio(false)} />}
    </AuthShell>;
  }

  Object.assign(window, { Splash, Onboarding, SignIn, Register, Otp, SetPin, Biometric, Lock });
})();
