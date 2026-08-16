import { safeWhatsAppUrl } from '../externalLinks';
import { BANK_WHATSAPP } from '../../components/configFiles/links';

describe('safeWhatsAppUrl', () => {
  it('allows official WhatsApp HTTPS links', () => {
    expect(safeWhatsAppUrl('https://wa.me/2348012345678?text=LINK%20123456')).toContain('wa.me/');
    expect(safeWhatsAppUrl('https://api.whatsapp.com/send?phone=2348012345678')).toContain('api.whatsapp.com/');
  });

  it('rejects untrusted schemes, hosts, and credential-bearing URLs', () => {
    expect(safeWhatsAppUrl('intent://wa.me/2348012345678')).toBeNull();
    expect(safeWhatsAppUrl('https://wa.me.evil.example/2348012345678')).toBeNull();
    expect(safeWhatsAppUrl('https://user:pass@wa.me/2348012345678')).toBeNull();
  });

  it('routes the banking button to the live Zitch Cloud API number', () => {
    expect(BANK_WHATSAPP).toBe('2349062831750');
    expect(safeWhatsAppUrl(`https://wa.me/${BANK_WHATSAPP}?text=Hi%20Zitch`))
      .toContain(`wa.me/${BANK_WHATSAPP}`);
  });
});

