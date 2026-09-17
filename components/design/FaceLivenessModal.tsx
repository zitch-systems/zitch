import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Modal, Pressable, Text, View } from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { SafeAreaView } from 'react-native-safe-area-context';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';

/**
 * In-app selfie capture for the combined Wema Tier-2 upgrade.
 *
 * Expo SDK 51 does not include the Vision Camera stack used by newer builds.
 * The configured verification service remains the decision boundary: this modal
 * only captures a front-camera JPEG as base64 and passes it to the upgrade
 * endpoint as `live_image`.
 */
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
  const [capturing, setCapturing] = useState(false);
  const [captureError, setCaptureError] = useState('');
  const held = useRef(false);

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
    setCaptureError('');
    if (!permission?.granted) void requestPermission();
  };

  useEffect(() => {
    if (!visible) release();
    return release;
  }, [visible]);

  const close = () => {
    release();
    onClose();
  };

  const capture = async () => {
    if (!permission?.granted || capturing || !camera.current) return;
    setCapturing(true);
    setCaptureError('');
    try {
      const photo = await camera.current.takePictureAsync({ base64: true, quality: 0.7, skipProcessing: false });
      if (!photo?.base64) throw new Error('The camera did not return an image.');
      onCapture(photo.base64);
    } catch (error) {
      setCaptureError(error instanceof Error ? error.message : 'Could not capture the photo. Try again.');
    } finally {
      setCapturing(false);
    }
  };

  return (
    <Modal visible={visible} animationType="slide" onShow={open} onRequestClose={close}>
      <SafeAreaView style={{ flex: 1, backgroundColor: '#000' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 16, paddingVertical: 12 }}>
          <Pressable onPress={close} hitSlop={12} accessibilityLabel="Close selfie capture">
            <ZIcon name="x" size={22} color="#fff" stroke={2.2} />
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={{ fontFamily: font.bold, color: '#fff', fontSize: 15 }}>Verify your identity</Text>
            <Text style={{ fontFamily: font.regular, color: 'rgba(255,255,255,.7)', fontSize: 12, marginTop: 2 }}>
              Verification service review
            </Text>
          </View>
        </View>

        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
          {permission?.granted ? (
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
      </SafeAreaView>
    </Modal>
  );
};

export default FaceLivenessModal;
