// Mirrors backend common.http MIN_* so a screen never lets the customer reach
// the PIN step with an amount the server will refuse, nor refuses one the
// server (and the WhatsApp channel) accepts.
export const MIN_TRANSFER = 50;
export const MIN_AIRTIME = 50;
export const MIN_ELECTRICITY = 500;
