import React, { useMemo, useState } from 'react';
import { View, Text, Pressable, Platform } from 'react-native';
import { router } from 'expo-router';
import {
  Screen, Header, Card, Btn, Toggle, PillTabs, SelectRow, PickerSheet, Sheet, Field,
} from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { apiJson } from '@/lib/api';
import { dayLabel, isoDay } from '@/lib/format';

const DAY = 24 * 60 * 60 * 1000;

const FRAMES = [
  { v: 'm1', label: 'Last month' },
  { v: 'm2', label: 'Last 2 months' },
  { v: 'custom', label: 'Custom' },
];

const FILE_TYPES = [
  { v: 'pdf', label: 'PDF document', sub: 'Best for printing, sharing and embassy or visa applications', icon: 'file' },
  { v: 'excel', label: 'Excel spreadsheet', sub: 'Best for bookkeeping — opens in Excel, Numbers or Sheets', icon: 'sheet' },
];

/** t*****o@gmail.com — enough to recognise your own address, not enough to
 *  hand someone shoulder-surfing the screen a login hint. */
const maskEmail = (email: string): string => {
  const [name, domain] = String(email || '').split('@');
  if (!domain) return email || '';
  const head = name.slice(0, 1);
  return `${head}${'*'.repeat(Math.max(1, Math.min(6, name.length - 1)))}@${domain}`;
};

/** Declared outside the screen so it isn't re-created (and re-mounted) on every
 *  render. Android ignores `borderStyle` on a single edge, so the dash comes
 *  from a fully-dashed box clipped down to its top rule. */
const Dashed = ({ line }: { line: string }) => (
  <View style={{ height: 1.5, marginVertical: 18, overflow: 'hidden' }}>
    <View style={{ height: 12, borderWidth: 1.5, borderStyle: 'dashed', borderColor: line, borderRadius: 1 }} />
  </View>
);

const Statement = () => {
  const { c } = useTheme();
  // Read the clock once, on mount. Every date on this screen is relative to
  // "now", and a "now" that moved between renders would let the range the
  // customer read differ from the range actually requested.
  const [now] = useState(() => Date.now());
  const [frame, setFrame] = useState('m1');
  const [fileType, setFileType] = useState('');
  const [showAddress, setShowAddress] = useState(false);
  const [picker, setPicker] = useState(false);
  const [editEmail, setEditEmail] = useState(false);
  const [email, setEmail] = useState('');
  const [draftEmail, setDraftEmail] = useState('');
  const [busy, setBusy] = useState(false);
  // Custom range, held as epoch-ms day boundaries. Only reachable from the
  // "Custom" frame; the presets compute their own range below.
  const [customFrom, setCustomFrom] = useState(() => Date.now() - 30 * DAY);
  const [customTo, setCustomTo] = useState(() => Date.now());
  const [dateEdit, setDateEdit] = useState<null | 'from' | 'to'>(null);
  const [dateDraft, setDateDraft] = useState('');

  React.useEffect(() => {
    // The address the statement will be mailed to is the account's own, not
    // something typed here — this only reads it so the screen can show which
    // inbox to check.
    apiJson('/api/kyc/status/')
      .then((r) => { if (r?.email) setEmail(String(r.email)); })
      .catch(() => {});
  }, []);

  const { from, to } = useMemo(() => {
    if (frame === 'custom') return { from: customFrom, to: customTo };
    return { from: now - (frame === 'm2' ? 60 : 30) * DAY, to: now };
  }, [frame, customFrom, customTo, now]);

  const rangeValid = to >= from;
  const ready = !!fileType && rangeValid && !!email;

  const openDate = (which: 'from' | 'to') => {
    if (frame !== 'custom') return;
    setDateDraft(isoDay(which === 'from' ? customFrom : customTo));
    setDateEdit(which);
  };

  const saveDate = () => {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateDraft.trim());
    if (!m) return notify('Check the date', 'Use the format YYYY-MM-DD, e.g. 2026-07-15.');
    const ts = new Date(+m[1], +m[2] - 1, +m[3]).getTime();
    if (Number.isNaN(ts)) return notify('Check the date', "That date doesn't exist.");
    if (ts > Date.now()) return notify('Check the date', "A statement can't cover the future.");
    if (dateEdit === 'from') setCustomFrom(ts); else setCustomTo(ts);
    setDateEdit(null);
  };

  const submit = async () => {
    if (!ready) return;
    setBusy(true);
    try {
      const res = await apiJson('/api/wallet/statement/request/', {
        from: isoDay(from),
        to: isoDay(to),
        file_type: fileType,
        include_address: showAddress,
        email,
      });
      if (res?.success) {
        notify('On its way', `Your ${fileType === 'pdf' ? 'PDF' : 'Excel'} statement is being prepared and will arrive at ${email} shortly.`);
        router.back();
      } else {
        notify('Not sent', res?.message || "We couldn't prepare that statement. Please try again.");
      }
    } catch {
      notify('Not sent', 'Something went wrong. Please try again later.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen>
      <Header title="Download Statement" onBack={() => router.back()} />

      <Card style={{ marginBottom: 14 }}>
        <Text style={{ fontSize: 15.5, fontFamily: font.bold, color: c.ink1, marginBottom: 14 }}>Time Frame</Text>
        <PillTabs options={FRAMES} value={frame} onChange={setFrame} scroll={false} check />
        <Dashed line={c.line} />
        <View style={{ flexDirection: 'row', gap: 14 }}>
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 13, fontFamily: font.semibold, color: c.ink2, marginBottom: 8 }}>Start Date</Text>
            <Pressable
              onPress={() => openDate('from')}
              disabled={frame !== 'custom'}
              accessibilityRole="button"
              accessibilityLabel={`Start date, ${dayLabel(from)}`}
              style={{ height: 52, borderRadius: 14, backgroundColor: c.surface3, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', gap: 8, opacity: frame === 'custom' ? 1 : 0.75 }}
            >
              <Text style={{ flex: 1, fontSize: 14.5, fontFamily: font.semibold, color: c.ink1 }}>{dayLabel(from)}</Text>
              {frame === 'custom' && <ZIcon name="calendar" size={17} color={c.ink3} />}
            </Pressable>
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 13, fontFamily: font.semibold, color: c.ink2, marginBottom: 8 }}>End Date</Text>
            <Pressable
              onPress={() => openDate('to')}
              disabled={frame !== 'custom'}
              accessibilityRole="button"
              accessibilityLabel={`End date, ${dayLabel(to)}`}
              style={{ height: 52, borderRadius: 14, backgroundColor: c.surface3, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', gap: 8, opacity: frame === 'custom' ? 1 : 0.75 }}
            >
              <Text style={{ flex: 1, fontSize: 14.5, fontFamily: font.semibold, color: c.ink1 }}>{dayLabel(to)}</Text>
              {frame === 'custom' && <ZIcon name="calendar" size={17} color={c.ink3} />}
            </Pressable>
          </View>
        </View>
        {!rangeValid && (
          <Text style={{ fontSize: 12.5, fontFamily: font.semibold, color: c.red, marginTop: 10 }}>
            The end date can&apos;t come before the start date.
          </Text>
        )}
      </Card>

      <Card style={{ marginBottom: 14 }}>
        <Text style={{ fontSize: 15.5, fontFamily: font.bold, color: c.ink1 }}>Email</Text>
        <Text style={{ fontSize: 13, fontFamily: font.regular, color: c.ink3, marginTop: 4, marginBottom: 14 }}>
          Your account statement will be sent to your email.
        </Text>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, height: 52, borderRadius: 14, backgroundColor: c.surface3, paddingHorizontal: 16 }}>
          <Text numberOfLines={1} style={{ flex: 1, fontSize: 14.5, fontFamily: font.semibold, color: email ? c.ink1 : c.ink3 }}>
            {email ? maskEmail(email) : 'No email on your account yet'}
          </Text>
          <Pressable
            onPress={() => { setDraftEmail(email); setEditEmail(true); }}
            accessibilityRole="button"
            accessibilityLabel="Edit email address"
            hitSlop={10}
          >
            <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.brand }}>Edit ›</Text>
          </Pressable>
        </View>
      </Card>

      <Card style={{ marginBottom: 14 }}>
        <Text style={{ fontSize: 15.5, fontFamily: font.bold, color: c.ink1 }}>File Type</Text>
        <Text style={{ fontSize: 13, fontFamily: font.regular, color: c.ink3, marginTop: 4, marginBottom: 14 }}>
          Select the format in which you would like to receive your account statement
        </Text>
        <SelectRow
          value={FILE_TYPES.find((f) => f.v === fileType)?.label}
          placeholder="Select file type"
          onPress={() => setPicker(true)}
          icon={fileType === 'excel' ? 'sheet' : fileType === 'pdf' ? 'file' : undefined}
        />
      </Card>

      <Card style={{ marginBottom: 22, flexDirection: 'row', alignItems: 'center', gap: 14 }}>
        <View style={{ flex: 1 }}>
          <Text style={{ fontSize: 15, fontFamily: font.bold, color: c.ink1 }}>Address shown on statement</Text>
          <Text style={{ fontSize: 12.5, fontFamily: font.regular, color: c.ink3, marginTop: 3 }}>
            Turn this on when the statement is for a visa, loan or landlord.
          </Text>
        </View>
        <Toggle on={showAddress} onChange={setShowAddress} />
      </Card>

      <Btn
        label={busy ? 'Preparing…' : 'Continue'}
        onPress={submit}
        disabled={!ready || busy}
      />
      {!email && (
        <Text style={{ fontSize: 12.5, fontFamily: font.regular, color: c.ink3, textAlign: 'center', marginTop: 10 }}>
          Add an email to your account first — tap Edit above.
        </Text>
      )}

      <PickerSheet
        open={picker}
        onClose={() => setPicker(false)}
        title="Select file type"
        options={FILE_TYPES}
        value={fileType}
        onPick={setFileType}
      />

      <Sheet open={editEmail} onClose={() => setEditEmail(false)} title="Statement email">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 16, fontFamily: font.regular }}>
          We&apos;ll send the statement here. This doesn&apos;t change the email you sign in with.
        </Text>
        <Field
          label="Email address"
          value={draftEmail}
          onChangeText={setDraftEmail}
          keyboardType="email-address"
          placeholder="you@example.com"
          autoComplete="email"
          textContentType="emailAddress"
        />
        <View style={{ height: 18 }} />
        <Btn
          label="Save"
          onPress={() => {
            const v = draftEmail.trim();
            if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v)) {
              return notify('Check the address', "That doesn't look like an email address.");
            }
            setEmail(v);
            setEditEmail(false);
          }}
        />
      </Sheet>

      <Sheet open={!!dateEdit} onClose={() => setDateEdit(null)} title={dateEdit === 'to' ? 'End date' : 'Start date'}>
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 16, fontFamily: font.regular }}>
          Enter the date as YYYY-MM-DD.
        </Text>
        <Field
          label="Date"
          value={dateDraft}
          onChangeText={(v) => setDateDraft(v.replace(/[^\d-]/g, '').slice(0, 10))}
          keyboardType={Platform.OS === 'ios' ? 'numbers-and-punctuation' : 'default'}
          placeholder="2026-07-15"
        />
        <View style={{ height: 18 }} />
        <Btn label="Set date" onPress={saveDate} />
      </Sheet>
    </Screen>
  );
};

export default Statement;
