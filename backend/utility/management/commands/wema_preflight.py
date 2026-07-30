"""Go-live preflight — the scriptable "are we go?" readiness check.

Runs the checks a human would otherwise click through before flipping Zitch to
live money, but as one command with a machine-readable exit code — so going live
is a mechanical, repeatable step instead of a checklist someone might skip. It is
read-only and moves no money: the same live self-tests as /wema-diagnose and
/vtu-diagnose (no purchases, no transfers).

HARD gates block real money and cause a nonzero exit:
  * Wema live keys present (channel id + wallet + per-product keys)
  * pointed at the LIVE host, not apiplayground (the sandbox)
  * simulation off, test-OTP bypass off, simulated-deposit token unset

SOFT checks are features that degrade without putting money at risk (securityInfo,
VTU wallet balance, email, SMS, card issuer). They print WARN and only fail the run
under --strict.

securityInfo used to be a hard gate on the belief that it was a signing scheme the
bank had yet to issue. Wema corrected that on 2026-07-27 — it is a value WE choose
that the bank echoes back to our authentication callback — so it no longer blocks a
launch. See utility.wema._security_info.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from utility import wema
from utility.providers import kyc_provider, payment_provider, payout_provider, vas_provider
from utility.vtung import vtu_probe

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


class Command(BaseCommand):
    help = ("Go-live readiness preflight for the Wema money rails (+ VAS/email/SMS). "
            "Exits 1 if any hard gate fails.")

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true",
                            help="Treat soft-check WARNs as failures too (exit 1).")

    def handle(self, *args, **options):
        checks = []  # (is_hard_gate, name, status, detail)

        d = wema.wema_diagnostics()
        checks.append((
            True, "Wema live keys",
            PASS if d["wema_live"] else FAIL,
            "channel + wallet + product keys present" if d["wema_live"]
            else f"status={d['status']} — {d['hint']}"))
        # SOFT, not a gate. Wema confirmed (2026-07-27) that securityInfo is "a private
        # key best known to you" which the bank merely echoes back to our authentication
        # callback — it is not issued by the bank and nothing indicates the bank
        # validates it. Blocking go-live on it was based on the earlier, wrong reading
        # that it was a signing scheme the bank had yet to supply. Still recommended:
        # it costs nothing and enables WEMA_AUTH_REQUIRE_SECURITY_INFO.
        checks.append((
            False, "securityInfo",
            PASS if d["security_info_set"] else WARN,
            "set (echoed back to the authentication callback)" if d["security_info_set"]
            else "WEMA_SECURITY_INFO unset — payouts still work (the callback authorises "
                 "from our ledger), but set any strong random value to enable "
                 "WEMA_AUTH_REQUIRE_SECURITY_INFO"))
        on_sandbox = "apiplayground" in (d["base_url"] or "").lower()
        checks.append((
            True, "Live host",
            FAIL if on_sandbox else PASS,
            f"sandbox: {d['base_url']}" if on_sandbox else f"live: {d['base_url']}"))
        # Hard gate: simulation mode serves MOCK responses across the ENTIRE stack
        # (Wema + VTU + cards + FX + Mono + KYC) — a customer would be told a purchase
        # succeeded while nothing was delivered. It must be off for real money to move.
        sim_flags = [name for name, cfg in (("WEMA_SIMULATION", settings.WEMA),
                                            ("MONO_SIMULATION", getattr(settings, "MONO", {})))
                     if (cfg or {}).get("SIMULATION")]
        checks.append((
            True, "Simulation mode",
            FAIL if sim_flags else PASS,
            f"ON via {', '.join(sim_flags)} — the whole stack is serving mocks; unset "
            f"before go-live" if sim_flags else "off (live rails)"))
        # Hard gate: the test-OTP bypass must NEVER be live at go-live — it lets one
        # number sign in with a fixed code. Fail readiness while it is configured.
        test_otp_on = bool(settings.TEST_OTP["PHONE"] and settings.TEST_OTP["CODE"])
        checks.append((
            True, "Test-OTP bypass",
            FAIL if test_otp_on else PASS,
            "TEST_OTP is SET — a fixed OTP is accepted for one number; unset "
            "TEST_OTP_PHONE + TEST_OTP_CODE before go-live" if test_otp_on else "off"))
        # Hard gate: the simulated-deposit endpoint must never be reachable at
        # go-live. It is already inert once WEMA_SIMULATION is off, but fail while
        # the token lingers so it gets cleaned up too.
        sim_deposit_on = bool(settings.SIMULATE_DEPOSIT_TOKEN)
        checks.append((
            True, "Simulated-deposit token",
            FAIL if sim_deposit_on else PASS,
            "SIMULATE_DEPOSIT_TOKEN is SET — unset it before go-live"
            if sim_deposit_on else "off"))

        # SOFT — VTU.ng (airtime/data/bills). Lean on vtu_probe's own empty-wallet
        # detection (it sets a balance hint) rather than re-parsing the amount.
        v = vtu_probe()
        if not v.get("config", {}).get("live"):
            checks.append((False, "VTU.ng rail", WARN, "no VTU credentials — airtime/data/bills disabled"))
        elif not v.get("auth", {}).get("ok"):
            checks.append((False, "VTU.ng rail", WARN, "auth failed — check VTUNG_* credentials"))
        else:
            bal = v.get("balance", {})
            if not bal.get("ok"):
                checks.append((False, "VTU.ng rail", WARN, "balance unreadable"))
            elif bal.get("hint"):  # vtu_probe sets a hint only when the wallet is empty
                checks.append((False, "VTU.ng rail", WARN,
                               "auth ok but VTU wallet empty — VAS buys fail until topped up"))
            else:
                checks.append((False, "VTU.ng rail", PASS, f"auth ok, balance {bal.get('balance')}"))

        checks.append((False, "Email (Resend)",
                       PASS if settings.RESEND["API_KEY"] else WARN,
                       "keyed" if settings.RESEND["API_KEY"]
                       else "RESEND_API_KEY unset — no transactional email"))
        from utility.providers import sms_live, sms_provider
        checks.append((False, f"SMS ({sms_provider()})",
                       PASS if sms_live() else WARN,
                       "keyed" if sms_live()
                       else f"no API key for the {sms_provider()} rail — no SMS/OTP-by-SMS"))
        checks.append((False, "Card issuer",
                       PASS if settings.CARD_ISSUER["API_KEY"] else WARN,
                       "keyed" if settings.CARD_ISSUER["API_KEY"]
                       else "no issuer key — virtual cards disabled"))

        # SOFT — the VAS status legends. Money-safe either way (an unknown code leaves
        # the purchase PENDING), so this can never be a gate; but an unset legend means
        # timed-out airtime/data/bill buys accumulate as PENDING rows that only a human
        # can clear, which ops should know before launch rather than discover from a
        # queue. Wema owes us these two maps — see docs/wema-migration.md.
        from utility.wema import _vas_legend
        for product, env_var in (("airtime", "WEMA_VAS_STATUS_LEGEND"),
                                 ("bills", "WEMA_BILLS_STATUS_LEGEND")):
            legend = _vas_legend(product)
            checks.append((
                False, f"VAS status legend ({product})",
                PASS if legend else WARN,
                f"{len(legend)} code(s) mapped" if legend
                else f"{env_var} unset — a timed-out {product} purchase stays PENDING "
                     "until an operator resolves it (no auto settle/refund)"))

        self.stdout.write("")
        self.stdout.write("Zitch go-live preflight")
        self.stdout.write("=======================")
        self.stdout.write(f"rails: funding={payment_provider()} payout={payout_provider()} "
                          f"vas={vas_provider()} kyc={kyc_provider()}")
        self.stdout.write("")
        for is_hard, name, status, detail in checks:
            tag = "GATE" if is_hard else "    "
            self.stdout.write(f"  [{status}] {tag} {name}: {detail}")

        hard_fail = [c for c in checks if c[0] and c[2] == FAIL]
        soft_warn = [c for c in checks if not c[0] and c[2] in (WARN, FAIL)]
        self.stdout.write("")
        if hard_fail:
            self.stdout.write(f"RESULT: NOT READY — {len(hard_fail)} hard gate(s) failing. "
                              f"Real money is blocked.")
        elif soft_warn and options["strict"]:
            self.stdout.write(f"RESULT: NOT READY (strict) — {len(soft_warn)} soft check(s) warning.")
        elif soft_warn:
            self.stdout.write(f"RESULT: GO for money rails — {len(soft_warn)} soft warning(s) "
                              f"(non-money features degraded).")
        else:
            self.stdout.write("RESULT: GO — all checks pass.")

        if hard_fail or (soft_warn and options["strict"]):
            raise SystemExit(1)
