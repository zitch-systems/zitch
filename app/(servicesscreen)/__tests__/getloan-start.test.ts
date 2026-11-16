jest.mock('expo-router', () => ({ router: { push: jest.fn(), back: jest.fn(), replace: jest.fn() } }));
jest.mock('@react-native-async-storage/async-storage', () => ({ getItem: jest.fn(async () => null), setItem: jest.fn(async () => {}), removeItem: jest.fn(async () => {}) }));
// eslint-disable-next-line import/first
import { startingLoanAmount } from '../getloan';

describe('startingLoanAmount', () => {
  it('opens on ₦100,000 when the credit allows it', () => {
    expect(startingLoanAmount(500000)).toBe(100000);
    expect(startingLoanAmount(100000)).toBe(100000);
  });

  it('never opens above the credit limit', () => {
    expect(startingLoanAmount(50000)).toBe(50000);
    expect(startingLoanAmount(75000)).toBe(50000);
    expect(startingLoanAmount(15000)).toBe(15000);
  });

  it('falls back to the smallest amount when the credit is under the minimum', () => {
    expect(startingLoanAmount(5000)).toBe(20000);
  });
});
