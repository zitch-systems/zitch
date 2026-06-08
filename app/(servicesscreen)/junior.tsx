import React from 'react';
import FeatureScreen from '@/components/design/FeatureScreen';

const Junior = () => (
  <FeatureScreen
    title="Zitch Junior"
    icon="invite"
    tagline="A safe pocket-money account for your child, fully managed by you."
    points={[
      { icon: 'wallet', title: 'Kid-safe wallet', sub: 'Set allowances and limits' },
      { icon: 'chart', title: 'Track spending', sub: 'See every transaction in real time' },
      { icon: 'fixed', title: 'Savings goals', sub: 'Help them save for what they want' },
    ]}
    primaryLabel="Join the waitlist"
    note="Zitch Junior requires guardian KYC. Get in touch to be among the first families onboarded."
  />
);

export default Junior;
