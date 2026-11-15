// Test double: no recent bill values unless a test supplies them.
const actual = jest.requireActual('../recents');

module.exports = {
  ...actual,
  useBillRecents: () => actual.EMPTY_RECENTS,
  loadBillRecents: jest.fn(async () => actual.EMPTY_RECENTS),
  refreshBillRecents: jest.fn(),
};
