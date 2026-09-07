const WHATSAPP_HOSTS = new Set(['wa.me', 'api.whatsapp.com']);

/** Return a safe HTTPS WhatsApp deep link, or null for an untrusted target. */
export function safeWhatsAppUrl(value: string | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (
      url.protocol !== 'https:' ||
      !WHATSAPP_HOSTS.has(url.hostname.toLowerCase()) ||
      url.username ||
      url.password
    ) {
      return null;
    }
    return url.toString();
  } catch {
    return null;
  }
}

