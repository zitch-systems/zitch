import React, { useCallback, useState } from 'react';
import { View, Text, ActivityIndicator, Pressable, RefreshControl, ScrollView } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken } from '@/lib/secureStore';
import { Screen, Header, Btn, money } from '@/components/design/ui';
import { Hero, SectionLabel } from '@/components/design/widgets';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';

type Plan = {
  reference: string;
  principal: string;
  interest: string;
  rate: string;
  duration_days: number;
  maturity_value: string;
  status: 'active' | 'matured';
  matures_at: string;
};

// Whole days from today until an ISO date (negative once the date has passed).
const daysUntil = (iso: string) => {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return 0;
  return Math.ceil((d.getTime() - Date.now()) / 86_400_000);
};

const pct = (rate: string) => `${(Number(rate) * 100).toFixed(0)}% p.a`;

const StatusPill = ({ matured }: { matured: boolean }) => {
  const { c } = useTheme();
  const color = matured ? c.lime : c.brand;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 5, borderRadius: 999, backgroundColor: `${color}1A` }}>
      <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: color }} />
      <Text style={{ fontSize: 11.5, fontFamily: font.bold, color }}>{matured ? 'Paid out' : 'Active'}</Text>
    </View>
  );
};

const Row = ({ k, v, strong }: { k: string; v: string; strong?: boolean }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 9, borderTopWidth: 1, borderTopColor: c.line }}>
      <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.regular }}>{k}</Text>
      <Text style={{ fontSize: strong ? 15 : 13.5, fontFamily: strong ? font.extrabold : font.semibold, color: c.ink1, fontVariant: ['tabular-nums'] }}>{v}</Text>
    </View>
  );
};

const PlanCard = ({ plan }: { plan: Plan }) => {
  const { c } = useTheme();
  const matured = plan.status === 'matured';
  const left = daysUntil(plan.matures_at);
  const progress = matured ? 1 : Math.min(1, Math.max(0, (plan.duration_days - Math.max(0, left)) / plan.duration_days));
  const matureLine = matured
    ? 'Matured'
    : left <= 0
      ? 'Maturing today'
      : `${plan.matures_at} · ${left} day${left === 1 ? '' : 's'} left`;

  return (
    <View style={{ borderRadius: 18, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, padding: 16, marginBottom: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <View style={{ width: 42, height: 42, borderRadius: 13, backgroundColor: 'rgba(15,162,149,.16)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name={matured ? 'check' : 'lock'} size={20} color={c.brand} />
          </View>
          <View>
            <Text style={{ fontFamily: font.bold, fontSize: 15, color: c.ink1 }}>Fixed Save</Text>
            <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>{pct(plan.rate)} · {plan.duration_days} days</Text>
          </View>
        </View>
        <StatusPill matured={matured} />
      </View>

      <View style={{ marginTop: 14, marginBottom: 4 }}>
        <View style={{ height: 6, borderRadius: 4, backgroundColor: c.surface3, overflow: 'hidden' }}>
          <View style={{ width: `${Math.round(progress * 100)}%`, height: '100%', backgroundColor: matured ? c.lime : c.brand }} />
        </View>
      </View>

      <Row k="Principal" v={money(Number(plan.principal))} />
      <Row k={matured ? 'Status' : 'Matures'} v={matureLine} />
      <Row k={matured ? 'Paid to wallet' : 'You get back'} v={money(Number(plan.maturity_value))} strong />
    </View>
  );
};

const MySavings = () => {
  const { c } = useTheme();
  const [plans, setPlans] = useState<Plan[]>([]);
  const [totalLocked, setTotalLocked] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    const token = await getToken();
    if (!token) {
      setLoading(false);
      return;
    }
    try {
      const res = await fetch(`${baseUrl}/api/savings/list/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: token }),
      }).then((r) => r.json());
      if (Array.isArray(res?.plans)) {
        setPlans(res.plans);
        setTotalLocked(Number(res.total_locked ?? 0));
      }
    } catch {
      // keep last-known state; the screen stays usable offline
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const onRefresh = () => { setRefreshing(true); load(); };

  const activeCount = plans.filter((p) => p.status === 'active').length;
  const topRate = plans.reduce((m, p) => Math.max(m, Number(p.rate)), 0);

  const header = (
    <Header
      title="My Fixed Saves"
      sub="Locked & earning"
      onBack={() => router.back()}
      right={
        <Pressable
          onPress={() => router.push('/fixedsave')}
          style={{ width: 42, height: 42, borderRadius: 13, backgroundColor: c.brand, alignItems: 'center', justifyContent: 'center' }}
        >
          <ZIcon name="plus" size={20} color="#fff" stroke={2.4} />
        </Pressable>
      }
    />
  );

  if (loading) {
    return (
      <Screen scroll={false}>
        {header}
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
          <ActivityIndicator color={c.brand} />
        </View>
      </Screen>
    );
  }

  return (
    <Screen scroll={false}>
      {header}
      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={{ paddingBottom: 28 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={c.brand} colors={[c.brand]} />}
      >
        <Hero style={{ marginBottom: 18 }}>
          <Text style={{ fontSize: 13, color: 'rgba(255,255,255,.85)', fontFamily: font.regular }}>Total locked</Text>
          <Text style={{ fontSize: 32, fontFamily: font.extrabold, color: '#fff', marginTop: 4, fontVariant: ['tabular-nums'] }}>{money(totalLocked)}</Text>
          <Text style={{ fontSize: 12.5, color: 'rgba(255,255,255,.85)', marginTop: 6, fontFamily: font.regular }}>
            {activeCount > 0
              ? `${activeCount} active plan${activeCount === 1 ? '' : 's'}${topRate > 0 ? ` · up to ${(topRate * 100).toFixed(0)}% p.a` : ''}`
              : 'Lock funds to start earning up to 22% p.a'}
          </Text>
        </Hero>

        {plans.length === 0 ? (
          <View style={{ alignItems: 'center', paddingVertical: 40, paddingHorizontal: 24 }}>
            <View style={{ width: 64, height: 64, borderRadius: 20, backgroundColor: c.surface3, alignItems: 'center', justifyContent: 'center', marginBottom: 16 }}>
              <ZIcon name="fixed" size={28} color={c.brand} />
            </View>
            <Text style={{ fontSize: 16, fontFamily: font.bold, color: c.ink1, marginBottom: 6 }}>No savings yet</Text>
            <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.regular, textAlign: 'center', marginBottom: 20 }}>
              Lock funds for a fixed period and earn up to 22% p.a. Your money stays safe until it matures.
            </Text>
            <Btn label="Start saving" icon="fixed" onPress={() => router.push('/fixedsave')} full={false} />
          </View>
        ) : (
          <>
            <SectionLabel>Your plans</SectionLabel>
            {plans.map((p) => <PlanCard key={p.reference} plan={p} />)}
          </>
        )}
      </ScrollView>
    </Screen>
  );
};

export default MySavings;
