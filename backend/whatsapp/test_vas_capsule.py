"""Encrypted identity recovery is short-lived and bound to one signed session."""
import json
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from accounts.models import hash_identifier
from wema_vas.identity import cipher
from wema_vas.test_enrollment import SETTINGS
from whatsapp import vas_capsule
from whatsapp.models import PendingAction


@override_settings(WEMA_VAS=SETTINGS)
class VasCapsuleTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.number = "12345678901"
        self.pa = PendingAction(pk=101, user_id=202, action_type="vas_enroll", state="flow_vas",
            expires_at=timezone.now() + timedelta(minutes=15), payload={
                "consent": True, "consent_at": timezone.now().isoformat(),
                "nonce": "session-nonce", "credentials": "credentials-binding", "link_id": 303,
                "flow_id": "approved-flow", "contract": "contract-digest", "mode": "validation",
                "consent_version": "version", "id_kind": "bvn", "identity_hash": hash_identifier(self.number),
            })

    def store(self):
        vas_capsule.store(self.pa, self.number)
        return vas_capsule._key(self.pa.payload[vas_capsule.FIELD])

    def test_only_ciphertext_is_cached_and_database_has_an_opaque_handle(self):
        with patch.object(cache, "add", wraps=cache.add) as added:
            key = self.store()
        encrypted = cache.get(key)
        self.assertIsInstance(encrypted, str)
        self.assertNotIn(self.number, encrypted)
        self.assertNotIn(self.number, json.dumps(self.pa.payload))
        self.assertRegex(key, r"^wa-vas-identity:[0-9a-f]{32}$")
        self.assertGreater(added.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(added.call_args.kwargs["timeout"], 900)
        self.assertEqual(vas_capsule.recover(self.pa), self.number)

    def test_cache_expiry_never_outlives_the_session_or_fifteen_minutes(self):
        for seconds in (12, 30 * 60):
            with self.subTest(seconds=seconds):
                self.pa.expires_at = timezone.now() + timedelta(seconds=seconds)
                with patch.object(cache, "add", wraps=cache.add) as added:
                    self.store()
                self.assertLessEqual(added.call_args.kwargs["timeout"], min(seconds, 900))

    def test_capsule_cannot_be_transplanted_or_rebound(self):
        self.store()
        for key, value in (("nonce", "other-session"), ("credentials", "changed"),
                ("link_id", 999), ("flow_id", "other-flow"), ("contract", "other-contract"),
                ("mode", "live"), ("consent_version", "other-version"), ("consent_at", "changed"),
                ("id_kind", "nin"), ("identity_hash", hash_identifier("99999999999"))):
            with self.subTest(binding=key):
                other = deepcopy(self.pa)
                other.payload[key] = value
                with self.assertRaises(vas_capsule.CapsuleUnavailable):
                    vas_capsule.recover(other)
        for key, value in (("pk", 404), ("user_id", 505),
                ("expires_at", self.pa.expires_at + timedelta(seconds=1))):
            with self.subTest(binding=key):
                other = deepcopy(self.pa)
                setattr(other, key, value)
                with self.assertRaises(vas_capsule.CapsuleUnavailable):
                    vas_capsule.recover(other)

    def test_missing_altered_or_expired_ciphertext_cannot_be_recovered(self):
        key = self.store()
        encrypted = cache.get(key)
        for value in (None, encrypted[:-2] + "xx", "not-encrypted"):
            with self.subTest(value_type="missing" if value is None else "invalid"):
                cache.set(key, value)
                with self.assertRaises(vas_capsule.CapsuleUnavailable):
                    vas_capsule.recover(self.pa)
        cache.set(key, encrypted)
        with patch("whatsapp.vas_capsule.timezone.now", return_value=self.pa.expires_at):
            with self.assertRaises(vas_capsule.CapsuleUnavailable):
                vas_capsule.recover(self.pa)

    def test_encryption_age_is_enforced_even_if_a_cache_keeps_old_data(self):
        self.pa.expires_at = timezone.now() + timedelta(minutes=30)
        key = self.store()
        with patch("whatsapp.vas_capsule.timezone.now", return_value=timezone.now() + timedelta(minutes=16)):
            with self.assertRaises(vas_capsule.CapsuleUnavailable):
                vas_capsule.recover(self.pa)

    def test_ciphertext_with_wrong_identifier_or_purpose_is_rejected(self):
        key = self.store()
        envelope = json.loads(cipher().decrypt(cache.get(key).encode()))
        for change in ("number", "purpose"):
            value = deepcopy(envelope)
            if change == "number":
                value["number"] = "99999999999"
            else:
                value["binding"]["purpose"] = "other-purpose"
            cache.set(key, cipher().encrypt(json.dumps(value).encode()).decode())
            with self.assertRaises(vas_capsule.CapsuleUnavailable):
                vas_capsule.recover(self.pa)

    def test_key_rotation_keeps_old_capsules_only_while_the_old_key_is_retained(self):
        self.store()
        replacement = Fernet.generate_key().decode()
        with override_settings(WEMA_VAS={**SETTINGS, "IDENTITY_KEYS": [replacement, *SETTINGS["IDENTITY_KEYS"]]}):
            self.assertEqual(vas_capsule.recover(self.pa), self.number)
        with override_settings(WEMA_VAS={**SETTINGS, "IDENTITY_KEYS": [replacement]}):
            with self.assertRaises(vas_capsule.CapsuleUnavailable):
                vas_capsule.recover(self.pa)

    def test_storage_outage_or_denied_consent_never_returns_an_armed_capsule(self):
        with patch.object(cache, "add", side_effect=RuntimeError("cache unavailable")):
            with self.assertRaises(vas_capsule.CapsuleUnavailable):
                self.store()
        self.assertNotIn(vas_capsule.FIELD, self.pa.payload)
        self.pa.payload["consent"] = False
        with patch.object(cache, "add") as added:
            with self.assertRaises(vas_capsule.CapsuleUnavailable):
                self.store()
        added.assert_not_called()
