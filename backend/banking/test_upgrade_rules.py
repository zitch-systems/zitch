"""Upgrade form rules that depend on the ID document and the in-app face capture."""
import base64
from io import BytesIO
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from accounts.models import User, record_identity_proof
from wallet.models import Wallet

from .client import Reply
from .models import BankAccount
from .test_client import CONFIG
from .upgrades import submit_upgrade
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
    def test_the_nin_is_the_id_with_no_type_choice_dates_or_back(self):
        body = upgrade_profile(2, profile(idType="2", idCardBack=None, idIssueDate="2020-01-01", placeOfBirth="Lagos"))
        self.assertEqual(body["idType"], "1")
        self.assertEqual(body["idNumber"], "10987654321")
        for gone in ("idIssueDate", "idExpiryDate", "idCardBack", "placeOfBirth"):
            self.assertNotIn(gone, body)

    def test_the_nin_slip_photo_is_required(self):
        with self.assertRaisesRegex(ValueError, "NIN slip"):
            upgrade_profile(2, profile(idCardFront=None))

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


class WalletProfileTests(SimpleTestCase):
    def test_only_the_banks_mandatory_opening_fields_are_sent(self):
        from .validation import wallet_profile
        body = wallet_profile({"lastName": "Doe", "otherNames": "Jane", "phoneNo": "08011112222", "gender": 1,
            "dateOfBirth": "01/01/1990", "address": "1 Test Road", "email": "jane@example.test", "ninUserId": "X",
            "placeOfBirth": "Lagos", "accountName": "Jane", "nextOfKinName": "John", "nextOfKinPhoneNo": "08022223333"})
        self.assertEqual(set(body), {"lastName", "otherNames", "phoneNo", "gender", "dateOfBirth", "address"})


def pdf_bytes(pages=1):
    import pypdfium2
    pdf = pypdfium2.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(595, 842)
    out = BytesIO()
    pdf.save(out)
    pdf.close()
    return out.getvalue()


class FitDocumentTests(SimpleTestCase):
    def test_a_large_photo_in_another_format_becomes_a_jpeg_within_the_limit(self):
        from .validation import fit_document
        image = Image.effect_noise((1800, 1800), 90).convert("RGB")
        out = BytesIO()
        image.save(out, format="WEBP", quality=95)
        fitted = fit_document(base64.b64encode(out.getvalue()).decode(), maximum_chars=50000)
        self.assertLessEqual(len(fitted), 50000)
        with Image.open(BytesIO(base64.b64decode(fitted))) as result:
            self.assertEqual(result.format, "JPEG")

    def test_a_small_pdf_bill_is_kept_and_a_large_one_becomes_an_image(self):
        from .validation import fit_document
        small = base64.b64encode(pdf_bytes()).decode()
        self.assertEqual(fit_document(small, maximum_chars=50000, allow_pdf=True), small)
        rendered = fit_document(small, maximum_chars=20000, allow_pdf=False)  # A PDF where only images are accepted.
        with Image.open(BytesIO(base64.b64decode(rendered))) as result:
            self.assertEqual(result.format, "JPEG")

    def test_files_over_5_mb_and_unreadable_files_are_refused(self):
        from .validation import fit_document
        with self.assertRaisesRegex(ValueError, "5 MB"):
            fit_document("A" * (8 * 1024 * 1024), maximum_chars=50000)
        with self.assertRaisesRegex(ValueError, "photo"):
            fit_document(base64.b64encode(b"not an image at all").decode(), maximum_chars=10)


from django.test import TestCase, override_settings  # noqa: E402

from .test_client import CONFIG  # noqa: E402


@override_settings(NINEPSB=CONFIG)
class UpgradeDocumentEndpointTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        from .test_fixtures import make_bank_user
        cache.clear()
        self.user, self.token = make_bank_user("08077770001", "docs@zitch.test")

    def post(self, **body):
        import json
        return self.client.post("/api/banking/upgrade/document/", data=json.dumps({"access_token": self.token, **body}),
                                content_type="application/json")

    def test_a_large_photo_is_accepted_converted_and_held_for_the_upgrade(self):
        from .upgrades import stored_documents
        image = Image.effect_noise((1180, 1180), 90).convert("RGB")
        out = BytesIO()
        image.save(out, format="PNG")
        encoded = base64.b64encode(out.getvalue()).decode()
        self.assertGreater(len(encoded), 4 * 1024 * 1024)  # Over the normal request limit.
        response = self.post(kind="idCardFront", file=encoded)
        self.assertEqual(response.status_code, 200, response.content)
        held = stored_documents(self.user)
        self.assertLessEqual(len(held["idCardFront"]), 100000)

    def test_unknown_kinds_are_refused(self):
        self.assertEqual(self.post(kind="passport", file=picture()).status_code, 400)


@override_settings(NINEPSB=CONFIG)
class UpgradeAccountNameTests(TestCase):
    """The bank names a wallet "MERCHANT/CUSTOMER NAME"; the customer sends only their own."""

    def setUp(self):
        self.user = User.objects.create_user(username="upgrade", first_name="Jane", last_name="Doe",
            phone="07041267880", email="jane@example.test", phone_verified=True, email_verified=True)
        self.wallet = Wallet.objects.create(user=self.user)
        self.bank = BankAccount.objects.create(wallet=self.wallet, number="1100000001", name="ZITCH/Jane Doe",
            environment="production", tier=1, pnd=False, frozen=False, status="active",
            checked_at=timezone.now(), available_balance=0, ledger_balance=0, opening_review_required=False)
        record_identity_proof(self.user, "bvn", "12345678901", source="9psb_otp")

    def test_invalid_tier_and_profile_types_fail_before_any_bank_request(self):
        with patch("banking.client.Client.wallet") as read, patch("banking.client.Client.upgrade") as send:
            for tier in (None, [], {}, 2.5, True):
                with self.subTest(tier=tier), self.assertRaisesRegex(ValueError, "Choose Tier"):
                    submit_upgrade(self.user, tier, {})
            for profile in (None, [], "invalid"):
                with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, "upgrade details"):
                    submit_upgrade(self.user, 2, profile)
        read.assert_not_called()
        send.assert_not_called()

    def submit(self, **changes):
        sent = {}

        def capture(number, tier, body):
            sent.update(body)
            return Reply({"status": "SUCCESS", "responseCode": "00"}, 200)

        with patch("banking.upgrades.update_account"), \
                patch("banking.upgrades.refresh_upgrade"), \
                patch("banking.client.Client.upgrade", side_effect=capture):
            return submit_upgrade(self.user, 2, profile(**changes)), sent

    def test_the_merchant_prefix_does_not_block_the_customers_own_name(self):
        result, sent = self.submit()
        self.assertTrue(result["pending"])
        self.assertEqual(sent["accountName"], "Jane Doe")

    def test_a_different_word_order_is_the_same_person_and_is_sent_as_the_bank_spells_it(self):
        result, sent = self.submit(accountName="DOE  JANE")
        self.assertTrue(result["pending"])
        self.assertEqual(sent["accountName"], "Jane Doe")

    def test_someone_elses_name_is_still_refused(self):
        with self.assertRaisesMessage(ValueError, "name confirmed on your bank account"):
            self.submit(accountName="John Smith")
        with self.assertRaisesMessage(ValueError, "name confirmed on your bank account"):
            self.submit(accountName="Jane Doe Smith")

    def test_app_submission_with_stored_documents_and_bvn_reaches_tier2_approval(self):
        from accounts.models import AccessToken, hash_identifier
        from .models import AccountUpgrade
        from .secrets import seal
        from .upgrades import store_document

        # Match the durable user fields written by completed BVN onboarding.
        self.user.bvn_hash, self.user.bvn_verified = hash_identifier("12345678901"), True
        self.user.recompute_tier()
        self.user.save(update_fields=["bvn_hash", "bvn_verified", "tier"])
        token = AccessToken.issue(self.user).key
        self.bank.encrypted_bvn = seal("12345678901")
        self.bank.save(update_fields=["encrypted_bvn"])
        status = self.client.post("/api/banking/status/", data={"access_token": token},
                                  content_type="application/json").json()
        self.assertEqual(status["account_name"], "Jane Doe")
        self.assertTrue(status["bvn_on_file"])
        body = profile(accountName=status["account_name"], bvn=None)
        for kind in ("userPhoto", "idCardFront", "utilityBill", "customerSignature"):
            store_document(self.user, kind, body.pop(kind))

        def readback(tier):
            return Reply({"status": "SUCCESS", "data": {
                "nuban": self.bank.number, "name": self.bank.name, "status": "Active", "tier": str(tier),
                "pndstatus": "InActive", "freezeStatus": "InActive", "ledgerBalance": "0.00",
                "availableBalance": "0.00"}}, 200)

        with patch("banking.client.Client.wallet", side_effect=[readback(1), readback(2)]), \
                patch("banking.client.Client.upgrade", return_value=Reply({"status": "SUCCESS"}, 200)) as sent, \
                patch("banking.client.Client.upgrade_status", return_value=Reply({"status": "SUCCESS", "data": {
                    "accountNumber": self.bank.number, "status": "APPROVED"}}, 200)):
            response = self.client.post("/api/banking/upgrade/", data={"access_token": token, "tier": 2,
                "profile": body}, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["success"], response.content)
        self.assertEqual(response.json()["bank_tier"], 2)
        self.assertEqual(sent.call_args.args[2]["accountName"], "Jane Doe")
        self.assertEqual(sent.call_args.args[2]["bvn"], "12345678901")
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 2)
        self.assertTrue(self.user.nin_verified)
        self.assertEqual(AccountUpgrade.objects.get().state, "approved")
