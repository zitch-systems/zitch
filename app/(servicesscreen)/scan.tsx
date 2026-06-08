import React, { useRef, useState } from 'react';
import { View, Text, Pressable, Platform, Alert } from 'react-native';
import { router } from 'expo-router';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { Screen, Header, Card, Field, Btn } from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';

// Pull a payable destination (10-digit account or 11-digit phone) out of a
// scanned/typed value: a Zitch pay link's query param, or a bare number.
const extractIdentifier = (raw: string): string | null => {
  const value = raw.trim();
  const q = value.match(/[?&](?:account|acct|phone|identifier)=([0-9]{10,11})/i);
  if (q) return q[1];
  const digits = value.replace(/\D/g, '');
  if (digits.length === 10 || digits.length === 11) return digits;
  return null;
};

const Scan = () => {
  const { c } = useTheme();
  const [permission, requestPermission] = useCameraPermissions();
  const [manual, setManual] = useState('');
  const handled = useRef(false); // guard against the camera firing repeatedly

  const useResult = (raw: string) => {
    const id = extractIdentifier(raw);
    if (id) {
      router.replace({ pathname: '/sendmoney', params: { identifier: id } });
    } else {
      handled.current = false; // let them try again
      Alert.alert('Unrecognised code', "That QR doesn't contain a Zitch account or phone number.");
    }
  };

  const onScan = ({ data }: { data: string }) => {
    if (handled.current) return;
    handled.current = true;
    useResult(data);
  };

  const submitManual = () => {
    if (!manual.trim()) return;
    useResult(manual);
  };

  // Web (and any platform without camera support) → manual entry fallback.
  const cameraSupported = Platform.OS === 'ios' || Platform.OS === 'android';

  return (
    <Screen pad={false} scroll={false}>
      <View style={{ paddingHorizontal: 20 }}>
        <Header title="Scan to Pay" sub="Point at a Zitch QR code" onBack={() => router.back()} />
      </View>

      <View style={{ flex: 1, paddingHorizontal: 16 }}>
        {cameraSupported && permission?.granted ? (
          <View style={{ flex: 1, borderRadius: 22, overflow: 'hidden', backgroundColor: '#000' }}>
            <CameraView
              style={{ flex: 1 }}
              facing="back"
              barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
              onBarcodeScanned={onScan}
            />
            {/* viewfinder frame */}
            <View pointerEvents="none" style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, alignItems: 'center', justifyContent: 'center' }}>
              <View style={{ width: 220, height: 220, borderRadius: 24, borderWidth: 3, borderColor: 'rgba(255,255,255,.9)' }} />
            </View>
          </View>
        ) : (
          <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', gap: 16 }}>
            <View style={{ width: 88, height: 88, borderRadius: 28, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
              <ZIcon name="scan" size={40} color={c.brand} />
            </View>
            {cameraSupported ? (
              <>
                <Text style={{ fontSize: 15, color: c.ink2, textAlign: 'center', maxWidth: 280, fontFamily: font.regular }}>
                  Allow camera access to scan a QR code, or enter the account number below.
                </Text>
                <Btn label="Enable camera" icon="scan" full={false} onPress={requestPermission} />
              </>
            ) : (
              <Text style={{ fontSize: 15, color: c.ink2, textAlign: 'center', maxWidth: 280, fontFamily: font.regular }}>
                Scanning needs the Zitch mobile app. Enter the account or phone number below to continue.
              </Text>
            )}
          </View>
        )}

        {/* Manual fallback — always available so the screen is never a dead end. */}
        <Card style={{ marginTop: 14, marginBottom: 18 }}>
          <Text style={{ fontSize: 13, fontFamily: font.bold, color: c.ink1, marginBottom: 10 }}>Or enter account / phone</Text>
          <Field
            value={manual}
            onChangeText={(v) => setManual(v.replace(/\D/g, '').slice(0, 11))}
            keyboardType="number-pad"
            placeholder="10-digit account or 11-digit phone"
            prefix={<ZIcon name="bank" size={18} color={c.ink3} />}
          />
          <View style={{ height: 12 }} />
          <Btn label="Continue" disabled={manual.replace(/\D/g, '').length < 10} onPress={submitManual} />
        </Card>
      </View>
    </Screen>
  );
};

export default Scan;
