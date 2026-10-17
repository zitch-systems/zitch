import React from 'react';
import FeatureScreen from '@/components/design/FeatureScreen';

const Insurance = () => (
  <FeatureScreen
    title="Insurance"
    icon="insurance"
    tagline="Insurance is not available in Zitch yet. Providers, cover options and prices will be confirmed before you can apply."
    points={[
      { icon: 'insurance', title: 'Cover options', sub: 'Supported policies will be listed when available' },
      { icon: 'card', title: 'Pricing and exclusions', sub: 'Review the full policy terms before applying' },
      { icon: 'help', title: 'Questions', sub: 'Contact support for service availability' },
    ]}
    primaryLabel="Contact support"
    note="No insurance policy or quote is issued from this page."
  />
);

export default Insurance;
