// Money formatting — matches the prototype's money()/moneyk() helpers.
export const money = (n: number | null | undefined): string =>
  '₦' +
  Number(n || 0).toLocaleString('en-NG', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

export const moneyk = (n: number | null | undefined): string =>
  '₦' + Number(n || 0).toLocaleString('en-NG');
