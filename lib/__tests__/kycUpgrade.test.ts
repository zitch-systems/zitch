/**
 * The combined existing-account upgrade, as seen from the app.
 *
 * This path existed on the backend (`/api/wallet/wema/upgrade-tier2/`) but
 * nothing in the app ever called it: it was absent from `EP`, absent from
 * `kycService`, and `startNin` routed to account creation instead — which, for
 * an account the bank has already opened, comes back `upgrade_required` and
 * cannot be retried into success. So the refusal told customers to "finish in
 * the Zitch app" while the app offered them the same dead end.
 *
 * These tests pin the wiring rather than the styling: the endpoint the request
 * goes to, and that all three fields the bank scores together are actually in
 * the body. Either one silently missing puts the customer back in the loop.
 */
import { EP } from '@/lib/endpoints';

// `mock`-prefixed so jest's hoisting of the factory permits the reference.
const mockApiJson = jest.fn();
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));

// Imported after the mock so the service binds to it.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { kycService } = require('@/lib/services/kyc');

describe('kycService.upgradeTier2', () => {
  beforeEach(() => {
    mockApiJson.mockReset();
    mockApiJson.mockResolvedValue({ success: true, tier: 2 });
  });

  it('is reachable at all', () => {
    expect(typeof kycService.upgradeTier2).toBe('function');
  });

  it('posts to the combined upgrade endpoint', async () => {
    await kycService.upgradeTier2('22222222222', '11111111111', 'BASE64IMAGE');
    expect(mockApiJson).toHaveBeenCalledTimes(1);
    expect(mockApiJson.mock.calls[0][0]).toBe(EP.wallet.wemaUpgradeTier2);
    expect(EP.wallet.wemaUpgradeTier2).toBe('/api/wallet/wema/upgrade-tier2/');
  });

  it('sends BVN, NIN and the live image together', async () => {
    await kycService.upgradeTier2('22222222222', '11111111111', 'BASE64IMAGE');
    // The bank scores all three in one request; a body missing any of them is
    // refused, and the customer has already handed over their identity by then.
    expect(mockApiJson.mock.calls[0][1]).toEqual({
      bvn: '22222222222',
      nin: '11111111111',
      live_image: 'BASE64IMAGE',
    });
  });

  it('does not route through account creation', async () => {
    await kycService.upgradeTier2('22222222222', '11111111111', 'BASE64IMAGE');
    expect(mockApiJson.mock.calls[0][0]).not.toBe(EP.wallet.createAccount);
  });
});
