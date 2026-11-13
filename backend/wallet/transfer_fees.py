"""The partner bank's fee on a transfer to another bank, paid by the customer.

Wema takes its NIP charge plus 7.5% VAT from the sender's own account for every
transfer to another bank: ₦10 + ₦0.75 on a transfer of up to ₦5,000. The app
quoted that charge (``/api/transfers/charge/``) but the send flow never debited
it, so each transfer left the Zitch balance ₦10.75 above what the bank held,
and a customer sending their whole Zitch balance would be refused by the bank.

The fee is its own "Transfer fee" ledger row, written in the same database
transaction as the transfer's debit, so the balance has to cover both. It then
follows the transfer:

- transfer Successful -> fee Successful (no balance change);
- transfer Failed (refunded by any route) -> fee returned and marked Failed;
- transfer Pending, or held for review -> fee left as it is.

The payout state functions in ``wallet.services`` settle the fee as they change
the transfer. ``reconcile_transfer_fees`` (run by the Wema cron) catches any
route that changes a transfer without them. Lock order everywhere: transfer row,
then fee row, then wallet — the same transfer-before-wallet order every payout
transition already uses.

Fee rows never carry the keys that mark a bank payout (``bank``, ``account``,
``wema_transfer``...), so the payout poller never asks the bank about them, and
they are silent: the transfer's own alert names the fee.
"""
import logging
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction as db_transaction

log = logging.getLogger("wallet")

FEE_SERVICE = "Transfer fee"
FEE_FOR_KEY = "transfer_fee_for"
PAYOUT_FEE_KEY = "transfer_fee"
_KOBO = Decimal("0.01")

#: The standard NIP tariff, used only when the bank's own schedule cannot be read:
#: (upper bound inclusive, charge before VAT). The bank charges its actual fee
#: either way; refusing every transfer while its fee-schedule endpoint is down
#: would be worse than quoting the published tariff.
_FALLBACK_TARIFF = ((Decimal("5000"), Decimal("10")),
                    (Decimal("50000"), Decimal("25")),
                    (None, Decimal("50")))


def fee_reference(payout_reference: str) -> str:
    return f"{payout_reference}-FEE"


def _enabled() -> bool:
    return bool(getattr(settings, "TRANSFER_FEE_ENABLED", True))


def _free_destination(bank_code: str, bank_name: str) -> bool:
    """A transfer to another Wema account is on-us: Wema charges no NIP fee."""
    codes = {str(code).strip() for code in getattr(
        settings, "TRANSFER_FEE_FREE_BANK_CODES", ("035", "000017")) if str(code).strip()}
    if str(bank_code or "").strip() in codes:
        return True
    name = str(bank_name or "").casefold()
    return "wema" in name or "alat" in name


def _schedule_charge(amount: Decimal) -> Decimal:
    from utility.providers import payout_charge

    try:
        charge = payout_charge(amount)
    except Exception:  # noqa: BLE001 - fall back to the published tariff
        charge = None
    if charge is not None:
        return Decimal(str(charge))
    for upper, tariff in _FALLBACK_TARIFF:
        if upper is None or amount <= upper:
            return tariff
    return _FALLBACK_TARIFF[-1][1]


def transfer_fee(amount, bank_code: str = "", bank_name: str = "") -> Decimal:
    """The fee the bank will take for sending ``amount`` to this bank: charge + VAT."""
    if not _enabled() or _free_destination(bank_code, bank_name):
        return Decimal("0.00")
    amount = Decimal(str(amount))
    if amount <= 0:
        return Decimal("0.00")
    vat = Decimal(str(getattr(settings, "TRANSFER_FEE_VAT_RATE", "0.075")))
    charge = _schedule_charge(amount)
    return (charge * (Decimal("1") + vat)).quantize(_KOBO, rounding=ROUND_HALF_UP)


def debit_transfer_fee(user, payout, fee: Decimal):
    """Write the fee row for ``payout``. Call inside the payout debit's transaction.

    Raises ``InsufficientFunds`` when the balance left after the transfer cannot
    cover the fee; the caller's transaction then rolls the transfer back too.
    """
    from wallet.services import debit

    return debit(
        user, fee, FEE_SERVICE,
        meta={FEE_FOR_KEY: payout.reference, "suppress_transaction_alert": True},
        reference=fee_reference(payout.reference),
        # The transfer itself already passed the spending checks and limits.
        enforce_limits=False,
    )


@db_transaction.atomic
def settle_transfer_fee(payout) -> str:
    """Make the fee row follow its transfer. Idempotent; safe from any route.

    Returns "none" (no fee), "confirmed", "released", "held" or "unchanged".
    """
    from wallet.models import Transaction, Wallet
    from wallet.services import _active_reversal_quarantine

    payout = Transaction.objects.select_for_update().get(pk=payout.pk)
    fee_row = (Transaction.objects.select_for_update()
               .filter(reference=fee_reference(payout.reference), user_id=payout.user_id,
                       direction=Transaction.OUT).first())
    if fee_row is None:
        return "none"
    if _active_reversal_quarantine(payout):
        return "held"
    if payout.transaction_status == Transaction.SUCCESS:
        if fee_row.transaction_status == Transaction.PENDING:
            fee_row.transaction_status = Transaction.SUCCESS
            fee_row.save(update_fields=["transaction_status"])
            return "confirmed"
        return "unchanged"
    if payout.transaction_status == Transaction.FAILED:
        if fee_row.transaction_status in (Transaction.PENDING, Transaction.SUCCESS):
            wallet = Wallet.objects.select_for_update().get(user_id=payout.user_id)
            wallet.balance += fee_row.amount
            wallet.save(update_fields=["balance", "updated"])
            fee_row.transaction_status = Transaction.FAILED
            fee_row.save(update_fields=["transaction_status"])
            log.info("transfer_fee_released payout=%s fee=%s", payout.reference, fee_row.amount)
            return "released"
        return "unchanged"
    return "unchanged"


def reconcile_transfer_fees(limit: int = 500) -> dict:
    """Settle fee rows whose transfer was resolved by a route that did not settle them."""
    from wallet.models import Transaction

    counts = {"confirmed": 0, "released": 0}
    open_fees = (Transaction.objects
                 .filter(meta__has_key=FEE_FOR_KEY, direction=Transaction.OUT,
                         transaction_status__in=(Transaction.PENDING, Transaction.SUCCESS))
                 .order_by("created")[:max(1, int(limit))])
    for fee_row in open_fees:
        payout = Transaction.objects.filter(
            reference=(fee_row.meta or {}).get(FEE_FOR_KEY), user_id=fee_row.user_id,
            direction=Transaction.OUT).first()
        if payout is None or payout.transaction_status == Transaction.PENDING:
            continue
        if (payout.transaction_status == Transaction.SUCCESS
                and fee_row.transaction_status == Transaction.SUCCESS):
            continue
        try:
            outcome = settle_transfer_fee(payout)
        except Exception:  # noqa: BLE001 - one row must not stop the rest
            log.exception("transfer_fee_settle_failed payout=%s", payout.reference)
            continue
        if outcome in counts:
            counts[outcome] += 1
    return counts
