// The bill screens open with the last number, meter, smartcard or betting ID
// used, and list the others under it. The list comes from the customer's own
// successful payments (backend utility/views.py bill_recents); it is fetched
// once per signed-in session and refreshed after each successful payment.
import { useEffect, useState } from 'react';
import { recentsService, type BillRecents } from '@/lib/services/bills';
import { getSessionGeneration } from '@/lib/secureStore';

export const EMPTY_RECENTS: BillRecents = { phones: [], meters: [], smartcards: [], betting: [], exam_phones: [] };

let cache: { generation: number; value: BillRecents } | null = null;
let inflight: { generation: number; promise: Promise<BillRecents> } | null = null;
const listeners = new Set<(value: BillRecents) => void>();

const list = <T,>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : []);

export const loadBillRecents = (force = false): Promise<BillRecents> => {
  const generation = getSessionGeneration();
  if (!force && cache && cache.generation === generation) return Promise.resolve(cache.value);
  if (!force && inflight && inflight.generation === generation) return inflight.promise;
  const promise = recentsService.get().then((r) => {
    const value: BillRecents = r && r.success
      ? {
          phones: list(r.phones), meters: list(r.meters), smartcards: list(r.smartcards),
          betting: list(r.betting), exam_phones: list(r.exam_phones),
        }
      : (cache && cache.generation === generation ? cache.value : EMPTY_RECENTS);
    // A sign-out while this was in flight must not leak the old list into the
    // next customer's session.
    if (generation !== getSessionGeneration()) return EMPTY_RECENTS;
    if (r && r.success) cache = { generation, value };
    listeners.forEach((fn) => fn(value));
    return value;
  }).catch(() => EMPTY_RECENTS).finally(() => { if (inflight?.promise === promise) inflight = null; });
  inflight = { generation, promise };
  return promise;
};

/** Refresh after a successful payment, so the next screen opens with it. */
export const refreshBillRecents = () => { void loadBillRecents(true); };

export const useBillRecents = (): BillRecents => {
  const generation = getSessionGeneration();
  const [value, setValue] = useState<BillRecents>(() => (cache && cache.generation === generation ? cache.value : EMPTY_RECENTS));
  useEffect(() => {
    let alive = true;
    const on = (next: BillRecents) => { if (alive) setValue(next); };
    listeners.add(on);
    loadBillRecents().then(on);
    return () => { alive = false; listeners.delete(on); };
  }, []);
  return value;
};

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
/** "12 Nov" (this year) or "12 Nov 2025". Blank when the date is unreadable. */
export const shortDate = (iso: string, now: Date = new Date()): string => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const day = `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  return d.getFullYear() === now.getFullYear() ? day : `${day} ${d.getFullYear()}`;
};

/** "0801 234 5678" for an 11-digit local number; anything else unchanged. */
export const groupPhone = (phone: string): string =>
  /^\d{11}$/.test(phone) ? `${phone.slice(0, 4)} ${phone.slice(4, 7)} ${phone.slice(7)}` : phone;

/** The network a Nigerian number most likely belongs to, by its prefix
 *  ('1' MTN, '2' GLO, '3' Airtel, '4' 9mobile), or '' when unknown. Numbers
 *  can be ported, so this only suggests; the customer's choice wins. */
export const networkForPhone = (phone: string): string => {
  const p = (phone || '').replace(/\D/g, '').replace(/^234/, '0').slice(0, 4);
  if (p.length < 4) return '';
  const MTN = ['0803', '0806', '0810', '0813', '0814', '0816', '0903', '0906', '0913', '0916', '0703', '0706', '0704', '0707'];
  const GLO = ['0805', '0807', '0811', '0815', '0905', '0915', '0705'];
  const AIRTEL = ['0802', '0808', '0812', '0901', '0902', '0904', '0907', '0912', '0701', '0708'];
  const NINE = ['0809', '0817', '0818', '0908', '0909'];
  if (MTN.includes(p)) return '1';
  if (GLO.includes(p)) return '2';
  if (AIRTEL.includes(p)) return '3';
  if (NINE.includes(p)) return '4';
  return '';
};
