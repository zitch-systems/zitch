import React from 'react';
import FeatureScreen from '@/components/design/FeatureScreen';

const Water = () => (
  <FeatureScreen
    title="Water Bills"
    icon="bills"
    tagline="Water bill payments are not available yet. Supported providers will appear here when the service launches."
    points={[
      { icon: 'bills', title: 'Water providers', sub: 'Availability will be confirmed at launch' },
      { icon: 'check', title: 'Verified accounts', sub: 'We confirm your account before paying' },
      { icon: 'history', title: 'Payment history', sub: 'Every receipt saved for you' },
    ]}
    primaryLabel="Talk to us"
    note="Water board billers are being connected in your region. Contact support for availability updates."
  />
);

export default Water;
