import React from 'react';
import FeatureScreen from '@/components/design/FeatureScreen';

const Remita = () => (
  <FeatureScreen
    title="Pay with Remita"
    icon="remita"
    tagline="Settle any Remita bill — government, school fees, utilities — with your Remita Retrieval Reference (RRR)."
    points={[
      { icon: 'bills', title: 'Any RRR', sub: 'Enter the reference, we handle the rest' },
      { icon: 'check', title: 'Instant receipt', sub: 'Confirmation you can share' },
      { icon: 'wallet', title: 'Pay from wallet', sub: 'No card or bank app needed' },
    ]}
    primaryLabel="Talk to us"
    note="Remita biller payments are being connected. Contact support to make a payment in the meantime."
  />
);

export default Remita;
