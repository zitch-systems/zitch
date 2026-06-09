// Zitch Admin — mock data layer (mirrors backend models: WhatsAppLink, ConversationState,
// WaMessageLog, Broadcast, AuditLog, SystemSetting, wallets/forex, transfers, utility)
window.ZADM = (function () {
  const now = Date.now();
  const ago = (mins) => new Date(now - mins * 60000);

  const USERS = [
    { id: 'u_1001', name: 'Adaeze Okonkwo', phone: '0803 221 4421', email: 'adaeze.o@gmail.com', kyc: 'face', tier: 3, status: 'active', joined: 'Jan 2025', wa: 'active', aiEnabled: true, marketingOptIn: true, wallets: { NGN: 482350.75, USD: 120.00, GBP: 85.50, CAD: 0 } },
    { id: 'u_1002', name: 'John Adeyemi', phone: '0812 998 0034', email: 'jadeyemi@yahoo.com', kyc: 'nin', tier: 2, status: 'active', joined: 'Mar 2025', wa: 'active', aiEnabled: true, marketingOptIn: false, wallets: { NGN: 1240500.00, USD: 0, GBP: 0, CAD: 310.20 } },
    { id: 'u_1003', name: 'Kemi Balogun', phone: '0705 443 8810', email: 'kemib@gmail.com', kyc: 'bvn', tier: 1, status: 'active', joined: 'Apr 2025', wa: 'none', aiEnabled: false, marketingOptIn: false, wallets: { NGN: 56200.00, USD: 0, GBP: 0, CAD: 0 } },
    { id: 'u_1004', name: 'Tunde Eze', phone: '0901 777 2203', email: 'tunde.eze@outlook.com', kyc: 'face', tier: 3, status: 'frozen', joined: 'Feb 2025', wa: 'active', aiEnabled: false, marketingOptIn: true, wallets: { NGN: 9800450.00, USD: 2400.00, GBP: 0, CAD: 0 } },
    { id: 'u_1005', name: 'Ngozi Umeh', phone: '0816 220 9987', email: 'ngoziu@gmail.com', kyc: 'pending', tier: 0, status: 'active', joined: 'Jun 2026', wa: 'pending', aiEnabled: true, marketingOptIn: false, wallets: { NGN: 2500.00, USD: 0, GBP: 0, CAD: 0 } },
    { id: 'u_1006', name: 'Ibrahim Musa', phone: '0809 114 7765', email: 'imusa@gmail.com', kyc: 'nin', tier: 2, status: 'active', joined: 'May 2025', wa: 'active', aiEnabled: true, marketingOptIn: true, wallets: { NGN: 310875.40, USD: 45.00, GBP: 0, CAD: 0 } },
    { id: 'u_1007', name: 'Chiamaka Obi', phone: '0703 555 1290', email: 'chiamaka.obi@gmail.com', kyc: 'bvn', tier: 1, status: 'active', joined: 'Aug 2025', wa: 'none', aiEnabled: true, marketingOptIn: false, wallets: { NGN: 88410.00, USD: 0, GBP: 12.00, CAD: 0 } },
    { id: 'u_1008', name: 'Seun Afolabi', phone: '0818 002 6614', email: 'seunafo@gmail.com', kyc: 'face', tier: 3, status: 'active', joined: 'Jan 2026', wa: 'active', aiEnabled: true, marketingOptIn: true, wallets: { NGN: 2105320.10, USD: 800.00, GBP: 240.00, CAD: 95.00 } },
  ];

  const TXNS = [
    { id: 'ZTC-88341', user: 'Adaeze Okonkwo', type: 'transfer', channel: 'whatsapp', desc: 'To John Adeyemi · GTBank ····1234', amt: -5000, cur: 'NGN', fee: 10, status: 'success', time: ago(12) },
    { id: 'ZTC-88340', user: 'Seun Afolabi', type: 'fx', channel: 'app', desc: 'NGN → USD · $336.65 @ ₦1,485.20', amt: -500000, cur: 'NGN', fee: 0, status: 'success', time: ago(26) },
    { id: 'ZTC-88339', user: 'Ibrahim Musa', type: 'airtime', channel: 'whatsapp', desc: 'MTN · 0809 114 7765', amt: -1000, cur: 'NGN', fee: 0, status: 'success', time: ago(31) },
    { id: 'ZTC-88338', user: 'John Adeyemi', type: 'fund', channel: 'app', desc: 'Monnify checkout', amt: 250000, cur: 'NGN', fee: 0, status: 'success', time: ago(58) },
    { id: 'ZTC-88337', user: 'Kemi Balogun', type: 'electricity', channel: 'app', desc: 'IKEDC prepaid · 0414 ···· 882', amt: -10000, cur: 'NGN', fee: 100, status: 'pending', time: ago(64) },
    { id: 'ZTC-88336', user: 'Tunde Eze', type: 'transfer', channel: 'app', desc: 'To Zenith ····0099', amt: -1200000, cur: 'NGN', fee: 25, status: 'flagged', time: ago(95) },
    { id: 'ZTC-88335', user: 'Chiamaka Obi', type: 'data', channel: 'whatsapp', desc: 'Airtel 2GB · 30 days', amt: -1500, cur: 'NGN', fee: 0, status: 'success', time: ago(130) },
    { id: 'ZTC-88334', user: 'Seun Afolabi', type: 'cable', channel: 'app', desc: 'DStv Compact · IUC 7023…', amt: -19000, cur: 'NGN', fee: 0, status: 'success', time: ago(170) },
    { id: 'ZTC-88333', user: 'Adaeze Okonkwo', type: 'fx', channel: 'whatsapp', desc: 'NGN → GBP · £85.50 @ ₦1,872.45', amt: -160095, cur: 'NGN', fee: 0, status: 'success', time: ago(220) },
    { id: 'ZTC-88332', user: 'Ngozi Umeh', type: 'fund', channel: 'app', desc: 'Monnify checkout', amt: 2500, cur: 'NGN', fee: 0, status: 'success', time: ago(300) },
    { id: 'ZTC-88331', user: 'Ibrahim Musa', type: 'transfer', channel: 'app', desc: 'To UBA ····5521', amt: -45000, cur: 'NGN', fee: 10, status: 'failed', time: ago(340) },
    { id: 'ZTC-88330', user: 'John Adeyemi', type: 'fx', channel: 'app', desc: 'NGN → CAD · C$310.20 @ ₦1,082.10', amt: -335667, cur: 'NGN', fee: 0, status: 'success', time: ago(400) },
  ];

  const CONVOS = [
    {
      msisdn: '+234 803 221 4421', user: 'Adaeze Okonkwo', status: 'bot', aiEnabled: true, agent: null, last: ago(2),
      msgs: [
        { dir: 'in', text: 'send money to my brother john, 5k', t: ago(14), intent: { intent: 'transfer', recipient: 'john', amount: 5000, confidence: 0.96 } },
        { dir: 'out', text: 'Got it 👍 John Adeyemi · GTBank ····1234 · ₦5,000 + ₦10 fee. Reply with your PIN to confirm, or "cancel".', t: ago(14) },
        { dir: 'in', text: '••••  (PIN masked)', t: ago(13) },
        { dir: 'out', text: '✅ ₦5,000 sent to John Adeyemi. New balance ₦477,350.75. Receipt ZTC-88341.', t: ago(12) },
        { dir: 'in', text: 'balance', t: ago(3), intent: { intent: 'balance', confidence: 0.99 } },
        { dir: 'out', text: '💼 NGN ₦477,350.75 · USD $120.00 · GBP £85.50', t: ago(2) },
      ],
    },
    {
      msisdn: '+234 901 777 2203', user: 'Tunde Eze', status: 'human', aiEnabled: false, agent: 'Funmi (support)', last: ago(8),
      msgs: [
        { dir: 'in', text: 'why is my account frozen?? I need to pay my supplier today', t: ago(45), intent: { intent: 'support', topic: 'account_frozen', confidence: 0.91 } },
        { dir: 'out', text: 'Your account is under a routine review. An agent will join this chat shortly.', t: ago(44) },
        { dir: 'out', text: '[Agent · Funmi] Hi Tunde — I can see the review on your account. We need one more document: a utility bill dated within 3 months. You can reply with a photo here.', t: ago(10), agent: true },
        { dir: 'in', text: 'ok sending it now', t: ago(8) },
      ],
    },
    {
      msisdn: '+234 818 002 6614', user: 'Seun Afolabi', status: 'bot', aiEnabled: true, agent: null, last: ago(20),
      msgs: [
        { dir: 'in', text: 'convert 500k to dollars', t: ago(22), intent: { intent: 'fx_convert', from: 'NGN', to: 'USD', amount: 500000, confidence: 0.95 } },
        { dir: 'out', text: '💱 Quote: ₦500,000 → $336.65 @ ₦1,485.20/$. Valid 60s. Reply with your PIN to confirm.', t: ago(22) },
        { dir: 'in', text: '••••  (PIN masked)', t: ago(21) },
        { dir: 'out', text: '✅ Converted. USD wallet credited $336.65. Receipt ZTC-88340.', t: ago(20) },
      ],
    },
    {
      msisdn: '+234 809 114 7765', user: 'Ibrahim Musa', status: 'bot', aiEnabled: true, agent: null, last: ago(31),
      msgs: [
        { dir: 'in', text: 'airtime 1k', t: ago(33), intent: { intent: 'airtime', amount: 1000, confidence: 0.93 } },
        { dir: 'out', text: 'MTN ₦1,000 to 0809 114 7765 (your linked number)? Reply with your PIN to confirm.', t: ago(33) },
        { dir: 'in', text: '••••  (PIN masked)', t: ago(32) },
        { dir: 'out', text: '✅ Airtime delivered. Receipt ZTC-88339.', t: ago(31) },
      ],
    },
    {
      msisdn: '+234 705 110 3344', user: '(unlinked)', status: 'paused', aiEnabled: false, agent: null, last: ago(120),
      msgs: [
        { dir: 'in', text: 'hello can i borrow 50k', t: ago(122), intent: { intent: 'unknown', confidence: 0.41 } },
        { dir: 'out', text: 'This number isn\'t linked to a Zitch account yet. Open the app → Settings → WhatsApp, then send LINK <code> here.', t: ago(121) },
        { dir: 'in', text: 'abeg just send am', t: ago(120), flagged: true },
      ],
    },
  ];

  const BROADCASTS = [
    { id: 'bc_31', template: 'fx_rate_drop_alert', category: 'utility', status: 'done', created: 'Jun 6, 2026', by: 'amara@zitch.com', queued: 3120, sent: 3120, delivered: 3017, read: 2410, failed: 103 },
    { id: 'bc_30', template: 'june_cashback_promo', category: 'marketing', status: 'done', created: 'Jun 2, 2026', by: 'amara@zitch.com', queued: 1874, sent: 1874, delivered: 1798, read: 1322, failed: 76 },
    { id: 'bc_29', template: 'maintenance_window', category: 'utility', status: 'done', created: 'May 28, 2026', by: 'dapo@zitch.com', queued: 3098, sent: 3098, delivered: 3001, read: 2876, failed: 97 },
    { id: 'bc_28', template: 'savings_rate_update', category: 'marketing', status: 'draft', created: 'May 25, 2026', by: 'amara@zitch.com', queued: 0, sent: 0, delivered: 0, read: 0, failed: 0 },
  ];

  const AUDIT = [
    { actor: 'funmi@zitch.com', role: 'support', action: 'wa.handover', target: '+234 901 777 2203', before: { status: 'bot', ai: true }, after: { status: 'human', ai: false }, t: ago(46) },
    { actor: 'funmi@zitch.com', role: 'support', action: 'wa.agent_reply', target: '+234 901 777 2203', before: {}, after: { chars: 182 }, t: ago(10) },
    { actor: 'dapo@zitch.com', role: 'finance', action: 'fx.margin_update', target: 'fx_margin_bps', before: { bps: 75 }, after: { bps: 60 }, t: ago(540) },
    { actor: 'amara@zitch.com', role: 'super_admin', action: 'broadcast.send', target: 'fx_rate_drop_alert', before: {}, after: { queued: 3120, category: 'utility' }, t: ago(4300) },
    { actor: 'system', role: 'system', action: 'txn.auto_flag', target: 'ZTC-88336', before: { status: 'pending' }, after: { status: 'flagged', rule: 'velocity>1m/24h' }, t: ago(95) },
    { actor: 'dapo@zitch.com', role: 'finance', action: 'user.freeze', target: 'u_1004 (Tunde Eze)', before: { status: 'active' }, after: { status: 'frozen' }, t: ago(2880) },
  ];

  const RATES = [
    { pair: 'NGN/USD', flag: '🇺🇸', provider: 1474.10, margin: 60, customer: 1485.20, settle: true, vol24: 41200000 },
    { pair: 'NGN/GBP', flag: '🇬🇧', provider: 1858.40, margin: 60, customer: 1872.45, settle: true, vol24: 18750000 },
    { pair: 'NGN/CAD', flag: '🇨🇦', provider: 1074.00, margin: 60, customer: 1082.10, settle: true, vol24: 6300000 },
    { pair: 'NGN/CNY', flag: '🇨🇳', provider: 205.30, margin: 60, customer: 206.84, settle: false, vol24: 0 },
  ];

  const FLOAT = [
    { cur: 'NGN', sym: '₦', bal: 184250300.22, provider: 'Monnify' },
    { cur: 'USD', sym: '$', bal: 92410.55, provider: 'Fincra' },
    { cur: 'GBP', sym: '£', bal: 31206.10, provider: 'Fincra' },
    { cur: 'CAD', sym: 'C$', bal: 12880.00, provider: 'Fincra' },
  ];

  const PROVIDERS = [
    { name: 'Monnify', role: 'Funding & payouts', status: 'operational', uptime: '99.98%' },
    { name: 'Baxi', role: 'Airtime · data · bills', status: 'operational', uptime: '99.91%' },
    { name: 'Fincra', role: 'FX rates & settlement', status: 'operational', uptime: '99.95%' },
    { name: 'Meta WhatsApp', role: 'Chat channel', status: 'degraded', uptime: '98.72%' },
    { name: 'Sendchamp', role: 'SMS / OTP', status: 'operational', uptime: '99.99%' },
    { name: 'Prembly', role: 'KYC (BVN · NIN · face)', status: 'operational', uptime: '99.87%' },
  ];

  const VOLUME_14D = [38, 44, 41, 52, 49, 61, 58, 47, 66, 72, 64, 78, 81, 74]; // ₦m/day

  const LOANS = [
    { id: 'ln_204', user: 'Ibrahim Musa', amt: 150000, tenor: '3 months', rate: '4.5%/mo', status: 'requested', score: 712, due: '—', outstanding: 0 },
    { id: 'ln_203', user: 'Chiamaka Obi', amt: 50000, tenor: '1 month', rate: '5%/mo', status: 'active', score: 688, due: 'Jul 2, 2026', outstanding: 52500 },
    { id: 'ln_202', user: 'John Adeyemi', amt: 200000, tenor: '6 months', rate: '4%/mo', status: 'overdue', score: 645, due: 'Jun 1, 2026', outstanding: 86400 },
    { id: 'ln_201', user: 'Adaeze Okonkwo', amt: 75000, tenor: '2 months', rate: '4.5%/mo', status: 'repaid', score: 745, due: '—', outstanding: 0 },
  ];
  const SAVINGS = [
    { id: 'sv_88', user: 'Seun Afolabi', principal: 500000, rate: '18% p.a.', start: 'Jan 12, 2026', maturity: 'Jul 12, 2026', status: 'active', payout: 545000 },
    { id: 'sv_87', user: 'Adaeze Okonkwo', principal: 200000, rate: '16% p.a.', start: 'Dec 9, 2025', maturity: 'Jun 9, 2026', status: 'matured', payout: 216000 },
    { id: 'sv_86', user: 'Ibrahim Musa', principal: 100000, rate: '15% p.a.', start: 'Nov 3, 2025', maturity: 'May 3, 2026', status: 'paid', payout: 107500 },
  ];
  const CARDS = [
    { id: 'cd_31', user: 'Seun Afolabi', last4: '4821', cur: 'USD', bal: 320.50, status: 'active', spend30: 1240.00 },
    { id: 'cd_30', user: 'Adaeze Okonkwo', last4: '9034', cur: 'USD', bal: 58.20, status: 'active', spend30: 310.75 },
    { id: 'cd_29', user: 'Tunde Eze', last4: '1177', cur: 'USD', bal: 0, status: 'frozen', spend30: 0 },
  ];
  const KYCQ = [
    { user: 'Ngozi Umeh', id: 'u_1005', type: 'bvn', submitted: ago(38), note: 'BVN name matches; selfie pending', tier: '0 → 1' },
    { user: 'Femi Adewale', id: 'u_1011', type: 'face', submitted: ago(140), note: 'Liveness score 0.93 (Prembly)', tier: '2 → 3' },
    { user: 'Blessing Eke', id: 'u_1014', type: 'nin', submitted: ago(260), note: 'NIN photo low-light — manual review', tier: '1 → 2' },
  ];
  const WEBHOOKS = [
    { src: 'Monnify', event: 'fund.success', ref: 'MNFY|82|441', sig: 'verified', code: 200, time: ago(18) },
    { src: 'Monnify', event: 'disbursement.completed', ref: 'MNFY|82|438', sig: 'verified', code: 200, time: ago(55) },
    { src: 'Meta WA', event: 'message.delivered', ref: 'wamid.HBg…98', sig: 'verified', code: 200, time: ago(61) },
    { src: 'Meta WA', event: 'message.failed · 131049', ref: 'wamid.HBg…41', sig: 'verified', code: 200, time: ago(190), note: 'marketing limit — recorded, not retried' },
    { src: 'Monnify', event: 'disbursement.reversed', ref: 'MNFY|82|405', sig: 'verified', code: 200, time: ago(300), note: 'wallet refunded' },
    { src: 'Baxi', event: 'vtu.callback', ref: 'BAXI-77231', sig: 'n/a', code: 200, time: ago(410) },
  ];
  const RECONS = [
    { run: 'zitch-reconcile-vtu', time: 'Today 02:00', checked: 1840, mismatches: 3, fixed: 3, status: 'done' },
    { run: 'zitch-maturities', time: 'Today 02:10', checked: 41, mismatches: 0, fixed: 0, status: 'done', note: '1 plan paid out (₦216,000)' },
    { run: 'zitch-reconcile-vtu', time: 'Yesterday 02:00', checked: 1716, mismatches: 1, fixed: 1, status: 'done' },
  ];

  const TEAM = [
    { name: 'Amara Nwosu', email: 'amara@zitch.com', role: 'super_admin' },
    { name: 'Dapo Ojo', email: 'dapo@zitch.com', role: 'finance' },
    { name: 'Funmi Alade', email: 'funmi@zitch.com', role: 'support' },
    { name: 'Ada Eke', email: 'ada@zitch.com', role: 'read_only' },
  ];

  const PERMS = [
    { perm: 'View dashboards & logs', super_admin: true, finance: true, support: true, read_only: true },
    { perm: 'Reply / handover WhatsApp chats', super_admin: true, finance: false, support: true, read_only: false },
    { perm: 'Send broadcasts', super_admin: true, finance: false, support: true, read_only: false },
    { perm: 'Refund / requery transactions', super_admin: true, finance: true, support: false, read_only: false },
    { perm: 'Edit FX margin & corridors', super_admin: true, finance: true, support: false, read_only: false },
    { perm: 'Freeze users / reset PIN', super_admin: true, finance: true, support: false, read_only: false },
    { perm: 'AI kill switch & system settings', super_admin: true, finance: false, support: false, read_only: false },
    { perm: 'Manage team & roles', super_admin: true, finance: false, support: false, read_only: false },
  ];

  const SETTINGS = [
    { key: 'ai_enabled_global', value: 'true', desc: 'Master switch for the WhatsApp AI intent layer. Off ⇒ channel is fully menu-driven.' },
    { key: 'fx_margin_bps', value: '60', desc: 'Margin added over the provider rate on every conversion quote.' },
    { key: 'fx_quote_ttl_seconds', value: '60', desc: 'How long a conversion quote stays valid. Expired quotes are never settled.' },
    { key: 'wa_pin_max_attempts', value: '1', desc: 'Wrong-PIN attempts before a WhatsApp flow is cancelled.' },
    { key: 'cny_settlement_enabled', value: 'false', desc: 'CNY corridor — quote/display only until a settlement partner is live.' },
    { key: 'broadcast_marketing_optin_only', value: 'true', desc: 'Marketing templates only reach users with marketing_opt_in = true.' },
  ];

  const fmtN = (n, cur) => {
    const sym = { NGN: '₦', USD: '$', GBP: '£', CAD: 'C$', CNY: '¥' }[cur] || '';
    const v = Math.abs(n).toLocaleString('en-NG', { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });
    return (n < 0 ? '-' : '') + sym + v;
  };
  const fmtT = (d) => {
    const diff = Math.round((Date.now() - d.getTime()) / 60000);
    if (diff < 60) return diff + 'm ago';
    if (diff < 1440) return Math.round(diff / 60) + 'h ago';
    return Math.round(diff / 1440) + 'd ago';
  };

  return { USERS, TXNS, CONVOS, BROADCASTS, AUDIT, RATES, FLOAT, PROVIDERS, VOLUME_14D, LOANS, SAVINGS, CARDS, KYCQ, WEBHOOKS, RECONS, TEAM, PERMS, SETTINGS, fmtN, fmtT };
})();
