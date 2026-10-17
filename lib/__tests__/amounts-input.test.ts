import { normalizeAmountInput } from '../amounts';

it.each([
  ['1,000.50', '1000.50'], [' 1250.75 ', '1250.75'], ['00050', '50'],
  ['1,234,567.89', '1234567.89'], ['.5', '0.5'], ['12.', '12.'], ['.', '0.'], ['', ''],
])('preserves the monetary value of %s', (input, expected) => {
  expect(normalizeAmountInput(input)).toBe(expected);
});

it.each(['-500', '+500', '1e3', '1.2.3', '12.345', '1,00', '12,34.56', 'NaN', 'Infinity', '₦100', '100abc', '1 000'])
('rejects ambiguous input %s instead of inventing an amount', (input) => {
  expect(normalizeAmountInput(input)).toBeNull();
});
