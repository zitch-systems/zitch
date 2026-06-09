// Zitch Admin — live data scaffold. The handoff's mock arrays are gone: ZAPI
// (api.js) fills these from /api/ops/* after login, and views re-render via the
// version bump in portal.jsx. Shapes mirror the design's mock contract.
window.ZADM = (function () {
  const fmtN = (n, cur) => {
    const sym = { NGN: '₦', USD: '$', GBP: '£', CAD: 'C$', CNY: '¥' }[cur] || '';
    const v = Math.abs(n).toLocaleString('en-NG', { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });
    return (n < 0 ? '-' : '') + sym + v;
  };
  const fmtT = (d) => {
    if (!d) return '—';
    const t = (d instanceof Date) ? d : new Date(d);
    const diff = Math.round((Date.now() - t.getTime()) / 60000);
    if (diff < 1) return 'now';
    if (diff < 60) return diff + 'm ago';
    if (diff < 1440) return Math.round(diff / 60) + 'h ago';
    return Math.round(diff / 1440) + 'd ago';
  };
  const fmtM = (n) => {
    if (Math.abs(n) >= 1e9) return '₦' + (n / 1e9).toFixed(2) + 'bn';
    if (Math.abs(n) >= 1e6) return '₦' + (n / 1e6).toFixed(1) + 'm';
    if (Math.abs(n) >= 1e3) return '₦' + (n / 1e3).toFixed(0) + 'k';
    return '₦' + n.toFixed(0);
  };
  return {
    SUMMARY: null,        // overview KPIs + volume series + providers + latest
    USERS: [], USERS_TOTAL: 0,
    TXNS: [],
    CONVOS: [],           // inbox rows (threads fetched per selection)
    BROADCASTS: [], BC_META: { opted_in: 0, linked: 0 },
    AUDIT: [],
    FX: { margin: 0, rates: [], float: [] },
    LOANS: [], SAVINGS: [], CARDS: [], MATURED_DUE: 0,
    KYCQ: [],
    RECON: { rows: [], providers: [] },
    AI: { enabled: true, intents: [] },
    SETTINGS: [], TEAM: [], PERMS: [], ROLES: ['super_admin', 'finance', 'support', 'read_only'],
    PROVIDERS: [],
    VOLUME_14D: [],
    fmtN, fmtT, fmtM,
  };
})();
