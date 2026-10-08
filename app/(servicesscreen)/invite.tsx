import React, { useEffect, useRef, useState } from 'react';
import { View, Text, Pressable, Share } from 'react-native';
import { router } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import { LinearGradient } from 'expo-linear-gradient';
import { Screen, Header, Card, Btn, Sheet, HeaderLink, NText } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font, radius, iconTint } from '@/lib/theme';


const INVITE_URL = 'https://zitch.ng';

const STEPS = [
  { icon: 'share', text: 'Share the Zitch website' },
  { icon: 'user', text: 'Friends learn about Zitch' },
  { icon: 'wallet', text: 'Check here for referral updates' },
];

const RULES = [
  'Sharing the Zitch website is available now.',
  'Referral rewards and personal referral codes are not available yet. Invitations are not currently tracked for rewards.',
  'Qualifying activity, reward amounts and payout terms will be published when a referral programme is available.',
];

const HOWTO = [
  'Tap Copy link or Share invite to send the Zitch website to a friend.',
  'Your friend can visit the website to learn about Zitch.',
  'Sharing now does not create a referral reward entitlement.',
];

// Dotted connector between the "how it works" steps. Drawn as dots rather than
// a dashed border: Android renders `borderStyle: 'dashed'` as a solid line on
// rounded/short views.
const Connector = () => {
  const { c } = useTheme();
  return (
    <View style={{ width: 24, height: 54, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
      {[0, 1, 2].map((i) => (
        <View key={i} style={{ width: 4, height: 4, borderRadius: 2, backgroundColor: c.brand, opacity: 0.55 }} />
      ))}
    </View>
  );
};

const SheetList = ({ items }: { items: string[] }) => {
  const { c } = useTheme();
  return (
    <View style={{ gap: 14, paddingBottom: 4 }}>
      {items.map((t, i) => (
        <View key={i} style={{ flexDirection: 'row', gap: 10 }}>
          <View style={{ width: 7, height: 7, borderRadius: 4, marginTop: 6, backgroundColor: c.brand }} />
          <Text style={{ flex: 1, fontSize: 13.5, lineHeight: 20, fontFamily: font.regular, color: c.ink2 }}>{t}</Text>
        </View>
      ))}
    </View>
  );
};

const Invite = () => {
  const { c, theme } = useTheme();
  const dark = theme === 'dark';
  const [copied, setCopied] = useState(false);
  const [rules, setRules] = useState(false);
  const [howto, setHowto] = useState(false);

  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (resetTimer.current) clearTimeout(resetTimer.current); }, []);
  const message = `Explore Zitch: ${INVITE_URL}`;

  const copy = async () => {
    try {
      await Clipboard.setStringAsync(INVITE_URL);
      setCopied(true);
      if (resetTimer.current) clearTimeout(resetTimer.current);
      resetTimer.current = setTimeout(() => setCopied(false), 1600);
      notify('Copied', 'The Zitch website link is on your clipboard.');
    } catch { notify('Could not copy', 'Please try Share invite instead.'); }
  };

  const share = async () => {
    try {
      await Share.share({ message });
    } catch {
      notify('Error', 'Could not open the share sheet.');
    }
  };

  return (
    <Screen>
      <Header
        title="Invite friends"
        onBack={() => router.back()}
        right={<HeaderLink label="Rules" onPress={() => setRules(true)} />}
      />

      {/* ---- Hero: headline + reward "note" ---- */}
      <NText style={{ fontSize: 27, lineHeight: 34, letterSpacing: -0.4, fontFamily: font.extrabold, color: c.ink1 }}>
        {'Share Zitch\nwith your friends'}
      </NText>

      <LinearGradient
        colors={c.heroGradient}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={{ marginTop: 16, borderRadius: radius.lg, padding: 20, overflow: 'hidden' }}
      >
        {/* soft translucent shapes instead of an illustration */}
        <View pointerEvents="none" style={{ position: 'absolute', right: -40, top: -46, width: 150, height: 150, borderRadius: 75, backgroundColor: 'rgba(255,255,255,.09)' }} />
        <View pointerEvents="none" style={{ position: 'absolute', right: 34, bottom: -58, width: 110, height: 110, borderRadius: 55, backgroundColor: 'rgba(255,255,255,.06)' }} />

        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={{ fontSize: 11.5, letterSpacing: 1.4, fontFamily: font.bold, color: 'rgba(255,255,255,.74)' }}>
              REFERRAL REWARD
            </Text>
            <NText style={{ fontSize: 34, lineHeight: 41, marginTop: 4, letterSpacing: -0.6, fontFamily: font.extrabold, color: '#fff' }}>
              Coming soon
            </NText>
            <Text style={{ marginTop: 8, fontSize: 12.5, lineHeight: 18, fontFamily: font.regular, color: 'rgba(255,255,255,.82)' }}>
              Referral rewards are not available yet. Any future programme terms will appear here.
            </Text>
          </View>
          <View style={{ width: 66, height: 66, borderRadius: 33, backgroundColor: 'rgba(255,255,255,.16)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="gift" size={30} color="#fff" stroke={2} />
          </View>
        </View>

        {/* perforation — reads as the stub of a banknote */}
        <View style={{ flexDirection: 'row', gap: 6, marginTop: 18 }}>
          {Array.from({ length: 20 }).map((_, i) => (
            <View key={i} style={{ flex: 1, height: 2, borderRadius: 1, backgroundColor: 'rgba(255,255,255,.3)' }} />
          ))}
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 12 }}>
          <ZIcon name="spark" size={14} color="#fff" stroke={2.2} />
          <Text style={{ flex: 1, fontSize: 12.5, fontFamily: font.semibold, color: 'rgba(255,255,255,.9)' }}>
            Share the Zitch website with a friend.
          </Text>
        </View>
      </LinearGradient>

      {/* ---- Explainer strip ---- */}
      <View
        style={{
          marginTop: 12,
          flexDirection: 'row',
          alignItems: 'center',
          gap: 10,
          borderRadius: radius.md,
          paddingVertical: 12,
          paddingHorizontal: 14,
          backgroundColor: iconTint(c.brand, dark),
        }}
      >
        <ZIcon name="invite" size={17} color={c.brand} stroke={2.2} />
        <Text style={{ flex: 1, fontSize: 12.5, lineHeight: 18, fontFamily: font.regular, color: c.ink2 }}>
          You can share Zitch now. Invitations are not currently tracked for rewards.
        </Text>
      </View>

      {/* ---- How it works ---- */}
      <Card style={{ marginTop: 16 }}>
        <Text style={{ fontSize: 14.5, fontFamily: font.bold, color: c.ink1 }}>How it works</Text>
        <View style={{ flexDirection: 'row', alignItems: 'flex-start', marginTop: 16 }}>
          {STEPS.map((s, i) => (
            <React.Fragment key={s.icon}>
              {i > 0 && <Connector />}
              <View style={{ flex: 1, alignItems: 'center' }}>
                <View style={{ width: 54, height: 54, borderRadius: 27, backgroundColor: iconTint(c.brand, dark), alignItems: 'center', justifyContent: 'center' }}>
                  <ZIcon name={s.icon} size={23} color={c.brand} stroke={2} />
                </View>
                <Text style={{ marginTop: 10, fontSize: 11.5, lineHeight: 16, textAlign: 'center', fontFamily: font.medium, color: c.ink2 }}>
                  {s.text}
                </Text>
              </View>
            </React.Fragment>
          ))}
        </View>
        <View style={{ marginTop: 18 }}>
          <Btn label="Share invite" icon="share" onPress={share} />
        </View>
      </Card>

      {/* ---- Invitation code ---- */}
      <View style={{ marginTop: 24, alignItems: 'center' }}>
        <Text style={{ fontSize: 14.5, fontFamily: font.bold, color: c.ink1 }}>Share the Zitch website</Text>
        <View style={{ alignSelf: 'stretch', flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 12 }}>
          <View
            style={{
              flex: 1,
              height: 52,
              borderRadius: radius.md,
              paddingHorizontal: 12,
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: c.surface3,
            }}
          >
            <NText
              selectable
              numberOfLines={1}
              accessibilityLabel="Zitch website, zitch.ng"
              style={{ fontSize: 20, letterSpacing: 1, fontFamily: font.extrabold, color: c.brand }}
            >
              {INVITE_URL}
            </NText>
          </View>
          <Btn
            label={copied ? 'Copied' : 'Copy link'}
            icon={copied ? 'check' : 'copy'}
            size="md"
            full={false}
            onPress={copy}
          />
        </View>
        <Pressable
          onPress={() => setHowto(true)}
          accessibilityRole="button"
          hitSlop={10}
          style={({ pressed }) => ({ marginTop: 14, opacity: pressed ? 0.6 : 1 })}
        >
          <Text style={{ fontSize: 13.5, fontFamily: font.bold, color: c.brand }}>
            {'How does sharing work?'}
          </Text>
        </Pressable>
      </View>

      {/* The milestone/tier card (reward tiers + expiry countdown) is omitted on
          purpose — there is no referral endpoint supplying tier amounts or an
          expiry date, and inventing them would put fake money on screen. */}

      <Sheet open={rules} onClose={() => setRules(false)} title="Referral rules">
        <SheetList items={RULES} />
      </Sheet>

      <Sheet open={howto} onClose={() => setHowto(false)} title="Sharing Zitch">
        <SheetList items={HOWTO} />
      </Sheet>
    </Screen>
  );
};

export default Invite;
