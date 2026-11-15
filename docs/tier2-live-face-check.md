# Tier 2 live face check

Tier 2 sends Wema the account number, the customer's BVN and NIN, and a live
photo of their face (`partner-account-upgrade-tier2`, `liveImageOfFace`). The
bank compares the photo with the photo behind the BVN/NIN and decides; only its
read-back raises the tier (`docs/wema-migration.md`, *Account tier upgrade*).

Before this check the photo went to the bank as captured: any base64 (even
non-image bytes), mirrored by both cameras, full sensor size from Android
(often over the 2.8 MB limit), with EXIF/GPS metadata, from a camera or a gallery
file, and with nothing to show a live person was in front of the camera.

## What happens now

1. **Capture, guided.** The app (`components/design/FaceLivenessModal.tsx`) and
   the WhatsApp page (`backend/whatsapp/templates/whatsapp/face_camera.html`)
   show an oval with a light mask around it (it also lights the face), a short
   countdown, then take a burst of straight photos and, after "slowly turn your
   head to your left", a few frames of the turn. Photos are never mirrored (the
   ID photo is not mirrored either). The WhatsApp page sends the 3:4 window the
   customer sees, gives live light and steadiness hints, keeps the sharpest
   frame of its burst, and falls back to two camera-app photos (straight, then
   turned) where the browser cannot stream the camera. Android takes a 4:3 photo
   near 1280 px instead of the 12-50 MP sensor default.
2. **Checked before the bank** (`backend/wallet/selfie.py`). Every photo is
   decoded strictly, turned upright (EXIF, or the orientation the face is found
   in), stripped of metadata and bounded to 1280 px. YuNet (`wallet/face_model`,
   MIT, OpenCV) finds the face and five landmarks. A photo is refused with one
   instruction when it is clearly unusable: no face, more than one face, face
   too far or cut off, not facing the camera, too dark, backlit, burnt out, or
   very blurred. The clearest passing frame of the burst is the one sent.
3. **Live, three-dimensional face** (`selfie.verify_head_turn`). The nose tip
   stands out from the face, so in a real head turn it moves across the line
   between the eyes; a printed or on-screen photo tilted in front of the camera
   is flat, so it keeps its place. The turn must move the nose at least 0.15 of
   the eye distance, either way, with the same framing and background as the
   straight photo.
4. **Bound to the photo.** The app calls `POST /api/wallet/wema/tier2-face-check/`
   (`frames` 1-3, `turns` 1-8; nothing goes to the bank, nothing is stored) and
   gets back the chosen frame and a 15-minute signed proof bound to the
   customer, their current credentials and that photo's SHA-256. It sends the
   photo and `liveness_token` to `/api/wallet/wema/upgrade-tier2/`. The WhatsApp
   page checks and submits in one request, before the link is claimed.
5. **Errors say what to do.** `selfie_*` and `liveness_*` codes (HTTP 422) come
   with one plain instruction (`selfie.MESSAGES`). A BVN/NIN problem keeps the
   checked photo; a refused or expired face check asks for a new one.

The Prembly SDK session no longer takes part in Tier 2: its `tier2_liveness`
purpose is refused before any provider call (`tier2_moved_to_bank`), so no
session can raise the local tier without the bank. It still serves the
new-device sign-in check.

## Rollout switch

`TIER2_LIVENESS_REQUIRED` (API service, default `false`). The WhatsApp page
always requires the live check. App builds released before it send one photo
without a proof; while the switch is off they are still accepted after the
photo checks, and each one is logged (`tier2_upgrade_without_live_face_check`).
Set it to `true` once the app with the live check is installed by customers:
older builds then get "Update the Zitch app to finish Tier 2".

## Thresholds and calibration

The limits in `wallet/selfie.py` are lenient on purpose: they refuse only photos
a face matcher cannot use, because a refusal here is a customer who cannot
reach Tier 2. They were set by running the checks over frontal portraits of
lighter and darker skin (sample photos from open-source face-recognition
projects, kept out of the repository) and over degraded copies of each: Gaussian and motion blur, darkening with and
without noise, over-exposure, a bright background with a dark face, heavy JPEG,
small sizes, sideways and mirrored copies, and a flat photo tilted 20-35 degrees
in perspective.

| Check | Limit | Calibration |
| --- | --- | --- |
| Face brightness | refuse below 40 (0-255) | portraits 92-158; halved light still passes |
| Backlight | face below 60 and background 110 brighter | a lit face on a white wall passes |
| Burnt out | face above 228 or 35% of it at white | |
| Blur (Crete et al., 0-1) | refuse above 0.75 | sharp 0.28-0.40, a soft video still 0.64, blur radius 3 0.68-0.79 |
| Face size | width under 16% of the photo or eyes under 36 px apart | |
| Pose | tilt over 25 degrees, nose more than 0.25 off centre | a ~25 degree turn (0.19) passes |
| Head turn | nose moves 0.15 or more | flat photo tilted 20-35 degrees: 0.00-0.09 |

Edge energy falls with brightness, so measured plainly a dim or darker face
would read as blurry; blur is measured as a ratio instead, which light and skin
tone do not move. The backlight rule only fires when the face itself is dark,
so a darker face in front of a bright wall is not refused for it.

## Limits

- This is a check on the capture, not a certified presentation-attack test. It
  stops a static photo or a screen held up to the camera and an unusable photo;
  a pre-recorded video of the customer turning their head, or frames injected by
  a modified client, would need a certified liveness product. The bank's face
  match against the BVN/NIN photo remains the identity decision.
- The app's capture runs on expo-camera without on-device face tracking: the
  turn frames are timed, and the server measures them. On-device landmarks
  (for example ML Kit) could guide the turn as it happens; that needs a new
  native module and device testing.
- Memory: OpenCV, numpy and the model add about 45 MB to the API process, loaded
  on the first face check. If they cannot load, photos still get the light and
  readability checks, and the live check answers 503 instead of passing.
