import {
  outcomeMessage, receiptFileName, receiptHtml, receiptStamp,
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
