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
        <div><ZMark size={104} badge glow /></div>
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
      { icon: 'bills', t: 'Pay every bill in seconds', d: 'Airtime, data, cable TV, electricity, betting & exam pins — all in one app.' },
      { icon: 'send', t: 'Send money instantly', d: 'Free transfers to Zitch and any Nigerian bank, with saved beneficiaries.' },
      { icon: 'loan', t: 'Borrow & grow your money', d: 'Instant loans up to ₦500,000 and Fixed Save earning 22% p.a.' },
    ];
    const s = slides[i]; const last = i === slides.length - 1;
    return <AuthShell>
      <SB />
      <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '0 20px' }}><Tap onClick={() => app.nav.replace('signin')}><span style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink-3)' }}>Skip</span></Tap></div>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: '0 32px' }}>
        <div key={i} style={{ width: 150, height: 150, borderRadius: 44, background: 'var(--hero-grad)', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 24px 50px -20px rgba(0,132,123,.6)' }}><I name={s.icon} size={64} color="#fff" /></div>
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
        <div style={{ marginTop: 14, marginBottom: 6 }}><ZMark size={48} /></div>
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
        <div style={{ textAlign: 'right', marginTop: -4 }}><span style={{ fontSize: 13, fontWeight: 600, color: 'var(--brand)' }}>Forgot password?</span></div>
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
    const valid = name.length > 2 && phone.length >= 10;
    return <AuthShell>
      <SB />
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 24px 0' }}>
        <Tap onClick={() => app.nav.replace('signin')}><div style={{ width: 40, height: 40, borderRadius: 13, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--surface)', boxShadow: 'var(--shadow-card)' }}><I name="left" size={20} color="var(--ink-1)" /></div></Tap>
        <div style={{ fontSize: 26, fontWeight: 800, color: 'var(--ink-1)', marginTop: 18 }}>Create your account</div>
        <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 6, marginBottom: 26 }}>Join 5,000,000+ Nigerians on Zitch</div>
        <Field label="Full name" value={name} onChange={setName} placeholder="William Adeyemi" prefix={<I name="user" size={18} color="var(--ink-3)" />} />
        <Field label="Phone number" type="number" value={phone} onChange={(v) => setPhone(v.replace(/\D/g, '').slice(0, 11))} placeholder="0801 234 5678" prefix={<I name="airtime" size={18} color="var(--ink-3)" />} />
        <Field label="Email (optional)" value={email} onChange={setEmail} placeholder="you@email.com" prefix={<I name="remita" size={18} color="var(--ink-3)" />} />
        <div style={{ fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.5, marginTop: 4 }}>By continuing you agree to Zitch's <span style={{ color: 'var(--brand)', fontWeight: 600 }}>Terms</span> &amp; <span style={{ color: 'var(--brand)', fontWeight: 600 }}>Privacy Policy</span>.</div>
      </div>
      <BottomBar><PrimaryButton label="Continue" disabled={!valid} onClick={() => app.nav.replace('otp')} /></BottomBar>
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
  function Otp() {
    const app = useApp();
    const [code, setCode] = useState('');
    useEffect(() => { if (code.length === 5) { const t = setTimeout(() => app.nav.replace('setpin'), 500); return () => clearTimeout(t); } }, [code]);
    return <AuthShell>
      <SB />
      <div style={{ flex: 1, padding: '12px 24px 0', display: 'flex', flexDirection: 'column' }}>
        <Tap onClick={() => app.nav.replace('register')}><div style={{ width: 40, height: 40, borderRadius: 13, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--surface)', boxShadow: 'var(--shadow-card)' }}><I name="left" size={20} color="var(--ink-1)" /></div></Tap>
        <div style={{ fontSize: 24, fontWeight: 800, color: 'var(--ink-1)', marginTop: 18 }}>Verify your number</div>
        <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 6 }}>Enter the 5-digit code sent to <span style={{ fontWeight: 700, color: 'var(--ink-1)' }}>0801 234 5678</span></div>
        <div style={{ display: 'flex', gap: 10, margin: '28px 0 18px' }}>
          {[0, 1, 2, 3, 4].map(k => <div key={k} className="z-num" style={{ flex: 1, height: 58, borderRadius: 14, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 24, fontWeight: 800, color: 'var(--ink-1)', background: 'var(--surface)', border: '2px solid ' + (code.length === k ? 'var(--brand)' : 'var(--line)') }}>{code[k] || ''}</div>)}
        </div>
        <div style={{ fontSize: 13.5, color: 'var(--ink-3)' }}>Didn't get it? <span style={{ color: 'var(--brand)', fontWeight: 700 }}>Resend in 0:24</span></div>
        <div style={{ flex: 1 }} />
        <div style={{ paddingBottom: 24 }}><Keypad onKey={(k) => setCode(c => k === 'del' ? c.slice(0, -1) : (c.length < 5 ? c + k : c))} /></div>
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
