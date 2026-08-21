import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Modal, View, Text, Pressable, ActivityIndicator, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Svg, { Ellipse } from 'react-native-svg';
import {
  useCameraDevice,
  useCameraPermission,
  usePhotoOutput,
} from 'react-native-vision-camera';
import { Camera, type Face } from 'react-native-vision-camera-face-detector';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';

/**
 * In-app liveness capture for the non-bank (document/Prembly) KYC rail — a
 * live front-camera preview with a face guide and real-time "no face
 * detected" feedback, replacing a bare gallery-style camera snap.
 *
 * On-device face detection here is UX only, never the security boundary:
 * it just tells the customer when to press the shutter. The actual liveness
 * verdict is still decided server-side by Prembly on the captured photo,
 * exactly as before — nothing here changes what `/api/kyc/face/` trusts.
 *
 * The bank rail (`FaceVerifyModal`) is a completely different component: it
 * hands the whole capture off to the bank's own hosted page. This one is
 * for the deploys where that rail isn't live.
 */
const FaceLivenessModal = ({
  visible,
  onClose,
  onCapture,
}: {
  visible: boolean;
  onClose: () => void;
  /** Called with the captured selfie as base64 JPEG. */
  onCapture: (base64: string) => void;
}) => {
  const { c } = useTheme();
  const device = useCameraDevice('front');
  const { hasPermission, requestPermission } = useCameraPermission();
  // JPEG, and biased toward speed over quality — this only needs to be good
  // enough for Prembly's liveness check, and a slow capture here reads to the
  // customer as a frozen shutter.
  const photoOutput = usePhotoOutput({
    containerFormat: 'jpeg', quality: 0.7, qualityPrioritization: 'speed',
  });
  const [faceCount, setFaceCount] = useState(0);
  const [capturing, setCapturing] = useState(false);
  const held = useRef(false);

  const hold = () => { if (!held.current) { held.current = true; beginExternalActivity(); } };
  const release = () => { if (held.current) { held.current = false; endExternalActivity(); } };

  // Every open starts clean — mirrors FaceVerifyModal's onShow pattern. This
  // is an event ("the sheet opened"), not state synchronization, so it lives
  // in the Modal's onShow rather than an effect keyed on `visible`.
  const open = () => {
    hold();
    setFaceCount(0);
    setCapturing(false);
    if (!hasPermission) requestPermission();
  };

  // The hold has to release on every exit, not just the close button — same
  // reasoning as FaceVerifyModal. No setState here, only a ref and the
  // session module, so this is safe to run directly in the effect body.
  useEffect(() => {
    if (!visible) release();
    return release;
  }, [visible]);

  const close = () => { release(); onClose(); };

  const handleFaces = useCallback((faces: Face[]) => {
    setFaceCount(Array.isArray(faces) ? faces.length : 0);
  }, []);

  const ready = hasPermission && faceCount === 1 && !capturing;

  const capture = async () => {
    if (!ready) return;
    setCapturing(true);
    try {
      const photo = await photoOutput.capturePhoto({ flashMode: 'off' }, {});
      try {
        const path = await photo.saveToTemporaryFileAsync();
        const FS = await import('expo-file-system/legacy');
        const b64 = await FS.readAsStringAsync(path, { encoding: 'base64' });
        onCapture(b64);
      } finally {
        photo.dispose();
      }
    } catch {
      // Left in place with the shutter re-enabled — a failed capture is not
      // a failed verification, and the customer should just be able to
      // try again rather than meet a dead end.
    } finally {
      setCapturing(false);
    }
  };

  const status = !hasPermission
    ? 'Camera access is off — allow it in Settings to take your selfie.'
    : faceCount === 0
      ? 'No face detected — center your face in the oval'
      : faceCount > 1
        ? 'Only one face at a time, please'
        : 'Hold still…';

  return (
    <Modal visible={visible} animationType="slide" onShow={open} onRequestClose={close}>
      <SafeAreaView style={{ flex: 1, backgroundColor: '#000' }}>
        <View style={{
          flexDirection: 'row', alignItems: 'center', gap: 12,
          paddingHorizontal: 16, paddingVertical: 12,
        }}>
          <Pressable onPress={close} hitSlop={12} accessibilityLabel="Close selfie capture">
            <ZIcon name="x" size={22} color="#fff" stroke={2.2} />
          </Pressable>
          <Text style={{ flex: 1, fontFamily: font.bold, color: '#fff', fontSize: 15 }}>
            Verify your identity
          </Text>
        </View>

        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
          {device && hasPermission ? (
            <View style={{ width: '100%', flex: 1 }}>
              <Camera
                style={StyleSheet.absoluteFill}
                device={device}
                isActive={visible}
                outputs={[photoOutput]}
                runClassifications={false}
                runContours={false}
                runLandmarks={false}
                performanceMode="fast"
                onFacesDetected={handleFaces}
                onError={() => setFaceCount(0)}
              />
              {/* Purely visual — the server never sees this overlay, only the
                  photo underneath it. */}
              <View pointerEvents="none" style={StyleSheet.absoluteFill}>
                <Svg width="100%" height="100%">
                  <Ellipse
                    cx="50%" cy="46%" rx="38%" ry="30%"
                    fill="none"
                    stroke={faceCount === 1 ? c.lime : '#fff'}
                    strokeWidth={3}
                    strokeOpacity={0.9}
                  />
                </Svg>
              </View>
            </View>
          ) : !hasPermission ? (
            <View style={{ padding: 32, alignItems: 'center', gap: 10 }}>
              <ZIcon name="help" size={28} color="#fff" stroke={2} />
              <Text style={{ color: '#fff', textAlign: 'center', fontFamily: font.regular, fontSize: 13 }}>
                {status}
              </Text>
            </View>
          ) : (
            <ActivityIndicator color="#fff" />
          )}
        </View>

        <View style={{ paddingHorizontal: 24, paddingBottom: 28, paddingTop: 12, alignItems: 'center', gap: 16 }}>
          <Text style={{ color: '#fff', fontFamily: font.medium, fontSize: 13.5, textAlign: 'center' }}>
            {status}
          </Text>
          <Pressable
            onPress={capture}
            disabled={!ready}
            accessibilityRole="button"
            accessibilityLabel="Take photo"
            accessibilityState={{ disabled: !ready }}
            style={{
              width: 68, height: 68, borderRadius: 34,
              borderWidth: 4, borderColor: '#fff',
              backgroundColor: ready ? c.brand : 'rgba(255,255,255,.25)',
              alignItems: 'center', justifyContent: 'center',
              opacity: ready ? 1 : 0.6,
            }}
          >
            {capturing ? <ActivityIndicator color="#fff" /> : null}
          </Pressable>
          <Text style={{ color: 'rgba(255,255,255,.6)', fontFamily: font.regular, fontSize: 11, textAlign: 'center' }}>
            Your photo is used only to verify it&apos;s you.
          </Text>
        </View>
      </SafeAreaView>
    </Modal>
  );
};

export default FaceLivenessModal;
