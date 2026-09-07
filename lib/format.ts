// Money formatting — matches the design's `fmtN`: whole naira (₦ + grouped),
// showing kobo only when an amount actually has a fractional part, so balances
// read "₦150,000" like the prototype rather than "₦150,000.00".
export const money = (n: number | null | undefined): string =>
  '₦' +
  Number(n || 0).toLocaleString('en-NG', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });

export const moneyk = (n: number | null | undefined): string =>
  '₦' + Number(n || 0).toLocaleString('en-NG');
