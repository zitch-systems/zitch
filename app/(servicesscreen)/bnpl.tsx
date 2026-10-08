import React from 'react';
import FeatureScreen from '@/components/design/FeatureScreen';

const Bnpl = () => (
  <FeatureScreen
    title="Buy Now, Pay Later"
    icon="loan"
    tagline="Buy Now, Pay Later is not available yet. Terms and eligibility will appear when the service launches."
    points={[
      { icon: 'check', title: 'Payment plans', sub: 'Details will be published at launch' },
      { icon: 'spark', title: 'Clear pricing', sub: 'Review rates and fees before applying' },
      { icon: 'chart', title: 'Eligibility', sub: 'Requirements will be confirmed at launch' },
    ]}
    primaryLabel="Contact support"
    primaryIcon="loan"
    note="There is no active Buy Now, Pay Later application in the app yet."
  />
);

export default Bnpl;
