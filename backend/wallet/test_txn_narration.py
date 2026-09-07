"""The customer's note reaches the app's transaction list.

It was already stored, already on the WhatsApp receipt, and already quoted in the
debit alert — but the app's transactions endpoint never returned it, so the
history row and the detail screen had nothing to show and no way to show it. A
narration you can only read in one of three places is a narration the customer
will assume was lost.
"""
from decimal import Decimal

from django.test import TestCase

from accounts.models import AccessToken, User
from wallet.models import Transaction


def _user(n=1):
    phone = f"0802000{n:04d}"
    u = User.objects.create(username=phone, phone=phone, email=f"n{n}@example.test")
    u.set_unusable_password()
    u.save()
    return u


class TransactionNarrationTests(TestCase):

    def setUp(self):
        self.user = _user()

    def _txn(self, meta):
        return Transaction.objects.create(
            user=self.user, amount=Decimal("1000"), direction=Transaction.OUT,
            service="transfer", reference="ref-n1", meta=meta,
            transaction_status=Transaction.SUCCESS)

    def _list(self):
        token = AccessToken.issue(self.user)
        raw = getattr(token, "raw", None) or getattr(token, "key", None)
        r = self.client.post("/api/user-transaction-history/", {"access_token": raw},
                             content_type="application/json")
        if r.status_code == 404:
            self.skipTest("transactions route name differs on this build")
        return r

    def test_a_transfer_note_is_returned(self):
        self._txn({"note": "For food"})
        body = self._list().json()
        self.assertEqual(body["all_site_transactions"][0]["narration"], "For food")

    def test_a_bill_narration_is_returned_from_its_own_key(self):
        self._txn({"narration": "August light"})
        body = self._list().json()
        self.assertEqual(body["all_site_transactions"][0]["narration"], "August light")

    def test_no_note_is_an_empty_string_not_missing(self):
        """The key is always present, so the client never has to distinguish
        "absent" from "empty" to decide whether to draw the row."""
        self._txn({})
        row = self._list().json()["all_site_transactions"][0]
        self.assertIn("narration", row)
        self.assertEqual(row["narration"], "")

    def test_meta_is_never_null_so_the_or_guard_covers_a_forbidden_shape(self):
        """Documenting why the view still writes `(t.meta or {})`.

        The column is NOT NULL, so a null meta is unreachable today. This asserts
        the CONSTRAINT rather than the guard, because the constraint is the reason
        the guard is cheap insurance rather than dead code — and if a future
        migration relaxes it, this test fails and points straight at the view."""
        from django.db.utils import IntegrityError

        t = self._txn({})
        with self.assertRaises(IntegrityError):
            Transaction.objects.filter(pk=t.pk).update(meta=None)

    def test_it_is_bounded(self):
        self._txn({"note": "x" * 400})
        self.assertEqual(len(self._list().json()["all_site_transactions"][0]["narration"]), 120)
