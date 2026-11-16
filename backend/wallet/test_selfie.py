"""Tier 2 selfie checks and the live face check (wallet/selfie.py, face_check.py).

Face landmarks come from the stand-in detector in selfie_fixtures unless a
test says it runs the real YuNet model. No bank traffic: every upgrade call
is mocked and the network is blocked.
"""
import base64
import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image

from accounts.models import AccessToken, User
from wallet import face_check, selfie
from wallet import selfie_fixtures as fx
from wallet.services import get_or_create_wallet
from wallet.views import upgrade_wema_identity

PILOT = {
    "BASE_URL": "https://lagos-alat-blueapi.azure-api.net",
    "CHANNEL_ID": "test-channel", "KEYS": {"wallet": "test-wallet", "upgrade": "test-upgrade"},
    "SIMULATION": False,
}
BVN, NIN = "22222222222", "33333333333"
TURNED = {1: [{"yaw": 0.74}], 2: [{"yaw": 0.71}]}


def turns(*tags, **kwargs):
    return [fx.selfie_b64(tag=tag, **kwargs) for tag in tags]


class DecodeAndPrepareTests(SimpleTestCase):
    def test_refuses_what_is_not_a_photo_before_looking_at_it(self):
        for value, code in ((None, "selfie_missing"), ("", "selfie_missing"), ("   ", "selfie_missing"),
                            ("not-base64!", "selfie_unreadable"), ("ZmFrZQ==", "selfie_unreadable"),
                            ("data:text/plain;base64,ZmFrZQ==", "selfie_unreadable"),
                            ("A" * (selfie.MAX_BASE64_CHARS + 1), "selfie_too_large")):
            with self.subTest(value=str(value)[:20]):
                self.assertEqual(selfie.assess(value).code, code)

    def test_unsupported_tiny_or_strip_shaped_images_are_refused(self):
        gif = io.BytesIO()
        fx.portrait().save(gif, "GIF")
        cases = ((base64.b64encode(gif.getvalue()).decode(), "selfie_unreadable"),
                 (fx.b64(fx.portrait(240, 320)), "selfie_too_small"),
                 (fx.b64(Image.new("RGB", (1600, 400), (120, 110, 100))), "selfie_unreadable"))
        for value, code in cases:
            with self.subTest(code=code), fx.detector():
                self.assertEqual(selfie.assess(value).code, code)

    def test_huge_frames_are_refused_before_they_are_decoded(self):
        # A flat 20 MP PNG compresses to a few KB but would decode to 60 MB.
        flat = io.BytesIO()
        Image.new("RGB", (4000, 5000), (120, 110, 100)).save(flat, "PNG", optimize=True)
        self.assertLess(len(flat.getvalue()), selfie.MAX_BASE64_CHARS)
        with patch.object(Image.Image, "load", side_effect=AssertionError("decoded")):
            self.assertEqual(selfie.assess(base64.b64encode(flat.getvalue()).decode()).code,
                             "selfie_too_large")

    def test_data_urls_and_wrapped_base64_are_read(self):
        plain = fx.selfie_b64()
        wrapped = "\n".join(plain[i:i + 76] for i in range(0, len(plain), 76))
        with fx.detector():
            for value in ("data:image/jpeg;base64," + plain, wrapped):
                with self.subTest(prefix=value[:10]):
                    self.assertTrue(selfie.assess(value).ok)

    def test_a_clean_upright_jpeg_reaches_the_bank_unchanged(self):
        value = fx.selfie_b64()
        with fx.detector():
            photo = selfie.assess(value)
        self.assertTrue(photo.ok)
        self.assertEqual(photo.bank_base64(), value)

    def test_metadata_is_stripped_and_exif_orientation_applied(self):
        upright = fx.portrait()
        exif = Image.Exif()
        exif[0x0112] = 6        # the camera stored it turned: rotate 90 to view
        exif[0x010F] = "PhoneMaker"
        sideways = upright.transpose(Image.Transpose.ROTATE_90)
        with fx.detector(upright_only=True):
            photo = selfie.assess(fx.b64(sideways, exif=exif.tobytes()))
        self.assertTrue(photo.ok, photo.metrics)
        sent = Image.open(io.BytesIO(photo.bank_bytes()))
        self.assertEqual(sent.size, (720, 960))
        self.assertFalse(sent.getexif())
        self.assertNotIn(b"PhoneMaker", photo.bank_bytes())

    def test_large_camera_photos_are_sent_at_bank_size(self):
        with fx.detector():
            photo = selfie.assess(fx.b64(fx.portrait(1536, 2048), quality=80))
        self.assertTrue(photo.ok, photo.code)
        sent = Image.open(io.BytesIO(photo.bank_bytes()))
        self.assertEqual(max(sent.size), selfie.BANK_LONG_SIDE)
        self.assertEqual(sent.format, "JPEG")

    def test_a_photo_saved_sideways_without_exif_is_turned_upright(self):
        sideways = fx.portrait().transpose(Image.Transpose.ROTATE_270)
        with fx.detector(upright_only=True):
            photo = selfie.assess(fx.b64(sideways))
        self.assertTrue(photo.ok, photo.code)
        self.assertTrue(photo.metrics["rotated"])
        self.assertEqual(Image.open(io.BytesIO(photo.bank_bytes())).size, (720, 960))


class FaceQualityTests(SimpleTestCase):
    def assess(self, faces=None, **portrait):
        with fx.detector(faces):
            return selfie.assess(fx.selfie_b64(**portrait))

    def test_a_clear_frontal_selfie_passes(self):
        photo = self.assess()
        self.assertTrue(photo.ok, photo.metrics)
        self.assertEqual(photo.message, "")
        self.assertGreater(photo.score, 0.6)

    def test_each_unusable_photo_gets_one_instruction(self):
        cases = (
            ({0: []}, {}, "selfie_no_face"),
            ({0: [{}, {"box": (0.02, 0.1, 0.3, 0.3), "score": 0.9}]}, {}, "selfie_many_faces"),
            ({0: [{"box": (0.42, 0.45, 0.1, 0.1)}]}, {}, "selfie_face_small"),
            ({0: [{"box": (0.5, 0.3, 0.7, 0.5)}]}, {}, "selfie_face_cut"),
            ({0: [{"yaw": 0.85}]}, {}, "selfie_not_facing"),
            ({0: [{"roll": 40}]}, {}, "selfie_not_facing"),
            (None, {"light": 0.22}, "selfie_too_dark"),
            (None, {"light": 3.0}, "selfie_too_bright"),
            (None, {"scene": "bright", "face_light": 0.35}, "selfie_backlit"),
            (None, {"blur": 8}, "selfie_blurry"),
        )
        for faces, portrait, code in cases:
            with self.subTest(code=code, portrait=portrait):
                photo = self.assess(faces, **portrait)
                self.assertEqual(photo.code, code, photo.metrics)
                self.assertEqual(photo.message, selfie.MESSAGES[code])

    def test_a_small_background_face_or_a_lit_face_on_a_white_wall_still_passes(self):
        background_person = {0: [{}, {"box": (0.02, 0.05, 0.1, 0.1), "score": 0.95}]}
        self.assertTrue(self.assess(background_person).ok)
        # A dim but lit face in front of a bright wall is not "backlit".
        self.assertTrue(self.assess(scene="bright", face_light=0.7).ok)

    def test_turn_frames_only_need_one_face(self):
        with fx.detector({0: [{"yaw": 0.85}]}):
            self.assertTrue(selfie.assess(fx.selfie_b64(), frontal=False).ok)
        with fx.detector({0: []}):
            self.assertEqual(selfie.assess(fx.selfie_b64(), frontal=False).code, "selfie_no_face")

    def test_the_clearest_frame_of_a_burst_is_chosen(self):
        frames = [fx.selfie_b64(tag=0, blur=3), fx.selfie_b64(tag=1), fx.selfie_b64(tag=2, blur=2)]
        with fx.detector():
            index, photo = selfie.pick_frontal(frames)
        self.assertEqual(index, 1)
        self.assertTrue(photo.ok)

    def test_when_no_frame_passes_the_most_common_reason_is_given(self):
        frames = [fx.selfie_b64(tag=0, light=0.2), fx.selfie_b64(tag=1, light=0.2),
                  fx.selfie_b64(tag=2, blur=8)]
        with fx.detector():
            index, photo = selfie.pick_frontal(frames)
        self.assertEqual(photo.code, "selfie_too_dark")
        self.assertIn(index, (0, 1))

    def test_without_a_detector_light_is_still_checked_on_the_centre(self):
        with patch.object(selfie, "_detect", return_value=None):
            self.assertTrue(selfie.assess(fx.selfie_b64()).ok)
            self.assertEqual(selfie.assess(fx.selfie_b64(light=0.2)).code, "selfie_too_dark")
            self.assertEqual(selfie.assess(fx.b64(Image.new("RGB", (720, 960), (128, 128, 128)))).code,
                             "selfie_no_face")


class HeadTurnTests(SimpleTestCase):
    def check(self, faces, frames, **frontal):
        with fx.detector(faces):
            photo = selfie.assess(fx.selfie_b64(**frontal))
            return selfie.verify_head_turn(photo, frames)

    def test_a_real_turn_either_way_passes(self):
        for yaw in (0.72, 0.28):
            with self.subTest(yaw=yaw):
                code, metrics = self.check({1: [{"yaw": yaw}]}, turns(1))
                self.assertEqual(code, "ok", metrics)
                self.assertGreaterEqual(metrics["turn"], selfie.MIN_TURN)

    def test_a_flat_photo_turned_in_front_of_the_camera_does_not(self):
        # A printed or on-screen face keeps its nose between the eyes when tilted.
        code, metrics = self.check({1: [{"yaw": 0.56, "box": (0.31, 0.26, 0.38, 0.40)}],
                                    2: [{"yaw": 0.45}]}, turns(1, 2))
        self.assertEqual(code, "liveness_no_turn", metrics)

    def test_frames_without_a_face_or_with_another_face(self):
        self.assertEqual(self.check({1: []}, turns(1))[0], "liveness_no_face")
        self.assertEqual(self.check({1: [{"yaw": 0.75}, {"box": (0.0, 0.0, 0.3, 0.3), "score": 0.9}]},
                                    turns(1))[0], "liveness_mismatch")

    def test_turn_frames_from_another_place_or_framing_do_not_count(self):
        elsewhere = [fx.selfie_b64(tag=1, scene="street")]
        self.assertEqual(self.check(TURNED, elsewhere)[0], "liveness_mismatch")
        moved = {1: [{"yaw": 0.75, "box": (0.02, 0.05, 0.2, 0.2)}]}
        self.assertEqual(self.check(moved, turns(1))[0], "liveness_mismatch")
        landscape = [fx.b64(fx.portrait(960, 720, tag=1))]
        self.assertEqual(self.check(TURNED, landscape)[0], "liveness_mismatch")

    def test_the_same_room_still_matches_when_the_camera_re_exposes(self):
        brighter = [fx.selfie_b64(tag=1, light=1.25)]
        self.assertEqual(self.check(TURNED, brighter)[0], "ok")

    def test_missing_frames_or_an_unusable_straight_photo(self):
        self.assertEqual(self.check(None, [])[0], "liveness_missing")
        self.assertEqual(selfie.verify_head_turn(selfie.Selfie("selfie_blurry"), turns(1))[0],
                         "liveness_missing")


class LiveFaceCheckTests(SimpleTestCase):
    def test_the_best_straight_frame_and_a_turn_pass(self):
        frames = [fx.selfie_b64(tag=0, blur=3), fx.selfie_b64(tag=3)]
        with fx.detector(TURNED):
            result = face_check.check_live_face(frames, turns(1, 2))
        self.assertTrue(result.ok, result.metrics)
        self.assertEqual(result.index, 1)

    def test_a_bad_straight_photo_is_reported_before_the_turn(self):
        with fx.detector(TURNED):
            result = face_check.check_live_face([fx.selfie_b64(light=0.2)], turns(1))
        self.assertEqual(result.code, "selfie_too_dark")
        self.assertEqual(result.message, selfie.MESSAGES["selfie_too_dark"])

    def test_no_detector_means_no_pass(self):
        with fx.detector(TURNED), patch.object(selfie, "face_checks_available", return_value=False):
            result = face_check.check_live_face([fx.selfie_b64()], turns(1))
        self.assertEqual(result.code, "face_check_unavailable")
        self.assertEqual(result.message, face_check.UNAVAILABLE)

    def test_frame_lists_are_bounded(self):
        self.assertIsNone(face_check.frame_list("abc", 3))
        self.assertIsNone(face_check.frame_list([], 3))
        self.assertIsNone(face_check.frame_list(["a", "b", "c", "d"], 3))
        self.assertIsNone(face_check.frame_list(["a", 1], 3))
        self.assertEqual(face_check.frame_list(["a", "b"], 3), ["a", "b"])


class RealDetectorTests(SimpleTestCase):
    """The vendored YuNet model through OpenCV, without a stand-in."""

    def setUp(self):
        self.addCleanup(self.reset)
        self.reset()

    @staticmethod
    def reset():
        selfie._detector, selfie._detector_failed = None, False

    def test_the_model_loads_and_finds_the_drawn_face(self):
        self.assertTrue(selfie.face_checks_available())
        photo = selfie.assess(fx.selfie_b64())
        self.assertTrue(photo.ok, photo.metrics)
        self.assertAlmostEqual(photo.face.yaw, 0.5, delta=0.12)

    def test_an_empty_frame_has_no_face(self):
        blank = fx.b64(fx.portrait().crop((0, 0, 720, 300)).resize((720, 960)))
        self.assertEqual(selfie.assess(blank).code, "selfie_no_face")

    def test_a_changed_model_file_disables_face_checks(self):
        with patch.object(selfie, "_MODEL_SHA256", "0" * 64), self.assertLogs("wallet.selfie", "WARNING"):
            self.assertFalse(selfie.face_checks_available())


class ProofTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="proof", phone="08010000501")
        with fx.detector():
            self.photo = selfie.assess(fx.selfie_b64())
            self.other = selfie.assess(fx.selfie_b64(tag=2))

    def test_a_proof_covers_one_customer_and_one_photo(self):
        token = face_check.issue_proof(self.user, self.photo)
        self.assertTrue(face_check.proof_matches(self.user, token, self.photo))
        self.assertFalse(face_check.proof_matches(self.user, token, self.other))
        someone = User.objects.create_user(username="proof-2", phone="08010000502")
        self.assertFalse(face_check.proof_matches(someone, token, self.photo))
        for forged in ("", "x" * 600, token[:-2] + "aa", None):
            self.assertFalse(face_check.proof_matches(self.user, forged, self.photo))

    def test_a_proof_expires_and_dies_with_a_credential_change(self):
        token = face_check.issue_proof(self.user, self.photo)
        with patch.object(face_check, "PROOF_TTL_SECONDS", -1):
            self.assertFalse(face_check.proof_matches(self.user, token, self.photo))
        self.user.set_password("a-new-password-123")
        self.user.save()
        self.assertFalse(face_check.proof_matches(self.user, token, self.photo))


@override_settings(WEMA=PILOT, KYC_PROVIDER="wema", PAYMENT_PROVIDER="wema")
class Tier2FaceCheckEndpointTests(TestCase):
    path = "/api/wallet/wema/tier2-face-check/"

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="face-check", phone="08010000601", first_name="Ada", last_name="Eze",
            email_verified=True, phone_verified=True, bvn_verified=True, tier=1)
        self.user.set_bvn(BVN)
        self.user.save()
        self.wallet = get_or_create_wallet(self.user)
        self.wallet.account_number = "0123456789"
        self.wallet.save(update_fields=["account_number"])
        self.token = AccessToken.issue(self.user).key
        blocker = patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network"))
        blocker.start()
        self.addCleanup(blocker.stop)

    def post(self, **data):
        return self.client.post(self.path, {"access_token": self.token, **data},
                                content_type="application/json")

    def test_returns_the_chosen_frame_and_a_proof_for_it(self):
        frames = [fx.selfie_b64(tag=0, blur=3), fx.selfie_b64(tag=3)]
        with fx.detector(TURNED), patch("utility.wema.upgrade_tier2") as bank:
            response = self.post(frames=frames, turns=turns(1, 2))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["frame"], 1)
        self.assertEqual(body["expires_in"], face_check.PROOF_TTL_SECONDS)
        with fx.detector():
            self.assertTrue(face_check.proof_matches(self.user, body["liveness_token"],
                                                     selfie.assess(frames[1])))
        bank.assert_not_called()

    def test_refusals_say_what_to_do(self):
        with fx.detector({1: [{"yaw": 0.52}]}):
            response = self.post(frames=[fx.selfie_b64()], turns=turns(1))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "liveness_no_turn")
        self.assertEqual(response.json()["message"], selfie.MESSAGES["liveness_no_turn"])
        self.assertNotIn("liveness_token", response.json())
        with fx.detector():
            response = self.post(frames=[fx.selfie_b64(light=0.2)], turns=turns(1))
        self.assertEqual(response.json()["code"], "selfie_too_dark")

    def test_malformed_requests_and_ineligible_customers(self):
        for data in ({}, {"frames": "abc", "turns": ["x"]}, {"frames": ["x"] * 4, "turns": ["x"]},
                     {"frames": ["x"], "turns": ["x"] * 9}):
            with self.subTest(keys=sorted(data)):
                self.assertEqual(self.post(**data).status_code, 400)
        anonymous = self.client.post(self.path, {}, content_type="application/json")
        self.assertEqual(anonymous.status_code, 401)
        self.wallet.account_number = ""
        self.wallet.save(update_fields=["account_number"])
        self.assertEqual(self.post(frames=["x"], turns=["x"]).status_code, 400)
        self.user.face_verified, self.user.tier = True, 2
        self.user.save(update_fields=["face_verified", "tier"])
        self.assertEqual(self.post(frames=["x"], turns=["x"]).json()["code"], "tier2_complete")

    def test_unavailable_detector_or_bank_is_a_503(self):
        with fx.detector(TURNED), patch.object(selfie, "face_checks_available", return_value=False):
            response = self.post(frames=[fx.selfie_b64()], turns=turns(1))
        self.assertEqual(response.status_code, 503)
        with patch("wallet.views.tier2_face_available", return_value=False):
            self.assertEqual(self.post(frames=["x"], turns=["x"]).status_code, 503)


@override_settings(WEMA=PILOT, KYC_PROVIDER="wema", PAYMENT_PROVIDER="wema")
class Tier2UpgradeGateTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="gate", phone="08010000701", first_name="Ada", last_name="Eze",
            email_verified=True, phone_verified=True, bvn_verified=True, tier=1)
        self.user.set_bvn(BVN)
        self.user.save()
        self.wallet = get_or_create_wallet(self.user)
        self.wallet.account_number = "0123456789"
        self.wallet.save(update_fields=["account_number"])
        self.photo = fx.selfie_b64()
        with fx.detector():
            self.proof = face_check.issue_proof(self.user, selfie.assess(self.photo))
        blocker = patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network"))
        blocker.start()
        self.addCleanup(blocker.stop)

    def upgrade(self, **data):
        payload = {"bvn": BVN, "nin": NIN, "live_image": self.photo, **data}
        with fx.detector(), patch("utility.wema.upgrade_tier2", return_value={"success": True}) as bank, \
                patch("utility.wema.get_kyc_status", return_value={
                    "success": True, "tier": "Tier 2", "name": "ADA EZE"}):
            response = upgrade_wema_identity(self.user, payload)
        return response, bank

    def test_a_checked_photo_with_its_proof_goes_to_the_bank_once(self):
        response, bank = self.upgrade(liveness_token=self.proof)
        self.assertEqual(response.status_code, 200, response.content)
        bank.assert_called_once_with("0123456789", bvn=BVN, nin=NIN, live_image=self.photo)
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 2)

    def test_a_proof_for_another_photo_or_an_unusable_photo_never_reaches_the_bank(self):
        for data, code in (({"liveness_token": self.proof, "live_image": fx.selfie_b64(tag=4)}, "liveness_expired"),
                           ({"liveness_token": "forged"}, "liveness_expired"),
                           ({"live_image": fx.selfie_b64(light=0.2)}, "selfie_too_dark"),
                           ({"live_image": "ZmFrZQ=="}, "selfie_unreadable")):
            with self.subTest(code=code):
                response, bank = self.upgrade(**data)
                self.assertEqual(response.status_code, 422, response.content)
                self.assertEqual(json.loads(response.content)["code"], code)
                bank.assert_not_called()

    def test_older_apps_without_a_proof_until_the_check_is_required(self):
        with self.assertLogs("wallet", "WARNING") as logs:
            response, bank = self.upgrade()
        self.assertEqual(response.status_code, 200)
        bank.assert_called_once()
        self.assertIn("tier2_upgrade_without_live_face_check", "\n".join(logs.output))
        self.user.face_verified, self.user.tier = False, 1
        self.user.save(update_fields=["face_verified", "tier"])
        with override_settings(TIER2_LIVENESS_REQUIRED=True):
            response, bank = self.upgrade()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(json.loads(response.content)["code"], "liveness_required")
        bank.assert_not_called()

    def test_the_bank_receives_an_upright_photo_without_metadata(self):
        exif = Image.Exif()
        exif[0x0112] = 6
        exif[0x8825] = {2: (6.0, 30.0, 0.0)}  # GPS latitude
        camera = fx.b64(fx.portrait().transpose(Image.Transpose.ROTATE_90), exif=exif.tobytes())
        with fx.detector(upright_only=True):
            proof = face_check.issue_proof(self.user, selfie.assess(camera))
        payload = {"bvn": BVN, "nin": NIN, "live_image": camera, "liveness_token": proof}
        with fx.detector(upright_only=True), \
                patch("utility.wema.upgrade_tier2", return_value={"success": True}) as bank, \
                patch("utility.wema.get_kyc_status", return_value={
                    "success": True, "tier": "Tier 2", "name": "ADA EZE"}):
            response = upgrade_wema_identity(self.user, payload)
        self.assertEqual(response.status_code, 200, response.content)
        sent = Image.open(io.BytesIO(base64.b64decode(bank.call_args.kwargs["live_image"])))
        self.assertEqual(sent.size, (720, 960))
        self.assertFalse(sent.getexif())
