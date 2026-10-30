"""Synthetic bank eligibility for tests of shared application controls.

Fixtures create evidence directly; they never call a bank or open a customer
activation switch in production. Each caller explicitly overrides NINEPSB.
"""
from decimal import Decimal

from django.utils import timezone

from accounts.models import hash_identifier, record_identity_proof
from wallet.models import Wallet
from .models import AccountUpgrade, AddressVerification, BankAccount


class BankIdentityAPI:
    """Drive the public identity API; replace only the provider HTTP boundary."""

    def identity_profile(self, user):
        return {"lastName": user.last_name, "otherNames": user.first_name,
                "phoneNo": user.phone, "email": user.email, "gender": 1,
                "dateOfBirth": "01/01/1990", "address": "1 Test Road",
                "ninUserId": "SYNTHETIC-NIN-USER"}

    def identity_post(self, user, token, kind="bvn", identifier="12345678901", **extra):
        return self.client.post("/api/banking/identity/start/", data={
            "access_token": token, "kind": kind, "identifier": identifier,
            "profile": self.identity_profile(user), **extra}, content_type="application/json")

    def identity_confirm(self, token, reference, otp="654321"):
        return self.client.post("/api/banking/identity/confirm/", data={
            "access_token": token, "reference": reference, "otp": otp}, content_type="application/json")

    def bank_wallet_reply(self, user, *, tier=1, amount="0.00"):
        from .client import Reply
        return Reply({"status": "SUCCESS", "data": {
            "number": "INTERNAL-WALLET", "nuban": f"19{user.pk:08d}",
            "name": user.get_full_name(), "status": "Active", "tier": str(tier),
            "pndstatus": "InActive", "freezeStatus": "InActive",
            "ledgerBalance": amount, "availableBalance": amount}}, 200)

    def pending_identity(self, user, token, kind="bvn", identifier="12345678901"):
        from unittest.mock import patch
        from .client import Reply
        with patch("banking.client.Client.initiate_identity", return_value=Reply({"message": "PENDING"}, 200)):
            response = self.identity_post(user, token, kind, identifier)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["otp_required"])
        return response.json()["reference"]

    def complete_identity(self, user, token, kind="bvn", identifier="12345678901"):
        from unittest.mock import patch
        from .client import Reply
        reference = self.pending_identity(user, token, kind, identifier)
        with patch("banking.client.Client.verify_identity", return_value=Reply({"message": "SUCCESS"}, 200)), \
             patch("banking.client.Client.open_wallet", return_value=Reply({"status": "SUCCESS", "data": {
                 "accountNumber": f"19{user.pk:08d}", "fullName": user.get_full_name()}}, 200)), \
             patch("banking.client.Client.wallet", return_value=self.bank_wallet_reply(user)):
            response = self.identity_confirm(token, reference)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["success"], response.content)
        return response.json()

    def upgrade_profile(self, user):
        import base64
        from io import BytesIO
        from PIL import Image
        image = BytesIO()
        Image.new("RGB", (20, 20), "white").save(image, format="PNG")
        document = base64.b64encode(image.getvalue()).decode()
        return {"bvn": "12345678901", "nin": "10987654321", "accountName": user.get_full_name(),
                "phoneNumber": user.phone, "email": user.email, "idType": "1", "idIssueDate": "2020-01-01",
                "houseNumber": "1", "streetName": "Test Road", "state": "Lagos", "city": "Ikeja",
                "localGovernment": "Ikeja", "pep": "NO", "nearestLandmark": "Test school",
                **{key: document for key in ("userPhoto", "idCardFront", "idCardBack", "utilityBill", "customerSignature")}}

    def approve_tier2(self, user, token):
        from unittest.mock import patch
        from .client import Reply
        amount = str(Wallet.objects.get(user=user).balance)
        with patch("banking.client.Client.wallet", side_effect=[
                self.bank_wallet_reply(user, amount=amount), self.bank_wallet_reply(user, tier=2, amount=amount)]), \
             patch("banking.client.Client.upgrade", return_value=Reply({"status": "SUCCESS"}, 200)), \
             patch("banking.client.Client.upgrade_status", return_value=Reply({"status": "SUCCESS", "data": {
                 "accountNumber": f"19{user.pk:08d}", "status": "APPROVED"}}, 200)):
            response = self.client.post("/api/banking/upgrade/", data={"access_token": token,
                "tier": 2, "profile": self.upgrade_profile(user)}, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["success"], response.content)
        user.refresh_from_db()
        self.assertEqual(user.tier, 2)


def bind_test_bank(user, *, tier=None, available_balance=None):
    tier = user.tier if tier is None else tier
    wallet, _ = Wallet.objects.get_or_create(user=user)
    number = f"19{user.pk:08d}"
    name = user.get_full_name() or "Test Customer"
    wallet.account_number, wallet.account_name = number, name
    wallet.bank_name, wallet.bank_tier = "9PSB", tier
    wallet.save(update_fields=["account_number", "account_name", "bank_name", "bank_tier"])
    amount = wallet.balance if available_balance is None else Decimal(str(available_balance))
    bank, _ = BankAccount.objects.update_or_create(wallet=wallet, defaults={
        "number": number, "name": name, "environment": "production", "tier": tier,
        "pnd": False, "frozen": False, "status": "active", "checked_at": timezone.now(),
        "available_balance": amount, "ledger_balance": amount, "opening_review_required": False})
    if tier >= 1:
        record_identity_proof(user, "bvn", f"{10000000000 + user.pk}", source="9psb_otp")
    if tier >= 2:
        record_identity_proof(user, "nin", f"{20000000000 + user.pk}", source="9psb_otp")
        address = None
        if tier == 3:
            address, _ = AddressVerification.objects.get_or_create(user=user,
                reference=f"TEST-ADDRESS-{user.pk}", defaults={"address_digest": "test-address",
                "encrypted_address": "test-fixture", "state": "verified"})
            user.address_verified = True
        AccountUpgrade.objects.get_or_create(account=bank, reference=f"TEST-UPGRADE-{user.pk}", defaults={
            "tier": tier, "bvn_hash": hash_identifier(f"{10000000000 + user.pk}"),
            "nin_hash": hash_identifier(f"{20000000000 + user.pk}"), "request_digest": "test-upgrade",
            "state": "approved", "address_verification": address})
    user.recompute_tier()
    user.save(update_fields=["tier", "address_verified"])
    return bank


def make_bank_user(*args, bank_balance=None, **kwargs):
    from wallet.tests import make_user
    user, token = make_user(*args, **kwargs)
    bind_test_bank(user, available_balance=bank_balance)
    return user, token


def mock_bank_io(case, *, directory=False):
    """Replace only bank HTTP calls in shared ledger/control tests."""
    from unittest.mock import patch
    from .client import Reply
    if directory:
        from transfers.models import Bank
        Bank.objects.get_or_create(code="9psb", defaults={
            "name": "9 Payment Service Bank", "bank_code": "120001"})

    def wallet(number):
        bank = BankAccount.objects.get(number=number)
        return Reply({"status": "SUCCESS", "data": {
            "number": "INTERNAL-WALLET", "nuban": bank.number, "name": bank.name,
            "status": "Active", "tier": str(bank.tier),
            "pndstatus": "Active" if bank.pnd else "InActive",
            "freezeStatus": "Active" if bank.frozen else "InActive",
            "availableBalance": str(bank.available_balance),
            "ledgerBalance": str(bank.ledger_balance)}}, 200)

    def resolve(number, code):
        bank = BankAccount.objects.filter(number=number).first()
        return Reply({"code": "00", "customer": {"account": {
            "number": number, "bank": code,
            "name": bank.name if bank else "JOHN DOE"}}}, 200)

    case.wallet_read = case.enterContext(patch("banking.client.Client.wallet", side_effect=wallet))
    case.enquiry = case.enterContext(patch("banking.client.Client.resolve", side_effect=resolve))
    case.bank_send = case.enterContext(patch("banking.client.Client.transfer",
        return_value=Reply({"status": "SUCCESS", "responseCode": "00"}, 200)))


def bank_channel_case(cls):
    """Channel tests exercise real authorization with synthetic bank HTTP I/O."""
    from django.test import SimpleTestCase, TestCase, override_settings
    from .test_client import CONFIG
    if not issubclass(cls, SimpleTestCase):
        return cls
    setup = cls.setUp

    def setUp(self):
        mock_bank_io(self)
        setup(self)
        if isinstance(self, TestCase):
            # Older channel fixtures express eligibility as verified identity
            # flags. Supply the corresponding current-bank evidence explicitly
            # in this test decorator, never in the application's auth path.
            from accounts.models import User
            for user in User.objects.filter(is_active=True, phone_verified=True, email_verified=True):
                if (user.bvn_verified or user.nin_verified) and not BankAccount.objects.filter(wallet__user=user).exists():
                    bind_test_bank(user)

    cls.setUp = setUp
    return override_settings(NINEPSB={**CONFIG, "TRANSFER_FEE_TOTAL": "0.00"})(cls)
