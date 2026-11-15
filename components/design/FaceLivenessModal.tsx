import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Modal, Platform, Pressable, Text, View, type LayoutChangeEvent } from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { SafeAreaView } from 'react-native-safe-area-context';
import Svg, { Ellipse, Path } from 'react-native-svg';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import { kycService } from '@/lib/services/kyc';

/** What the live face check hands back: the straight photo the server chose
 * (base64 JPEG) and its 15-minute proof, both sent with the Tier 2 upgrade. */
export type FaceCapture = { image: string; token: string };

type Step = 'ready' | 'countdown' | 'straight' | 'turn' | 'checking' | 'failed';

const COUNTDOWN = 3;
const STRAIGHT_FRAMES = 2;
const TURN_FRAMES = 3;
const TURN_LEAD_MS = 650;
// The screen around the oval stays light in dark mode too: it is the light.
const MASK = 'rgba(248,250,247,0.92)';
const INK = '#142621';
const INK2 = '#40524A';
const INK3 = '#5D6F66';

const sleep = (ms: number) => new Promise<void>((resolve) => { setTimeout(resolve, ms); });

/**
 * The Android picture size to use: 4:3 and close to the 1280 px the bank
 * receives. Left alone, CameraX takes the sensor's full 12-50 MP, which is slow
 * to capture and process and far too large to upload. iOS sizes are presets and
 * its default is already about 1080p, so only Android sizes are picked.
 */
export const pickPictureSize = (sizes: string[]): string | undefined => {
  const parsed = sizes
    .map((size) => /^(\d+)x(\d+)$/.exec(size))
    .filter((match): match is RegExpExecArray => !!match)
    .map((match) => {
      const w = Number(match[1]); const h = Number(match[2]);
      return { size: match[0], long: Math.max(w, h), ratio: Math.max(w, h) / Math.min(w, h) };
    });
  if (!parsed.length) return undefined;
  const near = parsed.filter((p) => p.long >= 1200 && p.long <= 2000);
  if (near.length) {
    const fourThree = near.filter((p) => Math.abs(p.ratio - 4 / 3) < 0.05);
    const pool = fourThree.length ? fourThree : near;
    return pool.sort((a, b) => Math.abs(a.long - 1280) - Math.abs(b.long - 1280))[0].size;
  }
  const larger = parsed.filter((p) => p.long > 2000).sort((a, b) => a.long - b.long);
  return (larger[0] || parsed.sort((a, b) => b.long - a.long)[0]).size;
};

const GUIDE: Record<Step, string> = {
  ready: 'Fit your face inside the oval, in good light.',
  countdown: 'Look straight at the camera and hold still.',
  straight: 'Hold still…',
  turn: 'Now slowly turn your head to your left  ←',
  checking: 'Checking your face…',
  failed: '',
};

/**
 * Tier 2 live face check (in-app camera, expo-camera).
 *
 * The customer fills the oval; a countdown, then two straight photos and three
 * while they turn their head. The server picks the clearest straight photo the
 * bank can use and checks the turn came from a real, three-dimensional face
 * (wallet/face_check.py), returning that photo's proof. Nothing is stored on
 * the phone. Photos are not mirrored: the bank compares them with the photo on
 * the customer's ID, which is not mirrored either. The light mask around the
 * oval also lights the face in a dim room.
 */
const FaceLivenessModal = ({
  visible,
  onClose,
  onCapture,
}: {
  visible: boolean;
  onClose: () => void;
  onCapture: (capture: FaceCapture) => void;
}) => {
  const { c } = useTheme();
  const camera = useRef<React.ElementRef<typeof CameraView>>(null);
  const [permission, requestPermission] = useCameraPermissions();
  const [step, setStep] = useState<Step>('ready');
  const [count, setCount] = useState(0);
  const [message, setMessage] = useState('');
  const [cameraReady, setCameraReady] = useState(false);
  const [pictureSize, setPictureSize] = useState<string | undefined>(undefined);
  const [area, setArea] = useState({ width: 0, height: 0 });
  const held = useRef(false);
  const flow = useRef(0);
  const sized = useRef(false);
  const active = useRef(visible);
  active.current = visible;
  useEffect(() => () => { active.current = false; flow.current += 1; }, []);

  const release = () => {
    if (held.current) {
      held.current = false;
      endExternalActivity();
    }
  };

  const open = () => {
    if (!held.current) {
      held.current = true;
      beginExternalActivity(); // the app lock must not fire while the camera is up
    }
    flow.current += 1;
    setStep('ready');
    setMessage('');
    setCount(0);
    if (!permission?.granted) void requestPermission();
  };

  useEffect(() => {
    if (!visible) { release(); flow.current += 1; setCameraReady(false); }
    return release;
  }, [visible]);

  const close = () => {
    active.current = false;
    flow.current += 1;
    release();
    onClose();
  };

  const onCameraReady = () => {
    setCameraReady(true);
    if (Platform.OS !== 'android' || sized.current || !camera.current) return;
    sized.current = true;
    camera.current.getAvailablePictureSizesAsync()
      .then((sizes) => { const size = pickPictureSize(sizes); if (size && active.current) setPictureSize(size); })
      .catch(() => undefined);
  };

  const shoot = async (quality: number) => {
    const photo = await camera.current?.takePictureAsync({
      base64: true, quality, exif: false, shutterSound: false, skipProcessing: false,
    });
    if (!photo?.base64) throw new Error('The camera did not return an image.');
    return photo.base64;
  };

  const run = async () => {
    if ((step !== 'ready' && step !== 'failed') || !cameraReady || !permission?.granted) return;
    const mine = ++flow.current;
    const live = () => mine === flow.current && active.current;
    setMessage('');
    try {
      for (let n = COUNTDOWN; n > 0; n -= 1) {
        setCount(n);
        setStep('countdown');
        await sleep(800);
        if (!live()) return;
      }
      setCount(0);
      setStep('straight');
      const frames: string[] = [];
      for (let i = 0; i < STRAIGHT_FRAMES; i += 1) {
        frames.push(await shoot(0.85));
        if (!live()) return;
      }
      setStep('turn');
      await sleep(TURN_LEAD_MS);
      if (!live()) return;
      const turns: string[] = [];
      for (let i = 0; i < TURN_FRAMES; i += 1) {
        turns.push(await shoot(0.5));
        if (!live()) return;
      }
      setStep('checking');
      const result = await kycService.checkTier2Face(frames, turns);
      if (!live()) return;
      const chosen = typeof result.frame === 'number' ? frames[result.frame] : undefined;
      if (result.success && chosen && result.liveness_token) {
        onCapture({ image: chosen, token: result.liveness_token });
        return;
      }
      setMessage(result.message || 'We could not check your face. Please try again.');
      setStep('failed');
    } catch {
      if (!live()) return;
      setMessage('We could not use the camera. Please try again.');
      setStep('failed');
    }
  };

  const busy = step === 'countdown' || step === 'straight' || step === 'turn' || step === 'checking';
  const onLayout = (event: LayoutChangeEvent) => {
    const { width, height } = event.nativeEvent.layout;
    setArea({ width, height });
  };
  // The oval: the face fills it at a distance where the photo suits the bank.
  const rx = area.width * 0.36;
  const ry = Math.min(rx * 1.32, area.height * 0.4);
  const cx = area.width / 2;
  const cy = area.height * 0.43;
  const ring = step === 'failed' ? '#D97706' : busy ? '#1F9D55' : c.brand;
  const guide = step === 'failed' ? message : GUIDE[step];

  return (
    <Modal visible={visible} animationType="slide" onShow={open} onRequestClose={close}>
      <SafeAreaView style={{ flex: 1, backgroundColor: MASK }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 16, paddingVertical: 12 }}>
          <Pressable onPress={close} hitSlop={12} accessibilityRole="button" accessibilityLabel="Close face check">
            <ZIcon name="x" size={22} color={INK} stroke={2.2} />
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={{ fontFamily: font.bold, color: INK, fontSize: 15 }}>Live face check</Text>
            <Text style={{ fontFamily: font.regular, color: INK2, fontSize: 12, marginTop: 2 }}>
              Look straight at the camera, then turn your head
            </Text>
          </View>
        </View>

        <View style={{ flex: 1 }} onLayout={onLayout}>
          {permission?.granted ? (
            <>
              <CameraView
                ref={camera}
                style={{ flex: 1 }}
                facing="front"
                animateShutter={false}
                pictureSize={pictureSize}
                onCameraReady={onCameraReady}
                onMountError={() => { setMessage('The camera could not start. Close this and try again.'); setStep('failed'); }}
              />
              {area.width > 0 && (
                <Svg pointerEvents="none" width={area.width} height={area.height} style={{ position: 'absolute', top: 0, left: 0 }}>
                  <Path
                    fill={MASK}
                    fillRule="evenodd"
                    d={`M0 0H${area.width}V${area.height}H0Z M${cx - rx} ${cy}a${rx} ${ry} 0 1 0 ${2 * rx} 0a${rx} ${ry} 0 1 0 ${-2 * rx} 0Z`}
                  />
                  <Ellipse cx={cx} cy={cy} rx={rx} ry={ry} fill="none" stroke={ring} strokeWidth={4} />
                </Svg>
              )}
              {count > 0 && (
                <View pointerEvents="none" style={{ position: 'absolute', left: 0, right: 0, top: cy - 40, alignItems: 'center' }}>
                  <Text accessibilityElementsHidden importantForAccessibility="no" style={{ fontFamily: font.extrabold, fontSize: 64, color: '#fff', textShadowColor: 'rgba(0,0,0,.45)', textShadowRadius: 10 }}>
                    {count}
                  </Text>
                </View>
              )}
              <View pointerEvents="none" style={{ position: 'absolute', left: 20, right: 20, top: cy + ry + 14, alignItems: 'center', gap: 8 }}>
                {step === 'checking' && <ActivityIndicator color={c.brand} />}
                <Text accessibilityLiveRegion="polite" style={{ fontFamily: font.bold, fontSize: 15, lineHeight: 21, textAlign: 'center', color: step === 'failed' ? '#B45309' : INK }}>
                  {guide}
                </Text>
              </View>
            </>
          ) : permission?.canAskAgain !== false ? (
            <View style={{ flex: 1, padding: 32, alignItems: 'center', justifyContent: 'center', gap: 14 }}>
              <ZIcon name="camera" size={32} color={INK} stroke={2} />
              <Text style={{ color: INK, textAlign: 'center', fontFamily: font.regular, fontSize: 13 }}>
                Allow camera access to take the live face check.
              </Text>
              <Pressable accessibilityRole="button" onPress={() => void requestPermission()} style={{ paddingHorizontal: 18, paddingVertical: 12, borderRadius: 14, backgroundColor: c.brand }}>
                <Text style={{ color: c.inkOnBrand, fontFamily: font.bold }}>Enable camera</Text>
              </Pressable>
            </View>
          ) : (
            <View style={{ flex: 1, padding: 32, alignItems: 'center', justifyContent: 'center', gap: 12 }}>
              <ZIcon name="help" size={28} color={INK} stroke={2} />
              <Text style={{ color: INK, textAlign: 'center', fontFamily: font.regular, fontSize: 13 }}>
                Camera access is off. Enable it in Settings to continue.
              </Text>
            </View>
          )}
        </View>

        <View style={{ paddingHorizontal: 24, paddingBottom: 24, paddingTop: 10, gap: 12 }}>
          <Text style={{ color: INK2, fontFamily: font.regular, fontSize: 12, textAlign: 'center', lineHeight: 17 }}>
            Face a window or a lamp · No glasses, cap or face covering · Phone at eye level
          </Text>
          <Pressable
            onPress={() => void run()}
            disabled={!permission?.granted || !cameraReady || busy}
            accessibilityRole="button"
            accessibilityLabel={step === 'failed' ? 'Try again' : 'Start face check'}
            accessibilityState={{ disabled: !permission?.granted || !cameraReady || busy }}
            style={({ pressed }) => ({ minHeight: 52, borderRadius: 999, backgroundColor: c.brand, alignItems: 'center', justifyContent: 'center', opacity: !permission?.granted || !cameraReady || busy ? 0.55 : pressed ? 0.85 : 1 })}
          >
            {busy ? <ActivityIndicator color={c.inkOnBrand} /> : (
              <Text style={{ color: c.inkOnBrand, fontFamily: font.bold, fontSize: 15 }}>
                {step === 'failed' ? 'Try again' : 'Start face check'}
              </Text>
            )}
          </Pressable>
          <Text style={{ color: INK3, fontFamily: font.regular, fontSize: 11, textAlign: 'center' }}>
            Your photos are used only for this check and are not stored by Zitch.
          </Text>
        </View>
      </SafeAreaView>
    </Modal>
  );
};

export default FaceLivenessModal;
