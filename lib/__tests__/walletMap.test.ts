// Mock the api/secureStore imports so importing lib/wallet doesn't pull the
// real network stack — we only want the pure mapTxn mapper.
jest.mock('@/lib/api', () => ({ apiPost: jest.fn() }));
jest.mock('@/lib/secureStore', () => ({ getToken: jest.fn() }));

import { mapTxn } from '@/lib/wallet';

describe('mapTxn', () => {
  it('honours the backend direction field over the label heuristic', () => {
    // Label says "send" (an outflow word) but the backend marked it `in`.
    const t = mapTxn({ service: 'Send', direction: 'in', amount: 100 }, 0);
    expect(t.dir).toBe('in');
  });

  it('falls back to the label heuristic when direction is absent', () => {
    expect(mapTxn({ service: 'Airtime purchase', amount: 200 }, 0).dir).toBe('out');
    expect(mapTxn({ service: 'Wallet funding', amount: 500 }, 0).dir).toBe('in');
  });

  it('maps service labels to the right icon', () => {
    expect(mapTxn({ service: 'MTN Airtime' }, 0).icon).toBe('airtime');
    expect(mapTxn({ service: 'GLO Data' }, 0).icon).toBe('data');
    expect(mapTxn({ service: 'DSTV subscription' }, 0).icon).toBe('tv');
    expect(mapTxn({ service: 'Electricity token' }, 0).icon).toBe('bills');
    expect(mapTxn({ service: 'Transfer to John' }, 0).icon).toBe('send');
    expect(mapTxn({ service: 'Mystery' }, 0).icon).toBe('wallet');
  });

  it('coerces amount to a number and carries a stable id', () => {
    const t = mapTxn({ service: 'X', amount: '1500', reference: 'REF-1' }, 3);
    expect(t.amount).toBe(1500);
    expect(t.id).toBe('REF-1');
  });

  it('uses the row index as a last-resort id', () => {
    expect(mapTxn({ service: 'X' }, 7).id).toBe('7');
  });
});
