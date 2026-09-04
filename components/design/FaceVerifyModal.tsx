import React, { useEffect, useRef, useState } from 'react';
import { Modal, View, Text, ActivityIndicator, Pressable, Platform } from 'react-native';
import { WebView } from 'react-native-webview';
import { SafeAreaView } from 'react-native-safe-area-context';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';

/**
 * The bank's face check, hosted inside the app.
 *
 * It used to open in the system browser. That worked, but it took the customer out
 * of Zitch mid-verification and dropped them at a bare Azure URL with no branding
 * and nothing to say who was asking for their face — which is exactly the shape of
 * a phishing page, and exactly the moment a careful customer should abandon. A
 * verification step that trains people to trust an unbranded pop-up is a bad habit
 * to teach in a banking app.
 *
 * The page itself is still entirely the BANK's: their URL, their liveness capture,
 * their result. Nothing here reads the page, injects into it, or learns the outcome
 * — the outcome arrives on our server from the bank, which is the only version a
 * client cannot fake. This component's whole job is to hold the frame and say
 * whose page this is.
 */
const FaceVerifyModal = ({
  url,
  visible,
  onClose,
}: {
  url: string;
  visible: boolean;
  onClose: () => void;
}) => {
  const { c } = useTheme();
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  // The app-lock counts the camera and the picker as "away"; the same applies here.
  // Held for as long as the sheet is up, and released once — a ref, not state, so a
  // re-render can never double-count it.
  const held = useRef(false);

  const hold = () => {
    if (!held.current) { held.current = true; beginExternalActivity(); }
  };
  const release = () => {
    if (held.current) { held.current = false; endExternalActivity(); }
  };

  const close = () => { release(); onClose(); };

  /** Every open starts clean.
   *
   * `loading` and `failed` used to persist across opens, so one transient network
   * error latched the error state: every later attempt showed "couldn't open"
   * without trying, and only killing the app cleared it.
   *
   * Done on the Modal's own onShow rather than in an effect keyed on `visible` —
   * setting state inside an effect body triggers the cascading render React warns
   * about, and this is genuinely an event ("the sheet opened"), not a state
   * synchronisation.
   */
  const open = () => {
    setLoading(true);
    setFailed(false);
    hold();
  };

  // The hold has to be released on EVERY exit, not just the ones that go through
  // the close button. The parent takes the sheet down itself when the bank confirms
  // — and unmounting the screen skips `close` entirely — so without this the
  // app-lock stays suppressed for the rest of the session on the happy path, which
  // is the one customers actually hit. No setState here: `release` only touches the
  // session module and a ref.
  useEffect(() => {
    if (!visible) release();
    return release;
  }, [visible]);

  return (
    <Modal visible={visible} animationType="slide" onShow={open} onRequestClose={close}>
      <SafeAreaView style={{ flex: 1, backgroundColor: c.bg }}>
        <View style={{
          flexDirection: 'row', alignItems: 'center', gap: 12,
          paddingHorizontal: 16, paddingVertical: 12,
          borderBottomWidth: 1, borderBottomColor: c.line,
        }}>
          <Pressable onPress={close} hitSlop={12} accessibilityLabel="Close face verification">
            <ZIcon name="x" size={22} color={c.ink1} stroke={2.2} />
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={{ fontFamily: font.bold, color: c.ink1, fontSize: 15 }}>
              Face verification
            </Text>
            {/* Named, every time. The customer is about to show their face to a page
                we did not write; they are entitled to know whose it is. */}
            <Text style={{ fontFamily: font.regular, color: c.ink3, fontSize: 12 }}>
              Secure page provided by your bank
            </Text>
          </View>
          <ZIcon name="shield" size={18} color={c.brand} stroke={2} />
        </View>

        {failed ? (
          <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32, gap: 10 }}>
            <ZIcon name="help" size={28} color={c.ink3} stroke={2} />
            <Text style={{ fontFamily: font.bold, color: c.ink1, fontSize: 15, textAlign: 'center' }}>
              Couldn&apos;t open the verification page
            </Text>
            <Text style={{ fontFamily: font.regular, color: c.ink3, fontSize: 13, textAlign: 'center', lineHeight: 19 }}>
              Check your connection and try again. Nothing has been submitted.
            </Text>
          </View>
        ) : (
          <WebView
            source={{ uri: url }}
            onLoadEnd={() => setLoading(false)}
            onError={() => { setLoading(false); setFailed(true); }}
            onHttpError={() => { setLoading(false); setFailed(true); }}
            // Some versions of Wema's hosted page navigate the WebView to the
            // profiled callback URL after a completed capture. Do not leave the
            // customer staring at our callback's JSON response: close the bank
            // sheet immediately and reveal the KYC screen while the parent keeps
            // polling the authenticated server result.
            onShouldStartLoadWithRequest={(request) => {
              try {
                const target = new URL(request.url);
                if (
                  target.protocol === 'https:'
                  && target.hostname === 'api.zitch.ng'
                  && target.pathname.startsWith('/webhooks/wema/face')
                ) {
                  close();
                  return false;
                }
              } catch {
                // Let the WebView handle malformed/transient navigation values.
              }
              return true;
            }}
            // Liveness needs the camera INSIDE the web page. Both platforms already
            // declare the permission at the app level (app.json); these hand it
            // through to the WebView so the customer is asked once, by the OS,
            // rather than meeting a silent black rectangle.
            allowsInlineMediaPlayback
            mediaPlaybackRequiresUserAction={false}
            mediaCapturePermissionGrantType="grant"
            // Android does not honour mediaCapturePermissionGrantType — it requires
            // the host to handle onPermissionRequest and explicitly grant the resources
            // the page asked for (camera, microphone). Without this the WebView silently
            // refuses getUserMedia and the liveness capture fails with a "network error"
            // inside the bank's page.
            onPermissionRequest={(request: { grant: (r: string[]) => void; resources: string[] }) => request.grant(request.resources)}
            // The bank's verification page also requests the device's location.
            // Permission is requested by the caller before this sheet opens;
            // this flag passes whatever the OS granted through to the web page.
            geolocationEnabled
            // Android needs this for getUserMedia to be offered at all.
            javaScriptEnabled
            domStorageEnabled
            // Allow the full HTTPS space. Wema's liveness SDK makes requests to
            // Azure Cognitive Services and other Azure subdomains during capture;
            // locking to the exact host silently blocks those and the page reports
            // "network error". The redirect guard below (setSupportMultipleWindows)
            // is the real protection against off-site navigation.
            originWhitelist={['https://*']}
            setSupportMultipleWindows={false}
            style={{ flex: 1, backgroundColor: c.bg }}
          />
        )}

        {loading && !failed ? (
          // Below the header, never over it. Covering the whole sheet hid the close
          // button, so a page that hung left the customer with no way out at all on
          // iOS, where there is no system back gesture to fall back on.
          <View pointerEvents="none" style={{
            position: 'absolute', left: 0, right: 0, top: 64, bottom: 0,
            alignItems: 'center', justifyContent: 'center', backgroundColor: c.bg,
          }}>
            <ActivityIndicator color={c.brand} />
            <Text style={{ fontFamily: font.regular, color: c.ink3, fontSize: 13, marginTop: 12 }}>
              Opening your bank&apos;s secure page…
            </Text>
          </View>
        ) : null}

        <View style={{ paddingHorizontal: 20, paddingVertical: 12, borderTopWidth: 1, borderTopColor: c.line }}>
          <Text style={{ fontFamily: font.regular, color: c.ink3, fontSize: 11.5, lineHeight: 17, textAlign: 'center' }}>
            Your photo is captured by your bank and is never stored by Zitch.
            {Platform.OS === 'ios' ? ' Allow camera and location access when asked.' : ''}
          </Text>
        </View>
      </SafeAreaView>
    </Modal>
  );
};


export default FaceVerifyModal;
