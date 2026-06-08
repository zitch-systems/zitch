import React from 'react';
import { router } from 'expo-router';
import FeatureScreen from '@/components/design/FeatureScreen';

const Bnpl = () => (
  <FeatureScreen
    title="Buy Now, Pay Later"
    icon="loan"
    tagline="Shop today and spread the cost over time — interest-free on your first order."
    points={[
      { icon: 'check', title: 'Split in 4', sub: 'Pay 25% now, the rest over 6 weeks' },
      { icon: 'spark', title: '₦0 interest', sub: 'No fees when you pay on time' },
      { icon: 'chart', title: 'Instant decision', sub: 'Eligibility based on your Zitch limit' },
    ]}
    primaryLabel="Check eligibility"
    primaryIcon="loan"
    onPrimary={() => router.push('/getloan')}
    note="Buy Now, Pay Later uses your Zitch credit limit. Repay from your wallet anytime."
  />
);

export default Bnpl;
