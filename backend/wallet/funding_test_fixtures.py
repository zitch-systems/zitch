"""Synthetic outstanding-liability evidence; no retired provider models."""
import hashlib

from wallet.models import UnsettledFunding
from wallet.services import credit


def funding_hold(user, amount="1000", number="9990000001", *, credit_funds=True):
    txn = credit(user, amount, "Historical funding") if credit_funds else None
    return UnsettledFunding.objects.create(
        user=user, amount=amount, destination_digest=hashlib.sha256(number.encode()).hexdigest(),
        operator_reference="historical-test-evidence",
        evidence={"credits": [{"transaction_id": txn.pk, "amount": str(txn.amount)}] if txn else []})
