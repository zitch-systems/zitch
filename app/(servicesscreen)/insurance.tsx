import React from 'react';
import FeatureScreen from '@/components/design/FeatureScreen';

const Insurance = () => (
  <FeatureScreen
    title="Insurance"
    icon="insurance"
    tagline="Protect what matters — health, your phone, your car and more, from a few naira a day."
    points={[
      { icon: 'insurance', title: 'Health cover', sub: 'Hospital visits & medication' },
      { icon: 'card', title: 'Device protection', sub: 'Screen damage, theft & loss' },
      { icon: 'bills', title: 'Auto & travel', sub: 'Third-party motor and travel plans' },
    ]}
    primaryLabel="Request a quote"
    onPrimary={undefined}
    note="Tell us what you'd like to insure and our team will set you up with a plan."
  />
);

export default Insurance;
