"""Which bank a customer means, read from the words they actually typed.

The question used to be answered with substring tests over the whole message: is
the message inside a bank's name, or a bank's name inside the message? Neither is
how people write. "Transfer to access 1668720613 centrac enterprise 1800" holds no
bank's full name and no alias of one, so it matched no bank at all, fell back to
every bank the account number's check digit allows, and offered AAA Finance, ABU
Microfinance Bank and Access Bank, in alphabetical order, as the first of six
choices for a bank the customer had already named.

So the message is read a word at a time. Every phrase that names a bank is a
*mention*, and a mention is one of two strengths:

* STRONG: a bank's full name, a short name people use ("gt", "tg", "first",
  "access", "uba"), a known alias ("guaranty trust", "alat"), several
  distinctive words together ("titan trust"), or a distinctive word with a bank
  word beside it ("aella mfb").
* WEAK: the one distinctive word of a bank nobody shortens ("aella" for Aella
  Microfinance Bank). The rail carries about a thousand institutions, and many of
  those words are also first names or ordinary words ("ada", "grace", "peace").
  So a weak mention never picks a bank by itself. It only moves that bank up the
  list the customer picks from.

Some short names are ordinary English words too ("first", "union", "hope"). They
are strong only where the message uses them as a bank: next to the account number,
followed by "bank" or "mfb", or as the whole answer to "which bank?". "Send 5k to
my first son 0123456789 gtb" names GTBank, not First Bank.

The account number's CBN check digit then narrows what the words left open. Under
all of it sits the name enquiry before the confirm card: no bank chosen here moves
money until the customer has seen the holder's name and the bank, and approved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .bank_aliases import BANK_ALIASES

STRONG = 2
WEAK = 1

_TOKEN = re.compile(r"[a-z0-9]+")

#: Words that say "a bank" without saying which. Never a mention on their own.
GENERIC = frozenset({
    "bank", "banks", "bnk", "plc", "ltd", "limited", "nigeria", "nigerian", "ng", "nig",
    "mfb", "mfbank", "microfinance", "micro", "finance", "financial", "psb", "payment",
    "payments", "service", "services", "digital", "company", "co", "of", "for", "and",
    "the", "merchant", "mortgage", "mfi", "group", "holdings", "holding",
})

#: Words that mark the word before them as a bank's name: "aella mfb", "first bank".
MARKERS = frozenset({"bank", "bnk", "mfb", "microfinance", "psb", "plc"})

#: Words that can sit between a bank and its account number without breaking the
#: link: "to first 0123456789", "access account no 0123456789".
_FILLER = frozenset({
    "to", "at", "in", "with", "via", "on", "into", "account", "acct", "acc", "ac", "a",
    "c", "no", "number", "num", "nuban", "bank", "my", "his", "her", "their", "the",
    "of", "is", "its", "it",
})

#: Ordinary chat words never indexed as a weak mention, whatever a microfinance
#: bank happens to be called. "send money to my brother" names no bank.
_STOPWORDS = frozenset({
    "send", "sent", "transfer", "pay", "paid", "money", "cash", "naira", "ngn", "to",
    "my", "me", "for", "from", "want", "the", "and", "this", "that", "with", "via",
    "into", "abeg", "pls", "please", "sir", "mum", "mom", "dad", "wife", "husband",
    "brother", "sister", "friend", "account", "number", "acct", "now", "today",
    "help", "thanks", "thank", "you", "kindly", "urgent", "fast", "quick", "new",
    "old", "one", "two", "three", "five", "ten", "hundred", "thousand", "million",
    "the", "best", "good", "great", "home", "life", "first", "union", "unity",
    "hope", "trust", "capital", "global", "people", "standard", "royal",
})

#: The short names people use for the banks we carry, by our own slug. Several
#: are new here: "tg" and "gt" for GTBank, and "first", which bank_aliases leaves
#: out because First Bank and FCMB both have a claim to it. In Nigeria "first"
#: means First Bank, so it is First Bank here, with FCMB kept as the alternate
#: (see _ALTERNATES) for when the account number fits FCMB and not First Bank.
NICKNAMES = {
    "access": "access", "accessbank": "access", "diamond": "access",
    "gt": "gtb", "gtb": "gtb", "tg": "gtb", "gtbank": "gtb", "guaranty": "gtb",
    "gtco": "gtb", "gtbnk": "gtb", "guarantytrust": "gtb",
    "zenith": "zenith", "zenithbank": "zenith",
    "uba": "uba",
    "first": "firstbank", "fbn": "firstbank", "firstbank": "firstbank",
    "fcmb": "fcmb", "first city": "fcmb", "first city bank": "fcmb",
    "fidelity": "fidelity", "fidelitybank": "fidelity",
    "wema": "wema", "alat": "wema", "wemabank": "wema",
    "opay": "opay", "paycom": "opay",
    "palmpay": "palmpay", "palm": "palmpay",
    "kuda": "kuda", "kudabank": "kuda",
    "moniepoint": "moniepoint", "monie": "moniepoint", "moniepointmfb": "moniepoint",
    "sterling": "sterling", "sterlingbank": "sterling",
    "union": "union", "ubn": "union", "unionbank": "union",
    "stanbic": "stanbic", "ibtc": "stanbic", "stanbicibtc": "stanbic",
    "ecobank": "ecobank", "eco": "ecobank",
    "carbon": "carbon",
    "citi": "citi", "citibank": "citi",
    "fairmoney": "fairmoney",
    "globus": "globus",
    "gomoney": "gomoney",
    "hope": "hope",
    "jaiz": "jaiz",
    "keystone": "keystone",
    "lotus": "lotus",
    "mint": "mint", "mintyn": "mint",
    "momo": "momo", "mtn": "momo",
    "nova": "nova",
    "optimus": "optimus",
    "parallex": "parallex",
    "polaris": "polaris", "skye": "polaris",
    "premiumtrust": "premiumtrust",
    "providus": "providus",
    "unity": "unity", "unitybank": "unity",
    "rubies": "rubies",
    "scb": "scb", "stanchart": "scb",
    "signature": "signature",
    "smartcash": "smartcash", "airtel": "smartcash",
    "sparkle": "sparkle",
    "suntrust": "suntrust", "stb": "suntrust",
    "taj": "taj", "tajbank": "taj",
    "titan": "titan",
    "vbank": "vbank", "vfd": "vbank",
}

#: Short names that are also ordinary words, telco brands or first names. They
#: name a bank only where the message uses them as one (see _used_as_bank).
RISKY = frozenset({
    "first", "union", "unity", "hope", "signature", "carbon", "mint", "lotus", "nova",
    "sparkle", "keystone", "titan", "optimus", "eco", "palm", "monie", "mtn", "airtel",
    "diamond", "citi", "skye",
})

#: What else the customer may mean by a short name, tried when the account number
#: does not fit the first reading.
_ALTERNATES = {"first": ("fcmb",)}

#: Names each slug is listed under, here or on the rail. A row is that bank when
#: its slug matches or its name reads the same as one of these, so the short names
#: still work against a table whose rows were imported under the rail's names.
CANONICAL_NAMES = {
    "access": ("Access Bank",),
    "gtb": ("GTBank", "Guaranty Trust Bank", "GT Bank"),
    "zenith": ("Zenith Bank",),
    "uba": ("UBA", "United Bank for Africa"),
    "firstbank": ("First Bank", "First Bank of Nigeria", "FirstBank"),
    "fcmb": ("FCMB", "First City Monument Bank"),
    "fidelity": ("Fidelity Bank",),
    "wema": ("Wema Bank", "ALAT by Wema"),
    "opay": ("OPay", "OPay Digital Services", "Paycom"),
    "palmpay": ("PalmPay",),
    "kuda": ("Kuda", "Kuda Microfinance Bank"),
    "moniepoint": ("Moniepoint MFB", "Moniepoint Microfinance Bank"),
    "sterling": ("Sterling Bank",),
    "union": ("Union Bank", "Union Bank of Nigeria"),
    "stanbic": ("Stanbic IBTC", "Stanbic IBTC Bank"),
    "ecobank": ("Ecobank", "Ecobank Nigeria"),
    "carbon": ("Carbon", "Carbon MFB"),
    "citi": ("Citibank Nigeria", "Citibank", "Citi Bank"),
    "fairmoney": ("FairMoney MFB", "FairMoney Microfinance Bank"),
    "globus": ("Globus Bank",),
    "gomoney": ("GoMoney",),
    "hope": ("Hope PSB",),
    "jaiz": ("Jaiz Bank",),
    "keystone": ("Keystone Bank",),
    "lotus": ("Lotus Bank",),
    "mint": ("Mint MFB", "Mint-Finex MFB"),
    "momo": ("MoMo PSB (MTN)", "MoMo PSB"),
    "nova": ("Nova Bank", "Nova Merchant Bank"),
    "optimus": ("Optimus Bank",),
    "parallex": ("Parallex Bank",),
    "polaris": ("Polaris Bank",),
    "premiumtrust": ("PremiumTrust Bank",),
    "providus": ("Providus Bank",),
    "unity": ("Unity Bank",),
    "rubies": ("Rubies MFB",),
    "scb": ("Standard Chartered", "Standard Chartered Bank"),
    "signature": ("Signature Bank",),
    "smartcash": ("SmartCash PSB (Airtel)", "SmartCash PSB"),
    "sparkle": ("Sparkle MFB",),
    "suntrust": ("SunTrust Bank",),
    "taj": ("TAJ Bank",),
    "titan": ("Titan Trust Bank",),
    "vbank": ("V Bank (VFD MFB)", "VFD MFB", "VFD Microfinance Bank"),
}


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(str(text or "").lower())


def _core(words) -> list[str]:
    return [w for w in words if w not in GENERIC]


def _name_key(name: str) -> frozenset:
    """A bank name with its generic words dropped, as a set: "MONIEPOINT
    MICROFINANCE BANK" and "Moniepoint MFB" are the same bank."""
    return frozenset(_core(tokens(name)))


_CANONICAL_KEYS = {slug: {_name_key(n) for n in names} - {frozenset()}
                   for slug, names in CANONICAL_NAMES.items()}


def _within_one_edit(a: str, b: str) -> bool:
    """True when `a` is `b` with one letter wrong, missing, extra or swapped."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return (len(diff) == 2 and diff[1] == diff[0] + 1
                and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if la < lb else (b, a)
    for i in range(len(long_)):
        if long_[:i] + long_[i + 1:] == short:
            return True
    return False


@dataclass
class Mention:
    start: int
    end: int
    phrase: str
    #: bank pk -> (bank, strength)
    banks: dict
    fuzzy: bool = False


@dataclass
class Resolution:
    """What the words and the account number together say about the bank.

    `bank` is set when the message names one bank (or the account number can only
    be at one); otherwise `choices` is the ranked list to ask from, which can be
    empty: then ask for the bank's name.
    """
    bank: object = None
    choices: list = field(default_factory=list)
    #: The words that named the bank, when some did ("access", "gt").
    phrase: str = ""
    #: Whether the message named a bank at all (a strong mention).
    named: bool = False


class BankIndex:
    """Every phrase that names one of `banks`, with how strongly it does."""

    def __init__(self, banks):
        self.banks = list(banks)
        self.phrases: dict[tuple, dict] = {}
        self.slugs: dict[str, list] = {}
        self._fuzzy: dict[str, str] = {}
        for bank in self.banks:
            self._index(bank)
        # Short names map to EVERY row that is that bank, by slug or by name, so
        # "gt" still finds a table that lists it as "GUARANTY TRUST BANK".
        for nick, slug in NICKNAMES.items():
            rows = self.rows_for_slug(slug)
            if not rows:
                continue
            for bank in rows:
                self._add(tuple(tokens(nick)), bank, STRONG)
            if len(nick) >= 5 and " " not in nick and nick not in RISKY:
                self._fuzzy.setdefault(nick, slug)
        for slug, (_short, names) in BANK_ALIASES.items():
            for bank in self.rows_for_slug(slug):
                for alias in names:
                    words = tuple(tokens(alias))
                    if words:
                        self._add(words, bank, STRONG)

    def rows_for_slug(self, slug: str) -> list:
        if slug not in self.slugs:
            keys = _CANONICAL_KEYS.get(slug, set())
            self.slugs[slug] = [b for b in self.banks
                                if b.code == slug or _name_key(b.name) in keys]
        return self.slugs[slug]

    def _add(self, words: tuple, bank, strength: int) -> None:
        slot = self.phrases.setdefault(words, {})
        known = slot.get(bank.pk)
        if known is None or known[1] < strength:
            slot[bank.pk] = (bank, strength)

    def _index(self, bank) -> None:
        name = bank.name or ""
        whole = tuple(tokens(name))
        if not whole:
            return
        self._add(whole, bank, STRONG)
        if len(whole) > 1:
            self._add(("".join(whole),), bank, STRONG)       # "accessbank", "gtbank"
        # "V Bank (VFD MFB)" is called both of the things it says.
        parts = [tuple(tokens(p)) for p in re.split(r"[()\[\]/,]", name)]
        for words in {whole, *[p for p in parts if p]}:
            core = _core(words)
            if not core:
                continue
            if len(core) >= 2:
                self._add(tuple(core), bank, STRONG)          # "titan trust"
                squashed = "".join(core)
                if len(squashed) >= 6:
                    self._add((squashed,), bank, STRONG)      # "premiumtrust"
            elif len(core[0]) >= 3 and not core[0].isdigit() and core[0] not in _STOPWORDS:
                self._add((core[0],), bank, WEAK)             # "aella"
            for marker in MARKERS:
                self._add((*core, marker), bank, STRONG)      # "aella mfb"

    def fuzzy_slug(self, word: str) -> str:
        """The bank a misspelt short name was meant to be, when only one fits."""
        if len(word) < 5:
            return ""
        hits = {slug for nick, slug in self._fuzzy.items() if _within_one_edit(word, nick)}
        return hits.pop() if len(hits) == 1 else ""


def _used_as_bank(words, start, end, account_positions, whole_answer) -> bool:
    """Whether a short name that is also an ordinary word (RISKY) is used as a
    bank at words[start:end].

    Only three places count: the whole answer to "which bank?", right before a
    bank word ("first bank", "hope psb"), and right AFTER the account number
    ("0123456789 first"). Right before it does not: "send 5k to Hope 0123456789"
    is how people name the person they are paying.
    """
    if whole_answer:
        return True
    if end < len(words) and words[end] in MARKERS:
        return True
    return any(pos < start and all(w in _FILLER for w in words[pos + 1:start])
               for pos in account_positions)


def find_mentions(text: str, index: BankIndex, *, account: str = "",
                  whole_answer: bool = False) -> list[Mention]:
    """Every phrase in `text` that names a bank, longest first.

    `whole_answer` is for a reply to "which bank?" or the form's bank field, where
    the whole text is meant as a bank name and every word may be one.
    """
    words = tokens(text)
    positions = [i for i, w in enumerate(words) if account and w == account]
    used = [False] * len(words)
    found: list[Mention] = []
    for n in range(min(6, len(words)), 0, -1):
        for i in range(0, len(words) - n + 1):
            if any(used[i:i + n]):
                continue
            key = tuple(words[i:i + n])
            hits = index.phrases.get(key)
            if not hits:
                continue
            as_bank = (n > 1 or key[0] not in RISKY
                       or _used_as_bank(words, i, i + n, positions, whole_answer))
            banks = {}
            for pk, (bank, strength) in hits.items():
                if not as_bank:
                    strength = WEAK
                banks[pk] = (bank, strength)
            found.append(Mention(i, i + n, " ".join(key), banks))
            for j in range(i, i + n):
                used[j] = True
    for i, word in enumerate(words):
        if used[i] or word.isdigit():
            continue
        slug = index.fuzzy_slug(word)
        rows = index.rows_for_slug(slug) if slug else []
        if rows:
            found.append(Mention(i, i + 1, word, {b.pk: (b, STRONG) for b in rows}, fuzzy=True))
            used[i] = True
    found.sort(key=lambda m: m.start)
    return found


def _curated(bank) -> bool:
    return bank.code in CANONICAL_NAMES


def _dedupe(banks: list) -> list:
    """One row per bank. Two rows with one bank code, or with names that read the
    same ("ACCESS BANK" imported beside our "Access Bank"), are one bank listed
    twice; the first one kept is the best ranked."""
    seen, out = set(), []
    for bank in banks:
        code = "".join(ch for ch in (bank.bank_code or "") if ch.isdigit())
        keys = {f"code:{code}" if code else f"pk:{bank.pk}", _name_key(bank.name)}
        keys.discard(frozenset())
        if keys & seen:
            continue
        seen |= keys
        out.append(bank)
    return out


def _ranked(banks, *, scores=None, nuban=()) -> list:
    """Best guess first: what the message said, then the check digit, then the
    banks people actually send to, then the name."""
    nuban_pks = {b.pk for b in nuban}
    scores = scores or {}
    ordered = sorted(banks, key=lambda b: (-scores.get(b.pk, 0), b.pk not in nuban_pks,
                                           not getattr(b, "popular", False),
                                           not _curated(b), (b.name or "").lower()))
    return _dedupe(ordered)


def rank(banks) -> list:
    """`banks` best-known first (popular, then ours, then by name), one row each."""
    return _ranked(list(banks))


def strongest(mentions) -> tuple[int, dict, str]:
    """(strength, {pk: bank}, phrase) of the best mentions in a message."""
    best, banks, phrases = 0, {}, []
    for m in mentions:
        for pk, (bank, strength) in m.banks.items():
            if strength > best:
                best, banks, phrases = strength, {}, []
            if strength == best:
                banks[pk] = bank
                if m.phrase not in phrases:
                    phrases.append(m.phrase)
    return best, banks, " ".join(phrases)


def alternates(text: str, banks, fitting_pks) -> list:
    """Banks a short name could also mean that the account number fits ("first"
    -> FCMB), for when the first reading does not."""
    index = BankIndex(banks)
    return _dedupe([b for word in tokens(text) for slug in _ALTERNATES.get(word, ())
                    for b in index.rows_for_slug(slug) if b.pk in fitting_pks])


def match_banks(text: str, banks) -> list:
    """Banks the text names, as an answer to "which bank?" (the form's Bank field,
    the chat's bank question). Ranked; empty when it names none."""
    t = " ".join(tokens(text))
    if not t:
        return []
    banks = list(banks)
    exact = [b for b in banks if " ".join(tokens(b.name)) == t]
    if exact:
        return _dedupe(exact)
    index = BankIndex(banks)
    mentions = find_mentions(text, index, whole_answer=True)
    strength, named, _phrase = strongest(mentions)
    if named:
        return _ranked(named.values())
    # Half a word: "zen", "moni", "provid". Prefix of a word in the name, and the
    # old whole-substring rule, so a fragment still finds every bank it could be.
    if len(t) >= 3:
        hits = [b for b in banks
                if t in (b.name or "").lower()
                or any(w.startswith(t) for w in tokens(b.name))]
        if hits:
            return _ranked(hits)
    return []


def resolve(text: str, banks, *, account: str = "", nuban=None,
            whole_answer: bool = False) -> Resolution:
    """Which bank a whole message means for `account`.

    `nuban` is the list of banks the account number's check digit allows, or None
    when the check does not apply (an 11-digit wallet number). `whole_answer` says
    `text` is only a bank's name (the model's bank_name, a saved recipient's bank),
    not a sentence.
    """
    banks = list(banks)
    index = BankIndex(banks)
    mentions = find_mentions(text, index, account=account, whole_answer=whole_answer)
    strength, named, phrase = strongest(mentions)
    nuban_list = _ranked(nuban or [])
    nuban_pks = {b.pk for b in nuban_list}
    scores = {}
    for m in mentions:
        for pk, (_bank, s) in m.banks.items():
            scores[pk] = max(scores.get(pk, 0), s)

    if strength == STRONG:
        candidates = _ranked(named.values(), scores=scores, nuban=nuban_list)
        if not nuban_list:
            if len(candidates) == 1:
                return Resolution(bank=candidates[0], phrase=phrase, named=True)
            return Resolution(choices=candidates, phrase=phrase, named=True)
        fitting = [b for b in candidates if b.pk in nuban_pks]
        if len(fitting) == 1:
            return Resolution(bank=fitting[0], phrase=phrase, named=True)
        if fitting:
            return Resolution(choices=fitting, phrase=phrase, named=True)
        # The bank they named does not fit the check digit. The check digit is a
        # hint (a code kept in another code space breaks it); a bank the customer
        # named is an instruction, so it stands. The one exception is a word with
        # a second reading that DOES fit: "first" with an FCMB account number.
        if len(candidates) == 1:
            alternates = [b for word in phrase.split() for slug in _ALTERNATES.get(word, ())
                          for b in index.rows_for_slug(slug) if b.pk in nuban_pks]
            if alternates:
                return Resolution(choices=_dedupe(candidates + alternates),
                                  phrase=phrase, named=True)
            return Resolution(bank=candidates[0], phrase=phrase, named=True)
        return Resolution(choices=candidates, phrase=phrase, named=True)

    if strength == WEAK and nuban_list:
        fitting = [b for b in _ranked(named.values(), scores=scores) if b.pk in nuban_pks]
        if fitting:
            # Asked, never chosen: the word may be the recipient's name.
            return Resolution(choices=_dedupe(fitting + nuban_list), phrase=phrase)

    if len(nuban_list) == 1:
        return Resolution(bank=nuban_list[0])
    return Resolution(choices=nuban_list)
