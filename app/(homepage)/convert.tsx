import React, { useCallback, useState } from 'react';
import { View, Text, Pressable, ActivityIndicator } from 'react-native';
import { useFocusEffect } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import { apiJson } from '@/lib/api';
import { Screen, Card, Field, Naira, NText } from '@/components/design/ui';
import { Label, QuickAmounts } from '@/components/design/flowkit';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';

// Larger presets than airtime — this is a money-figure converter, not a top-up.
const NGN_PRESETS = [1000, 5000, 10000, 50000, 100000, 500000];

type Currency = { code: string; name: string; symbol: string; rate: number };

// Format a foreign-currency value: "$1,234.56".
const fx = (value: number, symbol: string) =>
  `${symbol}${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

// "Mon, 08 Jun 2026 00:02:31 +0000" -> "08 Jun 2026, 00:02 UTC".
const prettyTime = (s: string) =>
  s.replace(/^[A-Za-z]{3},\s*/, '').replace(/(\d{2}:\d{2}):\d{2}\s*\+0000$/, '$1 UTC').replace(/(\d{4})\s/, '$1, ');

const Convert = () => {
  const { c } = useTheme();
  const [amt, setAmt] = useState('');
  const [currencies, setCurrencies] = useState<Currency[]>([]);
  const [updated, setUpdated] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState('');

  const loadRates = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await apiJson('/api/convert/fx/');
      if (res?.success && Array.isArray(res.currencies) && res.currencies.length) {
        setCurrencies(res.currencies.map((r: any) => ({ ...r, rate: Number(r.rate) })));
        setUpdated(typeof res.updated === 'string' ? res.updated : '');
      } else {
        setError(res?.message || "Couldn't load live rates.");
      }
    } catch {
      setError('Network error. Please try again.');
    } finally {
      setLoading(false);
    }
  }, []);

  // Refresh on focus so the displayed rates are current each time the user
  // opens the tab (the server caches, so this is cheap).
  useFocusEffect(
    useCallback(() => {
      loadRates();
    }, [loadRates])
  );

  const amount = Number(amt || 0);

  const copyValue = async (cur: Currency) => {
    await Clipboard.setStringAsync(fx(amount * cur.rate, cur.symbol));
    setCopied(cur.code);
    setTimeout(() => setCopied((prev) => (prev === cur.code ? '' : prev)), 1300);
  };

  return (
    <Screen pad={false} tab>
      <Text style={{ paddingHorizontal: 20, paddingTop: 6, fontSize: 26, fontFamily: font.extrabold, color: c.ink1 }}>Convert</Text>
      <Text style={{ paddingHorizontal: 20, marginTop: 2, fontSize: 13.5, color: c.ink3, fontFamily: font.regular }}>
        Convert Naira to other currencies at live rates
      </Text>

      <View style={{ paddingHorizontal: 20, paddingTop: 18 }}>
        <Label>Amount in Naira</Label>
        <QuickAmounts amounts={NGN_PRESETS} value={amt} onPick={setAmt} />
        <Field
          label="Or enter amount"
          value={amt}
          onChangeText={(v) => setAmt(v.replace(/\D/g, ''))}
          keyboardType="number-pad"
          placeholder="0"
          prefix={<Naira style={{ color: c.ink2, fontSize: 16, fontWeight: '800' }} />}
        />

        {/* converted values */}
        <View style={{ marginTop: 20 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <Label>Converted</Label>
            <Pressable
              onPress={loadRates}
              disabled={loading}
              hitSlop={8}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 5, opacity: loading ? 0.5 : 1 }}
            >
              <ZIcon name="convert" size={13} color={c.brand} />
              <Text style={{ fontSize: 12.5, fontFamily: font.semibold, color: c.brand }}>Refresh</Text>
            </Pressable>
          </View>

          {loading ? (
            <Card style={{ alignItems: 'center', paddingVertical: 28 }}>
              <ActivityIndicator color={c.brand} />
              <Text style={{ marginTop: 10, fontSize: 13, color: c.ink3, fontFamily: font.regular }}>Fetching live rates…</Text>
            </Card>
          ) : error ? (
            <Card style={{ alignItems: 'center', paddingVertical: 24 }}>
              <Text style={{ fontSize: 13.5, color: c.ink2, fontFamily: font.semibold, textAlign: 'center' }}>{error}</Text>
              <Pressable onPress={loadRates} style={{ marginTop: 12, paddingVertical: 9, paddingHorizontal: 20, borderRadius: 999, backgroundColor: 'rgba(15,162,149,.12)' }}>
                <Text style={{ color: c.brand, fontFamily: font.bold, fontSize: 13 }}>Retry</Text>
              </Pressable>
            </Card>
          ) : (
            <Card pad={0} style={{ paddingHorizontal: 16 }}>
              {currencies.map((cur, i) => {
                const isCopied = copied === cur.code;
                return (
                  <Pressable
                    key={cur.code}
                    onPress={() => amount > 0 && copyValue(cur)}
                    style={({ pressed }) => ({
                      flexDirection: 'row',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      paddingVertical: 15,
                      borderBottomWidth: i === currencies.length - 1 ? 0 : 1,
                      borderBottomColor: c.line,
                      opacity: pressed && amount > 0 ? 0.6 : 1,
                    })}
                  >
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
                      <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
                        <Text style={{ fontSize: 17, fontFamily: font.extrabold, color: c.brand }}>{cur.symbol}</Text>
                      </View>
                      <View>
                        <Text style={{ fontSize: 14.5, fontFamily: font.bold, color: c.ink1 }}>{cur.code} · {cur.name}</Text>
                        <NText style={{ fontSize: 11.5, color: c.ink3, fontFamily: font.regular, marginTop: 1 }}>
                          {cur.rate > 0 ? `${cur.symbol}1 ≈ ₦${(1 / cur.rate).toLocaleString('en-US', { maximumFractionDigits: 0 })}` : cur.name}
                        </NText>
                      </View>
                    </View>
                    {isCopied ? (
                      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
                        <ZIcon name="check" size={15} color={c.brand} stroke={2.6} />
                        <Text style={{ fontSize: 13, fontFamily: font.bold, color: c.brand }}>Copied</Text>
                      </View>
                    ) : (
                      <Text
                        style={{
                          fontSize: 18,
                          fontFamily: font.extrabold,
                          color: amount > 0 ? c.ink1 : c.ink3,
                          fontVariant: ['tabular-nums'],
                        }}
                      >
                        {fx(amount * cur.rate, cur.symbol)}
                      </Text>
                    )}
                  </Pressable>
                );
              })}
            </Card>
          )}

          {!loading && !error && (
            <Text style={{ marginTop: 12, fontSize: 11.5, color: c.ink3, fontFamily: font.regular, textAlign: 'center' }}>
              {amount > 0 ? 'Tap a value to copy · ' : ''}Mid-market rates{updated ? ` · ${prettyTime(updated)}` : ''}
            </Text>
          )}
        </View>
      </View>
    </Screen>
  );
};

export default Convert;
