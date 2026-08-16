import AsyncStorage from '@react-native-async-storage/async-storage';

const KEY = 'z-pending-wa-approval';
const MAX_AGE_MS = 10 * 60 * 1000;

type StoredApproval = { token: string; savedAt: number };

const cleanToken = (value: unknown): string => {
  const token = String(value ?? '').trim();
  return /^[A-Za-z0-9._-]{1,128}$/.test(token) ? token : '';
};

/** Capture either the custom-scheme route or the HTTPS Android App Link. */
export async function rememberWhatsAppApprovalUrl(url: string | null | undefined): Promise<void> {
  if (!url) return;
  let token = '';
  try {
    const parsed = new URL(url);
    const customScheme = parsed.protocol === 'zitch:'
      && (parsed.hostname === 'waapprove' || parsed.pathname === '/waapprove');
    const appLink = parsed.protocol === 'https:'
      && parsed.hostname === 'api.zitch.ng'
      && parsed.pathname.startsWith('/wa/approve/');
    if (customScheme) token = parsed.searchParams.get('token') || '';
    if (appLink) {
      const match = parsed.pathname.match(/\/wa\/approve\/([A-Za-z0-9._-]+)/);
      token = match?.[1] || '';
    }
  } catch {
    // URL() understands both supported forms. Keep a tightly scoped fallback
    // for older JavaScript runtimes rather than accepting a token parameter
    // from an unrelated deep link.
    const match = String(url).match(
      /^zitch:(?:\/\/)?\/?waapprove\?(?:[^#&]+&)*token=([A-Za-z0-9._-]+)(?:&|#|$)/,
    );
    token = match?.[1] || '';
  }
  await rememberWhatsAppApproval(token);
}

export async function rememberWhatsAppApproval(value: unknown): Promise<void> {
  const token = cleanToken(value);
  if (!token) return;
  await AsyncStorage.setItem(KEY, JSON.stringify({ token, savedAt: Date.now() } satisfies StoredApproval));
}

export async function pendingWhatsAppApproval(): Promise<string> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    if (!raw) return '';
    const stored = JSON.parse(raw) as Partial<StoredApproval>;
    const token = cleanToken(stored.token);
    const savedAt = Number(stored.savedAt);
    if (!token || !Number.isFinite(savedAt) || Date.now() - savedAt > MAX_AGE_MS) {
      await AsyncStorage.removeItem(KEY);
      return '';
    }
    return token;
  } catch {
    await AsyncStorage.removeItem(KEY);
    return '';
  }
}

export async function clearPendingWhatsAppApproval(): Promise<void> {
  await AsyncStorage.removeItem(KEY);
}
