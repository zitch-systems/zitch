import React, { useCallback, useEffect } from 'react';
import { View, Text } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { Loading } from '@/components/design/Loading';
import { Screen, Header, ZItem, money } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';
import { transactionParams, useWallet } from '@/lib/wallet';
import { clearPendingNotificationOpen } from '@/lib/notifications';

/**
 * Notifications = the user's REAL account activity (credits, purchases,
 * transfers) from the wallet context — never a canned feed. There is no
 * backend notifications endpoint yet, so fabricated "cashback / loan limit /
 * interest" items would show every user identical fake money events; showing
 * real transactions keeps the bell honest until a push-notification feed
 * exists server-side.
 */
const Notifications = () => {
  const { c } = useTheme();
  const { txns, loading, hydrated, historyError, reload } = useWallet();

  useEffect(() => {
    clearPendingNotificationOpen().catch(() => {});
  }, []);

  useFocusEffect(useCallback(() => { void reload(); }, [reload]));

  return (
    <Screen tab refreshing={loading && hydrated} onRefresh={() => { void reload(); }}>
      <Header title="Notifications" sub="Activity on your account" onBack={() => router.back()} />
      {historyError ? <Text accessibilityRole="alert" style={{ color: c.amber, marginBottom: 12 }}>{historyError} Pull down to retry.</Text> : null}
      {!hydrated && txns.length === 0 ? (
        <View style={{ paddingVertical: 48 }}>
          <Loading full={false} />
        </View>
      ) : txns.length === 0 ? (
        <View style={{ alignItems: 'center', paddingVertical: 56, paddingHorizontal: 24 }}>
          <View style={{ width: 84, height: 84, borderRadius: 26, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="bell" size={38} color={c.brand} />
          </View>
          <Text style={{ fontSize: 17, fontFamily: font.extrabold, color: c.ink1, marginTop: 18 }}>{historyError ? 'Activity unavailable' : 'Nothing here yet'}</Text>
          <Text style={{ fontSize: 13.5, color: c.ink3, marginTop: 6, textAlign: 'center', maxWidth: 280, fontFamily: font.regular }}>
            Money in, purchases and transfers will show up here as they happen.
          </Text>
        </View>
      ) : (
        txns.map((x, i) => (
          <ZItem
            key={x.id}
            icon={x.icon}
            title={`${x.type} · ${(x.dir === 'in' ? '+' : '-') + money(Math.abs(x.amount))}`}
            sub={[x.status, x.detail].filter(Boolean).join(' · ')}
            last={i === txns.length - 1}
            onPress={() =>
              router.push({
                pathname: '/txndetail',
                params: transactionParams(x),
              })
            }
          />
        ))
      )}
    </Screen>
  );
};

export default Notifications;
