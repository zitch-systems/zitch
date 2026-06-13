import React from 'react';
import { router } from 'expo-router';
import { Screen, Header, ZItem } from '@/components/design/ui';

const Utility = () => {
  const items = [
    { icon: 'bills', title: 'Electricity', sub: 'Pay your electricity bill in seconds', to: '/buyelectricity' },
    { icon: 'tv', title: 'TV Subscriptions', sub: 'Renew DSTV, GOtv & StarTimes', to: '/buycable' },
    { icon: 'bills', title: 'Water', sub: 'Pay your water utility bill', to: '/water' },
  ];
  return (
    <Screen>
      <Header title="Utility Payment" sub="Seamless utility payments with Zitch" onBack={() => router.back()} />
      {items.map((it, i) => (
        <ZItem key={it.title} icon={it.icon} title={it.title} sub={it.sub} onPress={() => router.push(it.to as any)} last={i === items.length - 1} />
      ))}
    </Screen>
  );
};

export default Utility;
