import React from 'react';
import FeatureScreen from '@/components/design/FeatureScreen';

const Water = () => (
  <FeatureScreen
    title="Water Bills"
    icon="bills"
    tagline="Water bill payments are not available yet. Supported providers will appear here when the service launches."
    points={[
      { icon: 'bills', title: 'Water providers', sub: 'Availability will be confirmed at launch' },
      { icon: 'check', title: 'Account requirements', sub: 'Required details will be confirmed before payment' },
      { icon: 'history', title: 'Payment records', sub: 'Receipt options will be confirmed when available' },
    ]}
    primaryLabel="Contact support"
    note="Contact support for availability updates. No water bill payment is made from this page."
  />
);

export default Water;
