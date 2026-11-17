"""The CBN NUBAN check digit, shared by every surface that guesses a bank.

A 10-digit NUBAN is a 9-digit serial plus a check digit, and the bank's code is
baked into that check digit: weights 3,7,3 cycle over bank_code + serial, and
check = (10 - sum mod 10) mod 10. So the check digit can say which banks a
number is *valid* for. It narrows, it never proves: several banks can pass.
"""


def nuban_valid(account: str, bank_code: str) -> bool:
    """True when ``account`` passes the NUBAN check digit for ``bank_code``."""
    digits = "".join(ch for ch in str(account or "") if ch.isdigit())
    code = "".join(ch for ch in str(bank_code or "") if ch.isdigit())
    if len(digits) != 10 or not code:
        return False
    serial, check = digits[:9], int(digits[9])
    total = sum(int(c) * (3, 7, 3)[i % 3] for i, c in enumerate(code + serial))
    return (10 - total % 10) % 10 == check
