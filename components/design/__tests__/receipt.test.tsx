import React from 'react';
import renderer, { act, ReactTestInstance } from 'react-test-renderer';
import { Text } from 'react-native';
import Receipt from '../Receipt';
import { BANK_WHATSAPP_DISPLAY, SUPPORT_EMAIL } from '@/components/configFiles/links';
import { statusTone } from '@/lib/receipt';

jest.mock('expo-router', () => ({ router: { push: jest.fn() } }));
jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn(async () => true) }));
jest.mock('expo-linear-gradient', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  return { LinearGradient: ({ children, style }: { children?: React.ReactNode; style?: object }) =>
    ReactActual.createElement(View, { style }, children) };
});
jest.mock('react-native-svg', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  const Svg = ({ children }: { children?: React.ReactNode }) => ReactActual.createElement(View, null, children);
  return { __esModule: true, default: Svg, Svg, Path: (props: object) => ReactActual.createElement(View, props) };
});
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/ui', () => ({ Btn: () => null }));
jest.mock('@/components/design/Notify', () => ({ flash: jest.fn(), notifyError: jest.fn() }));
jest.mock('@/components/design/ReceiptExport', () => ({ __esModule: true, default: () => null }));
jest.mock('@/lib/wallet', () => ({
  useWallet: () => ({ accountName: 'ADA EZE', firstName: 'Ada', accountNumber: '0100000123', bankName: 'Wema Bank' }),
}));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { surface: '#fff', line: '#ddd', brand: '#0FA295', ink2: '#222' } }),
  font: { regular: 'r', medium: 'm', semibold: 's', bold: 'b', extrabold: 'x' },
}));

/** The visible string of a Text, including nested Text (the ₦ sign is one). */
const textOf = (node: ReactTestInstance | string): string => {
  if (typeof node === 'string') return node;
  return node.children.map((child) => textOf(child as ReactTestInstance | string)).join('');
};
const texts = (tree: renderer.ReactTestRenderer): string[] =>
  tree.root.findAllByType(Text).filter((t) => !t.parent || t.parent.type !== Text).map(textOf);

const render = (props: Partial<React.ComponentProps<typeof Receipt>>) => {
  let tree!: renderer.ReactTestRenderer;
  act(() => {
    tree = renderer.create(
      <Receipt title="Money sent" message="" rows={[]} onDone={() => {}} {...props} />,
    );
  });
  return tree;
};

it('lays out a transfer as the WhatsApp receipt does', () => {
  const tree = render({
    reference: 'ZT-TRF-1',
    rows: [
      ['Recipient', 'BOLA JOHNSON'], ['Account', '0123456789'], ['Bank', 'Access Bank'],
      ['Transfer amount', '₦5,000.00', true], ['Bank fee', '₦10.75'], ['Total debited', '₦5,010.75'],
    ],
  });
  const all = texts(tree);
  // The amount is the headline, printed once.
  expect(all.filter((t) => t === '₦5,000.00')).toHaveLength(1);
  expect(all).toEqual(expect.arrayContaining([
    'Successful', 'Recipient', 'BOLA JOHNSON', 'Access Bank · 0123456789',
    'Sender', 'ADA EZE', 'Wema Bank · 0100****23',
    'Bank fee', '₦10.75', 'Total debited', '₦5,010.75', 'Reference', 'ZT-TRF-1',
  ]));
  // The payer's own account is masked: the receipt gets forwarded.
  expect(all.join(' ')).not.toContain('0100000123');
  act(() => tree.unmount());
});

it('carries Zitch WhatsApp banking, its code and where to get help', () => {
  const tree = render({ rows: [['Total', '₦500.00', true]] });
  const all = texts(tree);
  expect(all).toEqual(expect.arrayContaining([
    'ZITCH WHATSAPP BANKING', 'Bank wherever you chat', `zitch.ng  ·  ${BANK_WHATSAPP_DISPLAY}`,
    'Scan to start', `Need help? ${SUPPORT_EMAIL}`,
  ]));
  expect(tree.root.findByProps({ accessibilityLabel: 'QR code to start Zitch banking on WhatsApp' })).toBeDefined();
  act(() => tree.unmount());
});

it('boxes an electricity token above the rows', () => {
  const tree = render({
    title: 'Token generated',
    rows: [['Disco', 'Ikeja Electric'], ['Token', '1234 5678 9012 3456 7890'], ['Total', '₦10,000.00', true]],
  });
  const all = texts(tree);
  expect(all).toEqual(expect.arrayContaining(['TOKEN', '1234 5678 9012 3456 7890', '₦10,000.00']));
  expect(all).not.toContain('Total');
  act(() => tree.unmount());
});

it('never dresses a pending transaction in success green', () => {
  const tree = render({ title: 'Transfer processing', status: 'Processing', rows: [['Transfer amount', '₦5,000.00', true]] });
  const pill = tree.root.findByProps({ accessibilityLabel: 'Transaction status: Processing' });
  expect(pill.props.style.backgroundColor).toBe(statusTone('pending').bg);
  expect(texts(tree)).not.toContain('Successful');
  act(() => tree.unmount());
});
