import React from 'react';
import FeatureScreen from '@/components/design/FeatureScreen';

const Junior = () => (
  <FeatureScreen
    title="Zitch Junior"
    icon="invite"
    tagline="Zitch Junior is not available yet. Child accounts cannot currently be opened in the app."
    points={[
      { icon: 'wallet', title: 'Account details', sub: 'Features will be confirmed when available' },
      { icon: 'insurance', title: 'Guardian requirements', sub: 'Eligibility and verification terms will be published' },
      { icon: 'help', title: 'Questions', sub: 'Contact support for service availability' },
    ]}
    primaryLabel="Contact support"
    note="There is no active Junior application or waitlist on this page."
  />
);

export default Junior;
