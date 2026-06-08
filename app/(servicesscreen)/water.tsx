import React from 'react';
import FeatureScreen from '@/components/design/FeatureScreen';

const Water = () => (
  <FeatureScreen
    title="Water Bills"
    icon="bills"
    tagline="Pay your state water board bill in seconds, straight from your Zitch wallet."
    points={[
      { icon: 'bills', title: 'All state boards', sub: 'Lagos, FCT, Rivers and more' },
      { icon: 'check', title: 'Verified accounts', sub: 'We confirm your account before paying' },
      { icon: 'history', title: 'Payment history', sub: 'Every receipt saved for you' },
    ]}
    primaryLabel="Talk to us"
    note="Water board billers are being connected in your region. Contact support to pay in the meantime."
  />
);

export default Water;
