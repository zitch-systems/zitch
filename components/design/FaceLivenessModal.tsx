import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Modal, Platform, Pressable, Text, View, type LayoutChangeEvent } from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as Brightness from 'expo-brightness';
import { WebView, type WebViewMessageEvent } from 'react-native-webview';
import { SafeAreaView } from 'react-native-safe-area-context';
import Svg, { Ellipse, Path } from 'react-native-svg';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import { kycService } from '@/lib/services/kyc';
import apiBaseUrl from '@/components/configFiles/apiConfig';

/** What the live face check hands back: the straight photo the server chose
 * (base64 JPEG) and its 15-minute proof, both sent with the Tier 2 upgrade. */
export type FaceCapture = { image: string; token: string };

/**
 * Tier 2 live face check.
 *
 * First choice is the guided check the WhatsApp face link uses (turn left,
 * turn right, smile), served by our API at /kyc/liveness/ with MediaPipe
 * FaceLandmarker self-hosted there, shown in a WebView. Each move is detected
 * on the device as it happens, and the page hands back the clearest straight
 * frames and the widest point of each turn by postMessage.
 *
 * If that page cannot run here (no network to it, an old WebView, no model,
 * the camera refused inside the WebView) the modal uses expo-camera instead:
 * a countdown, two straight photos and three while the customer turns.
 *
 * Either way the frames go to our server (wallet/face_check.py), which keeps
 * the straight photo the bank can use, checks the turn came from a real,
 * three-dimensional face, and returns that photo's proof. Nothing is stored on
 * the phone. Photos are not mirrored: the bank compares them with the photo on
 * the customer's ID, which is not mirrored either. The screen goes to full
 * brightness while the camera runs, which lights the face in a dim room.
 */
export const LIVENESS_URL = `${apiBaseUrl}/kyc/liveness/`;
const livenessOrigin = (() => {
  try {
    const url = new URL(LIVENESS_URL);
    return url.protocol === 'https:' ? url.origin : '';
  } catch {
    return '';
  }
})();
/** Per frame, the server's own limit (wallet/selfie.py MAX_BASE64_CHARS). */
const MAX_IMAGE_CHARS = 3_900_000;
const MAX_FRAMES = 3;
const MAX_TURNS = 8;
const BASE64 = /^[A-Za-z0-9+/]+={0,2}$/;
/** The page must say hello this soon, and be ready (or give up) this soon. */
export const PAGE_TIMEOUT_MS = 15_000;
export const READY_TIMEOUT_MS = 45_000;

type Mode = 'live' | 'camera';
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

const sameOrigin = (value: string | undefined) => {
  if (!livenessOrigin || !value) return false;
  try {
    return new URL(value).origin === livenessOrigin;
  } catch {
    return false;
  }
};

/** 1..max base64 strings, each within the server's size limit, or null. */
const frameList = (value: unknown, max: number): string[] | null => {
  if (!Array.isArray(value) || value.length < 1 || value.length > max) return null;
  return value.every((item) => typeof item === 'string' && item.length > 0
    && item.length <= MAX_IMAGE_CHARS && BASE64.test(item)) ? value as string[] : null;
};

/** Full screen brightness while `active`, then back to what it was. */
export function useFullBrightness(active: boolean) {
  useEffect(() => {
    if (!active) return;
    let previous: number | null = null;
    // Restoring waits for the raise to settle, so a quick close cannot leave
    // the screen stuck at full brightness.
    const raised = (async () => {
      try {
        previous = await Brightness.getBrightnessAsync();
        await Brightness.setBrightnessAsync(1);
      } catch { /* brightness is a nicety; the check works without it */ }
    })();
    return () => {
      void raised.then(async () => {
        try {
          if (Platform.OS === 'android') await Brightness.restoreSystemBrightnessAsync();
          else if (previous !== null) await Brightness.setBrightnessAsync(previous);
        } catch { /* nothing else to do */ }
      });
    };
  }, [active]);
}

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
  const [mode, setMode] = useState<Mode>(livenessOrigin ? 'live' : 'camera');
  const [ready, setReady] = useState(false);
  const [alive, setAlive] = useState(false);
  const [slow, setSlow] = useState(false);
  const [page, setPage] = useState(0);
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

  useFullBrightness(visible && !!permission?.granted);

  const release = () => {
    if (held.current) {
      held.current = false;
      endExternalActivity();
    }
  };

  const restart = () => {
    flow.current += 1;
    setStep('ready');
    setMessage('');
    setCount(0);
    setReady(false);
    setAlive(false);
    setSlow(false);
  };

  const open = () => {
    if (!held.current) {
      held.current = true;
      beginExternalActivity(); // the app lock must not fire while the camera is up
    }
    restart();
    setMode(livenessOrigin ? 'live' : 'camera');
    if (!permission?.granted) void requestPermission();
  };

  useEffect(() => {
    if (!visible) { release(); flow.current += 1; setCameraReady(false); }
    return release;
  }, [visible]);

  /** The expo-camera capture, when the page cannot run the check here. */
  const toCamera = () => {
    restart();
    setMode('camera');
  };

  // A page that never answers (offline, blocked host) falls back to the camera.
  useEffect(() => {
    if (!visible || mode !== 'live' || !permission?.granted || ready) return;
    // Nothing has been captured while the page is not ready: only the mode changes.
    const timer = setTimeout(() => {
      if (active.current) setMode('camera');
    }, alive ? READY_TIMEOUT_MS : PAGE_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [visible, mode, permission?.granted, ready, alive]);

  const close = () => {
    active.current = false;
    flow.current += 1;
    release();
    onClose();
  };

  /** Our server keeps the clearest straight photo and measures the turn. */
  const verify = async (frames: string[], turns: string[], live: () => boolean) => {
    setMessage('');
    setStep('checking');
    try {
      const result = await kycService.checkTier2Face(frames, turns);
      if (!live()) return;
      const chosen = typeof result.frame === 'number' ? frames[result.frame] : undefined;
      if (result.success && chosen && result.liveness_token) {
        onCapture({ image: chosen, token: result.liveness_token });
        return;
      }
      setMessage(result.message || 'We could not check your face. Please try again.');
    } catch {
      if (!live()) return;
      setMessage('We could not check your face. Please try again.');
    }
    setStep('failed');
  };

  const onMessage = (event: WebViewMessageEvent) => {
    if (!active.current || mode !== 'live' || !sameOrigin(event.nativeEvent.url)) return;
    let data: { type?: unknown; image?: unknown; frames?: unknown; turns?: unknown };
    try {
      data = JSON.parse(event.nativeEvent.data);
    } catch {
      return;
    }
    switch (data?.type) {
      case 'loading': setAlive(true); break;
      case 'ready': setAlive(true); setReady(true); break;
      case 'slow': setSlow(true); break;
      case 'unavailable': toCamera(); break;
      case 'capture': {
        if (step === 'checking') break;
        const frames = frameList(data.frames ?? (typeof data.image === 'string' ? [data.image] : undefined), MAX_FRAMES);
        if (!frames) break;
        const turns = frameList(data.turns, MAX_TURNS);
        // A page without the head-turn frames cannot be checked: use the camera.
        if (!turns) { toCamera(); break; }
        const mine = ++flow.current;
        void verify(frames, turns, () => mine === flow.current && active.current);
        break;
      }
      default: break;
    }
  };

  const tryLiveAgain = () => {
    restart();
    setPage((n) => n + 1); // a fresh page: the moves start again
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
      await verify(frames, turns, live);
    } catch {
      if (!live()) return;
      setMessage('We could not use the camera. Please try again.');
      setStep('failed');
    }
  };

  const live = mode === 'live' && !!permission?.granted;
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
      <SafeAreaView style={{ flex: 1, backgroundColor: live ? '#fff' : MASK }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 16, paddingVertical: 12 }}>
          <Pressable onPress={close} hitSlop={12} accessibilityRole="button" accessibilityLabel="Close face check">
            <ZIcon name="x" size={22} color={INK} stroke={2.2} />
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={{ fontFamily: font.bold, color: INK, fontSize: 15 }}>Live face check</Text>
            <Text style={{ fontFamily: font.regular, color: INK2, fontSize: 12, marginTop: 2 }}>
              {live ? 'Turn left, turn right, then smile' : 'Look straight at the camera, then turn your head'}
            </Text>
          </View>
        </View>

        <View style={{ flex: 1 }} onLayout={onLayout}>
          {live ? (
            <View style={{ flex: 1 }}>
              <WebView
                key={page}
                testID="liveness-web"
                source={{ uri: LIVENESS_URL }}
                onMessage={onMessage}
                onError={() => { if (active.current) toCamera(); }}
                onHttpError={() => { if (active.current) toCamera(); }}
                // Only the liveness page itself; it never navigates anywhere.
                onShouldStartLoadWithRequest={(request: { url: string }) => sameOrigin(request.url)}
                originWhitelist={['https://*']}
                // The camera runs inside the page: inline video, no tap to
                // play, and the app's camera permission passed through for our
                // own host only.
                allowsInlineMediaPlayback
                mediaPlaybackRequiresUserAction={false}
                mediaCapturePermissionGrantType="grantIfSameHostElsePrompt"
                javaScriptEnabled
                setSupportMultipleWindows={false}
                style={{ flex: 1, backgroundColor: '#fff' }}
              />
              {!ready || step === 'checking' || step === 'failed' ? (
                <View
                  pointerEvents={step === 'failed' ? 'auto' : 'none'}
                  style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, alignItems: 'center', justifyContent: 'center', backgroundColor: '#fff', padding: 28, gap: 14 }}
                >
                  {step === 'failed' ? (
                    <>
                      <ZIcon name="help" size={28} color="#B45309" stroke={2} />
                      <Text accessibilityLiveRegion="polite" style={{ fontFamily: font.bold, color: '#B45309', fontSize: 15, lineHeight: 21, textAlign: 'center' }}>
                        {message}
                      </Text>
                      <Pressable
                        onPress={tryLiveAgain}
                        accessibilityRole="button"
                        accessibilityLabel="Try again"
                        style={({ pressed }) => ({ minHeight: 52, alignSelf: 'stretch', borderRadius: 999, backgroundColor: c.brand, alignItems: 'center', justifyContent: 'center', opacity: pressed ? 0.85 : 1 })}
                      >
                        <Text style={{ color: c.inkOnBrand, fontFamily: font.bold, fontSize: 15 }}>Try again</Text>
                      </Pressable>
                    </>
                  ) : (
                    <>
                      <ActivityIndicator color={c.brand} />
                      <Text accessibilityLiveRegion="polite" style={{ fontFamily: font.regular, color: INK3, fontSize: 13 }}>
                        {step === 'checking' ? 'Checking your face…' : 'Getting the face check ready…'}
                      </Text>
                    </>
                  )}
                </View>
              ) : null}
            </View>
          ) : permission?.granted ? (
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

        {live ? (
          <View style={{ paddingHorizontal: 24, paddingBottom: 20, paddingTop: 8, alignItems: 'center', gap: 8 }}>
            {slow && step !== 'checking' ? (
              <Pressable onPress={toCamera} accessibilityRole="button" accessibilityLabel="Use the simple camera instead" hitSlop={8}>
                <Text style={{ color: c.brand, fontFamily: font.bold, fontSize: 13.5, textDecorationLine: 'underline' }}>
                  Having trouble? Use the simple camera instead
                </Text>
              </Pressable>
            ) : null}
            <Text style={{ color: INK3, fontFamily: font.regular, fontSize: 11, textAlign: 'center' }}>
              The face check runs on your phone. Zitch checks the photos for this request only and does not store them.
            </Text>
          </View>
        ) : (
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
        )}
      </SafeAreaView>
    </Modal>
  );
};

export default FaceLivenessModal;
