"""Partnership eligibility with immutable historical funding holds."""
import re
import hashlib

from wallet.models import Wallet, UnsettledFunding
from django.core.cache import cache
from django.db import connection, DatabaseError
from django.db.models import Q
import logging

log = logging.getLogger('wallet')


#: How long the retired provider's table list is trusted. The list is cached
#: because introspection is not free and the schema almost never moves; the
#: cost of that cache is that it can outlive a schema change, which is why
#: a query failure against a cached list is re-checked rather than believed.
SOURCE_TABLE_TTL = 300


def _source_table_names(key):
    """Introspect the retired provider's tables and remember them."""
    names = list(connection.introspection.table_names())
    cache.set(key, names, timeout=SOURCE_TABLE_TTL)
    return names


def _previous_provider_evidence(wallet_id, names):
    """Read the retired provider's tables for one wallet.

    Reads behind a savepoint on purpose. A stale table list makes the first
    statement fail, and the caller must still be able to query afterwards:
    inside a transaction (an ATOMIC_REQUESTS view, a wallet debit) a failed
    statement otherwise leaves the whole transaction unusable, so recovering
    from a stale list would be impossible -- and the caller's own later writes
    would fail too. In autocommit there is nothing to protect and Django makes
    all three calls no-ops, which is why they are used rather than atomic().
    """
    blockers = []
    sid = connection.savepoint()
    try:
        with connection.cursor() as cursor:
            if 'banking_bankaccount' in names:
                cursor.execute('SELECT EXISTS(SELECT 1 FROM banking_bankaccount WHERE wallet_id = %s)', [wallet_id])
                if cursor.fetchone()[0]:
                    blockers.append('previous_provider_account_review')
            if 'banking_previousbankarchive' in names:
                cursor.execute('''SELECT EXISTS(SELECT 1 FROM banking_previousbankarchive
                    WHERE wallet_id = %s AND (review_required = TRUE OR balance <> 0
                      OR pending_transactions > 0))''', [wallet_id])
                if cursor.fetchone()[0]:
                    blockers.append('archived_wema_balance_review')
    except Exception:
        connection.savepoint_rollback(sid)
        raise
    connection.savepoint_commit(sid)
    return blockers


def previous_provider_cutover_blockers(user):
    """Prevent 9PSB-era accounts/liabilities from being used as Wema funds.

    The former provider's tables remain read-only in the production database
    although its Django app has been retired. Bank records cannot be relabelled
    or treated as Wema balances merely because an account was reissued.

    Fails closed: if the evidence cannot be read, the caller is blocked. But
    "the cached table list names a table that is no longer there" is not that
    case, and must not be reported as it. The retired-schema cleanup drops
    those tables, so believing a stale list would block every customer's
    funding for the whole TTL, for tables that are simply gone.
    """
    wallet_id = Wallet.objects.filter(user_id=user.pk).values_list('id', flat=True).first()
    if not wallet_id:
        return []
    key = f'wema-restore:source-tables:{connection.alias}'
    names = cache.get(key)
    from_cache = names is not None
    try:
        return _previous_provider_evidence(
            wallet_id, names if from_cache else _source_table_names(key))
    except DatabaseError:
        if not from_cache:
            # Freshly introspected and still unreadable: genuinely unavailable.
            log.exception('wema_cutover_previous_provider_evidence_unavailable')
            return ['previous_provider_state_unavailable']
    # Decide on what the database actually has now, not on what it had up to
    # five minutes ago. Only a failure against tables it really carries is
    # "unavailable" -- and that still fails closed, below.
    cache.delete(key)
    try:
        return _previous_provider_evidence(wallet_id, _source_table_names(key))
    except DatabaseError:
        log.exception('wema_cutover_previous_provider_evidence_unavailable')
        return ['previous_provider_state_unavailable']



REVIEW_MESSAGE = (
    "Your account needs a balance review. Your money and transaction history are "
    "preserved. Contact support before adding or spending money."
)


_RETIRED_BANK_LABEL = (r"^\s*(9[ -]*psb|9[ -]*payment[ -]*service[ -]*bank"
                      r"([ -]*(ltd|limited|plc))?|(wema([ -]*bank)?|alat)[ -]+"
                      r"(vas|virtual[ -]*accounts?))\s*$")
_RETIRED_ACCOUNT_REFERENCE = r"^\s*(9psb|wema[-_ ]vas)([-_ ]|$)"


def retired_wallet_marker(bank_name, account_reference):
    """A retained legacy label or reference is sufficient to block reuse."""
    return bool(re.search(_RETIRED_BANK_LABEL, bank_name or "", re.I)
                or re.search(_RETIRED_ACCOUNT_REFERENCE, account_reference or "", re.I))


def retired_wallet_markers():
    return Q(bank_name__iregex=_RETIRED_BANK_LABEL) | Q(account_reference__iregex=_RETIRED_ACCOUNT_REFERENCE)


def legacy_funding_blockers(user):
    """Read-only; spend callers hold the wallet lock before evaluating exposure."""
    if UnsettledFunding.objects.filter(user_id=user.pk, archive__isnull=True).exists():
        return ["legacy_balance"]
    bank = Wallet.objects.filter(user_id=user.pk).values_list(
        "bank_name", "account_number", "account_reference").first()
    if bank and bank[1] and retired_wallet_marker(bank[0], bank[2]):
        return ["retired_provider_wallet_review"]
    return previous_provider_cutover_blockers(user)


def retired_funding_destination(number):
    """Reject retired destinations even after their account records are deleted."""
    return bool(isinstance(number, str) and number and (
        number.startswith("711")
        or UnsettledFunding.objects.filter(
            destination_digest=hashlib.sha256(number.encode()).hexdigest()).exists()))


def partnership_account_number(number):
    """Only a valid, non-retired bank number is usable for Partnership."""
    return (isinstance(number, str) and bool(re.fullmatch(r"[0-9]{10}", number))
            and not retired_funding_destination(number))


def partnership_allowed(user):
    if legacy_funding_blockers(user):
        return False
    number = Wallet.objects.filter(user_id=user.pk).values_list("account_number", flat=True).first()
    # Blank is eligible for first setup; a stored invalid/retired number needs
    # review before any channel can start a bank upgrade or verification flow.
    return not number or partnership_account_number(number)


def first_partnership_setup(user):
    """Preserve independent identity proof without replaying uncertain issuance."""
    from accounts.models import IdentityProof
    from wallet.models import WemaFaceSession, WemaProvisioningAttempt

    if not partnership_allowed(user):
        return False
    if (Wallet.objects.filter(user=user).exclude(account_number="").exists()
            or WemaFaceSession.objects.filter(user=user).exists()
            or WemaProvisioningAttempt.objects.filter(user=user).exists()
            or IdentityProof.objects.filter(user=user, source__in=[IdentityProof.WEMA_WALLET_OTP,
                IdentityProof.WEMA_FACE, IdentityProof.WEMA_TIER2]).exists()):
        return False
    return any(getattr(user, f"{kind}_verified", False) and getattr(user, f"{kind}_hash", "")
               and IdentityProof.objects.filter(user=user, identity_type=kind,
                   identity_hash=getattr(user, f"{kind}_hash"),
                   source=IdentityProof.IDENTITY_PROVIDER_OTP).exists()
               for kind in ("bvn", "nin"))
