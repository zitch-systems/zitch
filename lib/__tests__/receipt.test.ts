import {
  WHATSAPP_START_URL, maskAccount, outcomeMessage, qrPath, receiptFileName, receiptHtml, receiptLayout,
  receiptStamp, senderRows, shareReceipt,
} from '@/lib/receipt';
import { BANK_WHATSAPP, BANK_WHATSAPP_DISPLAY, SUPPORT_EMAIL } from '@/components/configFiles/links';

// Dates are built with the local-time constructor on purpose: the stamp is
// deliberately local (it is what the person holding the phone saw), so a test
// written against a UTC instant would pass or fail depending on the runner's zone.
const at = (h: number, m: number) => new Date(2026, 7, 5, h, m);

describe('receiptStamp', () => {
  it('formats the date unambiguously — never 05/08 vs 08/05', () => {
    expect(receiptStamp(at(14, 30)).date).toBe('05 Aug 2026');
  });

  it('reads midnight as 12 AM and noon as 12 PM, not 00 and 00', () => {
    expect(receiptStamp(at(0, 5)).time).toBe('12:05 AM');
    expect(receiptStamp(at(12, 5)).time).toBe('12:05 PM');
  });

  it('pads minutes so 10:05 never renders as 10:5', () => {
    expect(receiptStamp(at(22, 5)).time).toBe('10:05 PM');
  });
});

describe('receiptFileName', () => {
  it('names the file after the reference, per format', () => {
    expect(receiptFileName('ZT-9F3A21', 'jpeg')).toBe('Zitch-Receipt-ZT-9F3A21.jpg');
    expect(receiptFileName('ZT-9F3A21', 'pdf')).toBe('Zitch-Receipt-ZT-9F3A21.pdf');
  });

  it('strips characters a filesystem or share sheet would choke on', () => {
    expect(receiptFileName('ZT/../secret 21', 'jpeg')).toBe('Zitch-Receipt-ZT-secret-21.jpg');
  });

  it('still produces a usable name when there is no reference', () => {
    expect(receiptFileName('', 'pdf')).toBe('Zitch-Receipt.pdf');
  });

  it('bounds the length so a pathological reference cannot blow the path limit', () => {
    expect(receiptFileName('A'.repeat(400), 'jpeg')).toBe(`Zitch-Receipt-${'A'.repeat(48)}.jpg`);
  });
});

describe('receiptLayout', () => {
  // The same arrangement as the WhatsApp receipt (backend receipt_layout), so a
  // customer meets one document wherever they paid.
  const transfer = receiptLayout({
    title: 'Money sent',
    rows: [
      ['Recipient', 'BOLA JOHNSON'], ['Account', '0123456789'], ['Bank', 'Access Bank'],
      ['Note', 'June rent'], ['Transfer amount', '₦5,000.00', true],
      ['Bank fee', '₦10.75'], ['Total debited', '₦5,010.75'],
      ...senderRows({ name: 'ADA EZE', account: '0100000123', bank: 'Wema Bank' }),
      ['Date', '13 Nov 2026'], ['Time', '04:01 PM'], ['Reference', 'ZT-1'],
    ],
  });

  it('makes the amount the headline and prints it once', () => {
    expect(transfer.amount).toBe('₦5,000.00');
    expect(transfer.rows.map(([k]) => k)).not.toContain('Transfer amount');
  });

  it('puts the date and time under the status, not in the rows', () => {
    expect(transfer.when).toBe('13 Nov 2026, 04:01 PM');
    expect(transfer.rows.map(([k]) => k)).not.toEqual(expect.arrayContaining(['Date', 'Time']));
  });

  it('reads who was paid and who paid as one entry each, masking the payer', () => {
    expect(transfer.parties).toEqual([
      { label: 'Recipient', name: 'BOLA JOHNSON', detail: 'Access Bank · 0123456789' },
      { label: 'Sender', name: 'ADA EZE', detail: 'Wema Bank · 0100****23' },
    ]);
  });

  it('keeps everything else in order, with the reference last', () => {
    expect(transfer.rows.map(([k]) => k)).toEqual(['Note', 'Bank fee', 'Total debited', 'Reference']);
  });

  it('drops a Total or Amount row that only repeats the headline', () => {
    const airtime = receiptLayout({
      title: 'Successful',
      rows: [['Network', 'MTN'], ['Amount', '₦500.00'], ['Fee', '₦0'], ['Total', '₦500.00', true]],
    });
    expect(airtime.amount).toBe('₦500.00');
    expect(airtime.rows.map(([k]) => k)).toEqual(['Network', 'Fee']);
  });

  it('heads a loan with the money that moved, keeping the emphasised repayment as a row', () => {
    const loan = receiptLayout({
      title: 'Loan disbursed',
      rows: [['Loan amount', '₦10,000.00'], ['Interest', '₦500.00'], ['Total repayment', '₦10,500.00', true]],
    });
    expect(loan.amount).toBe('₦10,000.00');
    expect(loan.rows).toContainEqual(['Total repayment', '₦10,500.00', true]);
  });

  it('boxes a token or PIN, the one value someone types somewhere else', () => {
    const power = receiptLayout({
      title: 'Token generated',
      rows: [['Disco', 'Ikeja Electric'], ['Token', '1234 5678 9012 3456 7890'], ['Total', '₦10,000.00', true]],
    });
    expect(power.highlights).toEqual([['Token', '1234 5678 9012 3456 7890']]);
    expect(power.rows.map(([k]) => k)).toEqual(['Disco']);
    const pins = receiptLayout({ title: 'PIN purchased', rows: [['PIN 1', '111'], ['PIN 2', '222']] });
    expect(pins.highlights.map(([k]) => k)).toEqual(['PIN 1', 'PIN 2']);
  });

  it('falls back to the title when nothing is the money that moved', () => {
    const pending = receiptLayout({ title: 'Savings request processing', rows: [['Requested duration', '30 days']] });
    expect(pending.amount).toBe('');
    expect(pending.rows).toEqual([['Requested duration', '30 days', false]]);
  });
});

describe('maskAccount', () => {
  it('masks the middle the way bank alerts do', () => {
    expect(maskAccount('0123456789')).toBe('0123****89');
    expect(maskAccount('012-345-6789')).toBe('0123****89');
    expect(maskAccount('1234')).toBe('1234');
  });
});

describe('the WhatsApp code', () => {
  it('opens a chat with the banking number with "Hi" typed', () => {
    expect(WHATSAPP_START_URL).toBe(`https://wa.me/${BANK_WHATSAPP}?text=Hi`);
  });

  it('is a well-formed QR symbol', () => {
    const { size, d } = qrPath(WHATSAPP_START_URL);
    expect((size - 17) % 4).toBe(0);           // versions are 21, 25, 29… modules square
    expect(d.startsWith('M0 0h7v1h-7z')).toBe(true);   // the top-left finder pattern
  });
});

describe('receiptHtml', () => {
  const html = receiptHtml({
    title: 'Money sent',
    message: '₦2,000.00 sent to ADEYEMI WILLIAM.',
    rows: [['Bank', 'Access Bank'], ['Date', '05 Aug 2026'], ['Total', '₦2,000.00', true]],
  });

  it('carries every row through', () => {
    expect(html).toContain('Access Bank');
    expect(html).toContain('05 Aug 2026');
  });

  it('puts the amount in the headline, once', () => {
    expect(html).toContain('<div class="amount">₦2,000.00</div>');
    expect(html.split('₦2,000.00').length - 1).toBe(2);   // the headline and the message
    expect(html).toContain('<tr><td class="k">Bank</td>');
  });

  it('emphasises a strong row that is not the headline', () => {
    const loan = receiptHtml({
      title: 'Loan disbursed', message: 'x',
      rows: [['Loan amount', '₦10,000.00'], ['Total repayment', '₦10,500.00', true]],
    });
    expect(loan).toContain('<tr class="strong"><td class="k">Total repayment</td>');
  });

  it('stamps Successful by default', () => {
    expect(html).toContain('<span class="badge">Successful</span>');
  });

  it('includes Zitch WhatsApp banking, its code and where to get help on every PDF', () => {
    expect(html).toContain('Zitch WhatsApp Banking');
    expect(html).toContain('Bank wherever you chat');
    expect(html).toContain(BANK_WHATSAPP_DISPLAY);
    expect(html).toContain('zitch.ng');
    expect(html).toContain(`d="${qrPath(WHATSAPP_START_URL).d}"`);
    expect(html).toContain('Scan to start');
    expect(html).toContain(SUPPORT_EMAIL);
  });

  // The exported file is what a recipient treats as proof. A pending transfer
  // may yet be reversed and a failed one never happened — neither may leave the
  // app dressed in success green.
  it('stamps the real status on a pending or failed transaction', () => {
    const pendingHtml = receiptHtml({ title: 'Transfer processing', message: 'x', rows: [], status: 'Processing' });
    expect(pendingHtml).toContain('<span class="badge">Processing</span>');
    expect(pendingHtml).not.toContain('Successful');
    expect(pendingHtml).toContain('#b9770e');   // amber, not success green

    const failedHtml = receiptHtml({ title: 'Transfer', message: 'x', rows: [], status: 'Failed' });
    expect(failedHtml).toContain('<span class="badge">Failed</span>');
    expect(failedHtml).toContain('#c0392b');    // red
    expect(failedHtml).not.toContain('#128c4a');
  });

  it('renders blank or unknown status as amber, never success green', () => {
    const blankHtml = receiptHtml({ title: 'Transfer', message: 'x', rows: [], status: '' });
    expect(blankHtml).toContain('<span class="badge">Status unavailable</span>');
    expect(blankHtml).toContain('#b9770e');
    expect(blankHtml).not.toContain('#128c4a');

    const unknownHtml = receiptHtml({ title: 'Transfer', message: 'x', rows: [], status: 'BANK_REVIEW' });
    expect(unknownHtml).toContain('<span class="badge">BANK_REVIEW</span>');
    expect(unknownHtml).toContain('#b9770e');
    expect(unknownHtml).not.toContain('#128c4a');
  });

  // A recipient name comes from the bank and a note comes from the user; neither
  // is ours to trust. Escaping matters here because the PDF renderer is a real
  // browser engine — an unescaped value would become markup in the receipt.
  it('escapes values instead of letting them become markup', () => {
    const hostile = receiptHtml({
      title: 'Money sent',
      message: 'ok',
      rows: [['Note', '<script>alert(1)</script>'], ['Recipient', '<b>x</b>']],
    });
    expect(hostile).not.toContain('<script>');
    expect(hostile).not.toContain('<b>x</b>');
    expect(hostile).toContain('&lt;script&gt;');
  });
});

describe('outcomeMessage', () => {
  it('says nothing when the user backed out — a cancel is not an event', () => {
    expect(outcomeMessage('cancelled', 'pdf')).toBeNull();
  });

  it('names the format the user picked', () => {
    expect(outcomeMessage('saved', 'jpeg')).toBe('Image saved to your device');
    expect(outcomeMessage('saved', 'pdf')).toBe('PDF saved to your device');
  });
});

describe('senderRows', () => {
  it('names who paid, in the order a reader scans', () => {
    expect(senderRows({ name: 'ADA OKafor', account: '0123456789', bank: 'Zitch' }))
      .toEqual([
        ['Sender', 'ADA OKafor'],
        ['Sender account', '0123456789'],
        ['Sender bank', 'Zitch'],
      ]);
  });

  it('omits what it does not know rather than printing a blank row', () => {
    // A receipt is treated as proof. "Sender: —" reads as missing data; no row
    // at all reads as a receipt that never carried the field.
    expect(senderRows({ name: 'ADA OKafor' })).toEqual([['Sender', 'ADA OKafor']]);
    expect(senderRows({ name: '  ', account: '', bank: undefined })).toEqual([]);
    expect(senderRows(undefined)).toEqual([]);
  });

  it('trims, so a stray space from the API is not printed as indentation', () => {
    expect(senderRows({ name: ' ADA OKafor ' })).toEqual([['Sender', 'ADA OKafor']]);
  });

  it('feeds receiptHtml as the Sender entry', () => {
    const html = receiptHtml({
      title: 'Money sent',
      message: 'sent',
      rows: [['Recipient', 'ADEYEMI WILLIAM'], ...senderRows({ name: 'ADA OKafor', account: '0123456789' })],
    });
    expect(html).toContain('<tr><td class="k">Sender</td><td class="v"><div>ADA OKafor</div><div class="sub">0123****89</div></td></tr>');
    expect(html).not.toContain('0123456789');
  });
});

describe('receiptHtml typography', () => {
  const html = receiptHtml({ title: 'T', message: 'm', rows: [['k', 'v']] });
  const sizeOf = (selector: string): number => {
    const block = new RegExp(`${selector}\\s*\\{[^}]*?font-size:\\s*([\\d.]+)px`).exec(html);
    if (!block) throw new Error(`no font-size for ${selector}`);
    return Number(block[1]);
  };

  // The PDF is a page-shaped document. CSS px map at 96dpi, so 13px was under
  // 10pt — below what a printed receipt can be read at comfortably, and well
  // under the ~11pt body text normally sets at.
  it('sets row text at a readable print size', () => {
    expect(sizeOf('  td')).toBeGreaterThanOrEqual(15);
  });

  it('makes the amount the largest thing on the page', () => {
    expect(sizeOf('.amount')).toBeGreaterThan(sizeOf('  td'));
    expect(sizeOf('.amount')).toBeGreaterThan(sizeOf('.mark'));
  });

  it('leaves no text below 12px, the floor for the footer and labels', () => {
    const all = [...html.matchAll(/font-size:\s*([\d.]+)px/g)].map((m) => Number(m[1]));
    expect(all.length).toBeGreaterThan(5);
    expect(Math.min(...all)).toBeGreaterThanOrEqual(12);
  });

  it('lets a long value wrap instead of colliding with its label', () => {
    expect(html).toContain('word-break: break-word');
  });
});

describe('receiptHtml watermark', () => {
  const html = receiptHtml({ title: 'T', message: 'm', rows: [['k', 'v']] });

  it('marks the page with the Z mark so a forwarded copy still carries provenance', () => {
    expect(html).toContain('class="wm"');
    expect(/\.wm\s*\{[^}]*background-image:\s*url\(data:image\/png;base64,/.test(html)).toBe(true);
  });

  it('repeats on every printed page, not just the first', () => {
    // A statically positioned layer would render once and leave page two bare.
    expect(/\.wm\s*\{[^}]*position:\s*fixed/.test(html)).toBe(true);
  });

  it('is faint enough not to fight the amount', () => {
    const opacity = /\.wm\s*\{[^}]*opacity:\s*([\d.]+)/.exec(html);
    expect(opacity).not.toBeNull();
    expect(Number(opacity![1])).toBeLessThanOrEqual(0.08);
  });

  it('does not push the rows down — it marks the document, it does not change it', () => {
    // `position: fixed` takes it out of flow; an in-flow watermark would move
    // every row below it and alter the artifact it exists to mark.
    expect(/\.wm\s*\{[^}]*inset:/.test(html)).toBe(true);
  });
});


it('does not claim a payment succeeded when exporting its receipt fails', async () => {
  jest.spyOn(require('expo-sharing'), 'isAvailableAsync').mockResolvedValue(true);
  const warning = jest.spyOn(console, 'warn').mockImplementation(() => {});
  try {
    const outcome = await shareReceipt('jpeg', { reference: 'PENDING-REF', html: '', capture: async () => { throw new Error('Capture unavailable'); } });
    expect(outcome).toBe('failed');
    expect(outcomeMessage(outcome, 'jpeg')).not.toContain('payment went through');
    expect(outcomeMessage(outcome, 'jpeg')).toContain('Check the transaction status');
  } finally { warning.mockRestore(); }
});
