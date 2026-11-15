import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Modal, Platform, Pressable, Text, View } from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as Brightness from 'expo-brightness';
import { WebView, type WebViewMessageEvent } from 'react-native-webview';
import { SafeAreaView } from 'react-native-safe-area-context';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import apiBaseUrl from '@/components/configFiles/apiConfig';

/**
 * In-app selfie for the combined Wema Tier-2 upgrade, as an active liveness check.
 *
 * The customer turns their head left, then right, then smiles; each move is
 * detected on the device and the best frontal frame becomes the selfie. The
 * check is the SAME page the WhatsApp face link uses (served by our API at
 * /kyc/liveness/, MediaPipe FaceLandmarker self-hosted there), shown in a
 * WebView that hands the frame back by postMessage. No extra native module
 * beyond expo-brightness, which lights the face by raising the screen to full
 * brightness while the camera runs (and restores it after).
 *
 * The bank remains the decision boundary: it matches the photo to the BVN/NIN
 * record and its read-back raises the tier. If the check cannot run here (no
 * network to the page, an old WebView, no model, camera refused inside the
 * WebView) the modal falls back to the plain front-camera capture below, so
 * nobody is stuck.
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
/** A JPEG of at most 720 px is ~100-300 KB; refuse anything far outside that. */
const MAX_IMAGE_CHARS = 4_000_000;
const BASE64 = /^[A-Za-z0-9+/]+={0,2}$/;
/** The page must say hello this soon, and be ready (or give up) this soon. */
export const PAGE_TIMEOUT_MS = 15_000;
export const READY_TIMEOUT_MS = 45_000;

const sameOrigin = (value: string | undefined) => {
  if (!livenessOrigin || !value) return false;
  try {
    return new URL(value).origin === livenessOrigin;
  } catch {
    return false;
  }
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

type Mode = 'live' | 'camera';

const FaceLivenessModal = ({
  visible,
  onClose,
  onCapture,
}: {
  visible: boolean;
  onClose: () => void;
  onCapture: (base64: string) => void;
}) => {
  const { c } = useTheme();
  const camera = useRef<React.ElementRef<typeof CameraView>>(null);
  const [permission, requestPermission] = useCameraPermissions();
  const [mode, setMode] = useState<Mode>(livenessOrigin ? 'live' : 'camera');
  const [ready, setReady] = useState(false);
  const [alive, setAlive] = useState(false);
  const [slow, setSlow] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [captureError, setCaptureError] = useState('');
  const held = useRef(false);
  const capturingRef = useRef(false);
  const active = useRef(visible);
  active.current = visible;
  useEffect(() => () => { active.current = false; }, []);

  useFullBrightness(visible && !!permission?.granted);

  const release = () => {
    if (held.current) {
      held.current = false;
      endExternalActivity();
    }
  };

  const open = () => {
    if (!held.current) {
      held.current = true;
      beginExternalActivity();
    }
    setMode(livenessOrigin ? 'live' : 'camera');
    setReady(false);
    setAlive(false);
    setSlow(false);
    setCaptureError('');
    setCapturing(false);
    if (!permission?.granted) void requestPermission();
  };

  useEffect(() => {
    if (!visible) release();
    return release;
  }, [visible]);

  // A page that never answers (offline, blocked host) falls back to the camera.
  useEffect(() => {
    if (!visible || mode !== 'live' || !permission?.granted || ready) return;
    const timer = setTimeout(() => {
      if (active.current) setMode('camera');
    }, alive ? READY_TIMEOUT_MS : PAGE_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [visible, mode, permission?.granted, ready, alive]);

  const close = () => {
    active.current = false;
    release();
    onClose();
  };

  const onMessage = (event: WebViewMessageEvent) => {
    if (!active.current || !sameOrigin(event.nativeEvent.url)) return;
    let message: { type?: unknown; image?: unknown };
    try {
      message = JSON.parse(event.nativeEvent.data);
    } catch {
      return;
    }
    switch (message?.type) {
      case 'loading': setAlive(true); break;
      case 'ready': setAlive(true); setReady(true); break;
      case 'slow': setSlow(true); break;
      case 'unavailable': setMode('camera'); break;
      case 'capture': {
        const image = message.image;
        if (typeof image === 'string' && image.length > 0 && image.length <= MAX_IMAGE_CHARS && BASE64.test(image)) {
          onCapture(image);
        }
        break;
      }
      default: break;
    }
  };

  const capture = async () => {
    if (!active.current || !permission?.granted || capturingRef.current || !camera.current) return;
    capturingRef.current = true;
    setCapturing(true);
    setCaptureError('');
    try {
      const photo = await camera.current.takePictureAsync({ base64: true, quality: 0.7, skipProcessing: false });
      if (!photo?.base64) throw new Error('The camera did not return an image.');
      if (active.current) onCapture(photo.base64);
    } catch (error) {
      setCaptureError(error instanceof Error ? error.message : 'Could not capture the photo. Try again.');
    } finally {
      capturingRef.current = false;
      if (active.current) setCapturing(false);
    }
  };

  const live = mode === 'live' && !!permission?.granted;

  return (
    <Modal visible={visible} animationType="slide" onShow={open} onRequestClose={close}>
      <SafeAreaView style={{ flex: 1, backgroundColor: live ? '#fff' : '#000' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 16, paddingVertical: 12 }}>
          <Pressable onPress={close} hitSlop={12} accessibilityLabel="Close selfie capture">
            <ZIcon name="x" size={22} color={live ? c.ink1 : '#fff'} stroke={2.2} />
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={{ fontFamily: font.bold, color: live ? c.ink1 : '#fff', fontSize: 15 }}>Verify your identity</Text>
            <Text style={{ fontFamily: font.regular, color: live ? c.ink3 : 'rgba(255,255,255,.7)', fontSize: 12, marginTop: 2 }}>
              {live ? 'Turn left, turn right, then smile' : 'Verification service review'}
            </Text>
          </View>
        </View>

        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
          {live ? (
            <View style={{ width: '100%', flex: 1 }}>
              <WebView
                testID="liveness-web"
                source={{ uri: LIVENESS_URL }}
                onMessage={onMessage}
                onError={() => { if (active.current) setMode('camera'); }}
                onHttpError={() => { if (active.current) setMode('camera'); }}
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
              {!ready ? (
                <View pointerEvents="none" style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, alignItems: 'center', justifyContent: 'center', backgroundColor: '#fff' }}>
                  <ActivityIndicator color={c.brand} />
                  <Text style={{ fontFamily: font.regular, color: c.ink3, fontSize: 13, marginTop: 12 }}>Getting the face check ready…</Text>
                </View>
              ) : null}
            </View>
          ) : permission?.granted ? (
            <View style={{ width: '100%', flex: 1, overflow: 'hidden' }}>
              <CameraView ref={camera} style={{ flex: 1 }} facing="front" mirror />
              <View pointerEvents="none" style={{ position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, alignItems: 'center', justifyContent: 'center' }}>
                <View style={{ width: 250, height: 330, borderRadius: 125, borderWidth: 3, borderColor: c.lime, opacity: 0.9 }} />
              </View>
            </View>
          ) : permission?.canAskAgain !== false ? (
            <View style={{ padding: 32, alignItems: 'center', gap: 14 }}>
              <ZIcon name="camera" size={32} color="#fff" stroke={2} />
              <Text style={{ color: '#fff', textAlign: 'center', fontFamily: font.regular, fontSize: 13 }}>
                Allow camera access to take a selfie for identity verification.
              </Text>
              <Pressable onPress={() => void requestPermission()} style={{ paddingHorizontal: 18, paddingVertical: 12, borderRadius: 14, backgroundColor: c.brand }}>
                <Text style={{ color: c.inkOnBrand, fontFamily: font.bold }}>Enable camera</Text>
              </Pressable>
            </View>
          ) : (
            <View style={{ padding: 32, alignItems: 'center', gap: 12 }}>
              <ZIcon name="help" size={28} color="#fff" stroke={2} />
              <Text style={{ color: '#fff', textAlign: 'center', fontFamily: font.regular, fontSize: 13 }}>
                Camera access is off. Enable it in Settings to continue.
              </Text>
            </View>
          )}
        </View>

        {live ? (
          <View style={{ paddingHorizontal: 24, paddingBottom: 20, paddingTop: 8, alignItems: 'center', gap: 8 }}>
            {slow ? (
              <Pressable onPress={() => setMode('camera')} accessibilityRole="button" accessibilityLabel="Use the simple camera instead" hitSlop={8}>
                <Text style={{ color: c.brand, fontFamily: font.bold, fontSize: 13.5, textDecorationLine: 'underline' }}>
                  Having trouble? Use the simple camera instead
                </Text>
              </Pressable>
            ) : null}
            <Text style={{ color: c.ink3, fontFamily: font.regular, fontSize: 11, textAlign: 'center' }}>
              The face check runs on your phone. Zitch sends the selfie to the verification service and does not store it.
            </Text>
          </View>
        ) : (
          <View style={{ paddingHorizontal: 24, paddingBottom: 28, paddingTop: 12, alignItems: 'center', gap: 14 }}>
            <Text style={{ color: '#fff', fontFamily: font.medium, fontSize: 13.5, textAlign: 'center' }}>
              {captureError || (permission?.granted ? 'Center your face in the guide, then take the selfie.' : 'A selfie is required to continue.')}
            </Text>
            <Pressable
              onPress={() => void capture()}
              disabled={!permission?.granted || capturing}
              accessibilityRole="button"
              accessibilityLabel="Take selfie"
              style={{ width: 68, height: 68, borderRadius: 34, borderWidth: 4, borderColor: '#fff', backgroundColor: permission?.granted ? c.brand : 'rgba(255,255,255,.25)', alignItems: 'center', justifyContent: 'center', opacity: permission?.granted && !capturing ? 1 : 0.6 }}
            >
              {capturing ? <ActivityIndicator color="#fff" /> : <ZIcon name="camera" size={24} color="#fff" stroke={2.2} />}
            </Pressable>
            <Text style={{ color: 'rgba(255,255,255,.6)', fontFamily: font.regular, fontSize: 11, textAlign: 'center' }}>
              Zitch sends the captured image to the configured verification service and does not store it. The service decides whether the submission can be accepted.
            </Text>
          </View>
        )}
      </SafeAreaView>
    </Modal>
  );
};

export default FaceLivenessModal;
