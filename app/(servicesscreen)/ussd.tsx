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
    tagline="Bank without internet — check your balance, buy airtime and send money from any phone, on every network."
    points={[
      { icon: 'wallet', title: 'Check wallet balance', sub: 'No data or smartphone needed' },
      { icon: 'airtime', title: 'Buy airtime & data', sub: 'For yourself or anyone else' },
      { icon: 'send', title: 'Send money', sub: 'Transfers to any Nigerian bank' },
      { icon: 'bills', title: 'Pay bills', sub: 'Electricity, TV and more' },
    ]}
    primaryLabel="Chat with us"
    primaryIcon="chat"
    note="Our USSD shortcode is being registered with the networks. Standard carrier USSD charges will apply once live."
  />
);

export default Ussd;
