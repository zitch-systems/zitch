import { safeWhatsAppUrl } from '../externalLinks';

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
});

