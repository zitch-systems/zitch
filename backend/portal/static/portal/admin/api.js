// Zitch Admin — live API layer over /api/ops/ (token in localStorage; every
// call is POST JSON with a Bearer header, mirroring the app's auth scheme).
window.ZAPI = (function () {
  const D = window.ZADM;
  let token = localStorage.getItem('zops_token') || '';
  let me = JSON.parse(localStorage.getItem('zops_me') || 'null');

  async function call(path, body) {
    const res = await fetch('/api/ops/' + path + '/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) { logout(); throw new Error(data.message || 'Session expired — sign in again'); }
    if (!res.ok) throw new Error(data.message || ('Request failed (' + res.status + ')'));
    return data;
  }

  async function login(identifier, password) {
    const data = await call('login', { identifier, password });
    token = data.token;
    me = { role: data.role, caps: data.caps, name: data.name, email: data.email };
    localStorage.setItem('zops_token', token);
    localStorage.setItem('zops_me', JSON.stringify(me));
    return me;
  }
  function logout() {
    token = ''; me = null;
    localStorage.removeItem('zops_token');
    localStorage.removeItem('zops_me');
  }

  // ---- section loaders: fetch -> mutate ZADM in place -------------------- //
  const load = {
    summary: async () => {
      const s = await call('summary');
      D.SUMMARY = s; D.VOLUME_14D = s.volume_14d; D.PROVIDERS = s.providers;
    },
    users: async (q) => { const r = await call('users', { q }); D.USERS = r.rows; D.USERS_TOTAL = r.total; },
    txns: async (q, type) => { D.TXNS = (await call('transactions', { q, type })).rows; },
    inbox: async () => { D.CONVOS = (await call('inbox')).rows; },
    broadcasts: async () => { const r = await call('broadcasts'); D.BROADCASTS = r.rows; D.BC_META = { opted_in: r.opted_in, linked: r.linked }; },
    audit: async (q) => { D.AUDIT = (await call('audit', { q })).rows; },
    fx: async () => { D.FX = await call('fx'); },
    products: async () => {
      const r = await call('products');
      D.LOANS = r.loans; D.SAVINGS = r.savings; D.CARDS = r.cards; D.MATURED_DUE = r.matured_due;
    },
    kyc: async () => { D.KYCQ = (await call('kyc-queue')).rows; },
    recon: async () => { const r = await call('recon'); D.RECON = r; D.PROVIDERS = r.providers; },
    ai: async () => { D.AI = await call('ai'); },
    settings: async () => {
      const r = await call('settings');
      D.SETTINGS = r.settings; D.TEAM = r.team; D.PERMS = r.perms; D.ROLES = r.roles;
    },
  };

  // which loaders feed which nav view (portal.jsx refreshes on view switch)
  const VIEW_LOADERS = {
    overview: ['summary'], users: ['users'], kyc: ['kyc'], txns: ['txns'],
    fx: ['fx'], products: ['products'], wa: ['inbox'], broadcasts: ['broadcasts'],
    ai: ['ai'], recon: ['recon'], audit: ['audit'], settings: ['settings'],
  };
  async function loadView(view, ...args) {
    await Promise.all((VIEW_LOADERS[view] || []).map((k) => load[k](...args)));
  }

  // ---- actions (every one audit-logged server-side) ---------------------- //
  const actions = {
    userAction: (user_id, action) => call('user-action', { user_id, action }),
    kycReview: (user_id, approve) => call('kyc-review', { user_id, approve }),
    txnRequery: (reference) => call('txn-requery', { reference }),
    fxMargin: (bps) => call('fx-margin', { bps }),
    fxCorridor: (currency, enabled) => call('fx-corridor', { currency, enabled }),
    cardAction: (card_id) => call('card-action', { card_id }),
    loanRemind: (reference) => call('loan-remind', { reference }),
    runMaturities: () => call('run-maturities'),
    runRecon: () => call('run-recon'),
    thread: (msisdn) => call('thread', { msisdn }),
    convAi: (msisdn, enabled) => call('conv-ai', { msisdn, enabled }),
    aiGlobal: (enabled) => call('ai-global', { enabled }),
    // the three conversation actions live on the WhatsApp app's ops routes
    handover: (msisdn) => opsCall('handover', { msisdn }),
    returnBot: (msisdn) => opsCall('return-to-bot', { msisdn }),
    reply: (msisdn, text) => opsCall('reply', { msisdn, text }),
    broadcast: (template_name, category) => opsCall('broadcast', { template_name, category }),
  };
  async function opsCall(path, body) {
    const res = await fetch('/api/whatsapp/ops/' + path + '/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.message || 'Request failed');
    return data;
  }

  return { call, login, logout, load, loadView, ...actions, get me() { return me; }, get token() { return token; } };
})();
