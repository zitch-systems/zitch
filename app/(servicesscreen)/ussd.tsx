import React from 'react';
import FeatureScreen from '@/components/design/FeatureScreen';

/**
 * Zitch USSD banking — the shortcode is still being provisioned with the
 * carriers, so this presents what's coming instead of listing dialable codes.
 * The previous version dialled a "*000#" placeholder (see the old TODO): that
 * code is NOT Zitch's, so tapping "Dial" sent users to whatever their carrier
 * has on *000# — a real misdial, not a demo. Swap this back to a dial-list
 * screen once the real shortcode is live.
 */
const Ussd = () => (
  <FeatureScreen
    title="Zitch USSD"
    icon="phone"
    tagline="Zitch USSD is not available yet. There is no active shortcode to dial from this page."
    points={[
      { icon: 'phone', title: 'Shortcode', sub: 'The official code will be published when available' },
      { icon: 'wallet', title: 'Supported services', sub: 'Available transactions will be confirmed at launch' },
      { icon: 'bills', title: 'Networks and charges', sub: 'Coverage and fees will be published before use' },
    ]}
    primaryLabel="Contact support"
    primaryIcon="chat"
    note="Use the Zitch app or WhatsApp for services currently available on your account."
  />
);

export default Ussd;
