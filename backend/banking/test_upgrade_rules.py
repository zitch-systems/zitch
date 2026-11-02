"""Upgrade form rules that depend on the ID document and the in-app face capture."""
import base64
from io import BytesIO

from django.test import SimpleTestCase
from PIL import Image

from .validation import fit_photo, upgrade_profile


def picture(side=20, noise=False):
    image = Image.effect_noise((side, side), 90).convert("RGB") if noise else Image.new("RGB", (side, side), "white")
    out = BytesIO()
    image.save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


def profile(**changes):
    document = picture()
    data = {"bvn": "12345678901", "nin": "10987654321", "accountName": "Jane Doe", "phoneNumber": "08011112222",
            "email": "jane@example.test", "idType": "1", "houseNumber": "1", "streetName": "Test Road",
            "state": "Lagos", "city": "Ikeja", "localGovernment": "Ikeja", "pep": "NO", "nearestLandmark": "School",
            **{key: document for key in ("userPhoto", "idCardFront", "idCardBack", "utilityBill", "customerSignature")}}
    data.update(changes)
    return {k: v for k, v in data.items() if v is not None}


class UpgradeRuleTests(SimpleTestCase):
    def test_a_nin_needs_no_dates_or_back_and_uses_the_nin_as_its_id_number(self):
        body = upgrade_profile(2, profile(idCardBack=None))
        self.assertNotIn("idIssueDate", body)
        self.assertNotIn("idExpiryDate", body)
        self.assertNotIn("idCardBack", body)
        self.assertEqual(body["idNumber"], "10987654321")

    def test_a_passport_has_no_back_but_needs_its_dates(self):
        with self.assertRaisesRegex(ValueError, "required"):
            upgrade_profile(2, profile(idType="4", idCardBack=None))
        body = upgrade_profile(2, profile(idType="4", idCardBack=None, idNumber="A1234567",
                                          idIssueDate="2020-01-01", idExpiryDate="2030-01-01"))
        self.assertNotIn("idCardBack", body)
        self.assertEqual(body["idExpiryDate"], "2030-01-01")

    def test_a_drivers_licence_still_needs_its_back(self):
        with self.assertRaisesRegex(ValueError, "document"):
            upgrade_profile(2, profile(idType="2", idCardBack=None, idIssueDate="2020-01-01", idExpiryDate="2030-01-01"))

    def test_a_large_face_photo_is_shrunk_to_the_bank_limit(self):
        large = picture(side=900, noise=True)
        self.assertGreater(len(large), 100000)
        fitted = fit_photo(large, maximum_chars=100000)
        self.assertLessEqual(len(fitted), 100000)
        with Image.open(BytesIO(base64.b64decode(fitted))) as image:
            self.assertEqual(image.format, "JPEG")

    def test_no_face_photo_asks_for_face_verification(self):
        with self.assertRaisesRegex(ValueError, "face"):
            upgrade_profile(2, profile(userPhoto=None))
