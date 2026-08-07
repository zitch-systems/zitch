import {
  outcomeMessage, receiptFileName, receiptHtml, receiptStamp, senderRows,
} from '@/lib/receipt';

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

  it('marks the emphasised row as the total', () => {
    expect(html).toContain('<tr class="total"><td class="k">Total</td>');
    expect(html).toContain('<tr><td class="k">Bank</td>');
  });

  it('stamps Successful by default', () => {
    expect(html).toContain('<span class="badge">Successful</span>');
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

  // A recipient name comes from the bank and a note comes from the user; neither
  // is ours to trust. Escaping matters here because the PDF renderer is a real
  // browser engine — an unescaped value would become markup in the receipt.
  it('escapes values instead of letting them become markup', () => {
    const hostile = receiptHtml({
      title: 'Money sent',
      message: 'ok',
      rows: [['Note', '<script>alert(1)</script>']],
    });
    expect(hostile).not.toContain('<script>');
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

  it('feeds receiptHtml as ordinary rows', () => {
    const html = receiptHtml({
      title: 'Money sent',
      message: 'sent',
      rows: [['Recipient', 'ADEYEMI WILLIAM'], ...senderRows({ name: 'ADA OKafor' })],
    });
    expect(html).toContain('<tr><td class="k">Sender</td><td class="v">ADA OKafor</td></tr>');
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

  it('keeps the amount the largest thing on the page after the wordmark', () => {
    expect(sizeOf('tr.total td')).toBeGreaterThan(sizeOf('  td'));
    expect(sizeOf('.mark')).toBeGreaterThan(sizeOf('tr.total td'));
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
