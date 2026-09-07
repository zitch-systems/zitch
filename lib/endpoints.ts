// Single source of truth for backend endpoint paths.
//
// Screens used to hardcode '/api/...' strings inline, which scattered the API
// surface and let inconsistencies creep in (e.g. both `/api/transfer/send/` and
// `/api/transfers/send/` existed in the codebase). Centralizing them here makes
// the surface auditable and refactors safe. Add new endpoints to this map and
// reference EP.<domain>.<name> from a service in lib/services/*.
//
// NOTE: paths below mirror what the screens currently call. The transfers
// duplication (`transfer` vs `transfers`) is preserved as-used and flagged for a
// backend/client reconciliation — do not "fix" a path here without confirming
// which one the live API actually serves.

export const EP = {
  auth: {
    setPassword: '/api/set-password/',
    setTransactionPin: '/api/set-transaction-pin/',
    logout: '/api/logout/',
    updateInfo: '/api/update_info/',
    avatar: '/api/profile/avatar/',
  },
  wallet: {
    balance: '/api/wallet_balance/',
    history: '/api/user-transaction-history/',
    account: '/api/wallet/account/',
    createAccount: '/api/wallet/account/create/',
    wemaVerifyOtp: '/api/wallet/wema/verify-otp/',
    wemaResendOtp: '/api/wallet/wema/resend-otp/',
  },
  kyc: {
    status: '/api/kyc/status/',
    bvnStart: '/api/kyc/bvn/start/',
    bvnConfirm: '/api/kyc/bvn/confirm/',
    nin: '/api/kyc/nin/',
    face: '/api/kyc/face/',
  },
  transfers: {
    // Canonical (plural) paths.
    resolve: '/api/transfers/resolve/',
    send: '/api/transfers/send/',
    beneficiaries: '/api/transfers/beneficiaries/',
    // Legacy singular aliases still referenced by some screens — see NOTE above.
    resolveLegacy: '/api/transfer/resolve/',
    sendLegacy: '/api/transfer/send/',
  },
  utility: {
    buyAirtime: '/api/utility/buyairtime/',
    buyData: '/api/utility/buydata/',
    buyCable: '/api/utility/buycable/',
    buyElectricity: '/api/utility/buyelectricity/',
    validateIuc: '/api/utility/validate_iuc/',
    validateMeter: '/api/utility/validate_meter/',
  },
  cards: {
    list: '/api/cards/list/',
    create: '/api/cards/create/',
    details: '/api/cards/details/',
    fund: '/api/cards/fund/',
    freeze: '/api/cards/freeze/',
  },
  savings: {
    list: '/api/savings/list/',
    create: '/api/savings/create/',
  },
  loans: {
    status: '/api/loans/status/',
    request: '/api/loans/request/',
    repay: '/api/loans/repay/',
  },
  betting: { fund: '/api/betting/fund/' },
  exams: { buy: '/api/exams/buy/' },
  convert: { fx: '/api/convert/fx/' },
} as const;

export type EndpointDomain = keyof typeof EP;
