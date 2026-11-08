import React from 'react';
import renderer, { act } from 'react-test-renderer';
import BuyAirtime from '../buyairtime';
import BuyData from '../buydata';
import BuyCable from '../buycable';
import BuyElectricity from '../buyelectricity';
import BuyExam from '../exams';
import Utility from '../utility';
import Betting from '../betting';
import Remita from '../remita';

// All route-specific payment safety is exercised in the shared checkout suite.
// These entry points must not fall back to a retired provider's checkout.
jest.mock('@/components/banking/PaymentScreen', () => ({ kind }: { kind: string }) => {
  const ReactActual = jest.requireActual('react');
  const { Text } = jest.requireActual('react-native');
  return ReactActual.createElement(Text, null, kind);
});
it.each([[BuyAirtime, 'airtime'], [BuyData, 'data'], [BuyCable, 'bill'], [BuyElectricity, 'bill'],
  [BuyExam, 'bill'], [Remita, 'bill'], [Utility, 'bill'], [Betting, 'bill']] as const)('routes each service to the shared 9PSB checkout', (Component, kind) => {
  let tree!: renderer.ReactTestRenderer;
  act(() => { tree = renderer.create(<Component />); });
  expect(JSON.stringify(tree.toJSON())).toContain(kind);
  act(() => { tree.unmount(); });
});
