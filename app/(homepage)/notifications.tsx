import React from 'react';
import { router } from 'expo-router';
import { Screen, Header, ZItem } from '@/components/design/ui';
import { useTheme } from '@/lib/theme';

const ITEMS: { icon: string; title: string; sub: string; color: keyof ReturnType<typeof useTheme>['c'] }[] = [
  { icon: 'gift', title: 'You earned ₦120 cashback', sub: 'Bonus from airtime purchase', color: 'lime' },
  { icon: 'loan', title: 'Loan limit increased', sub: 'Your new limit is ₦500,000', color: 'brand' },
  { icon: 'spark', title: 'Daily interest paid', sub: '₦84.20 added to your wallet', color: 'amber' },
  { icon: 'bills', title: 'DSTV due in 2 days', sub: 'Renew Compact Plus to avoid cut-off', color: 'red' },
];

const Notifications = () => {
  const { c } = useTheme();
  return (
    <Screen>
      <Header title="Notifications" onBack={() => router.back()} />
      {ITEMS.map((x, i) => (
        <ZItem
          key={x.title}
          icon={x.icon}
          iconColor={(c as any)[x.color]}
          iconBg={(c as any)[x.color] + '22'}
          title={x.title}
          sub={x.sub}
          last={i === ITEMS.length - 1}
        />
      ))}
    </Screen>
  );
};

export default Notifications;
