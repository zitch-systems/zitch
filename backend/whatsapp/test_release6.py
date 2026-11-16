"""The remaining designed pieces: the short approve link, the sign-in code by SMS,
the verification card, the menu's second lines and in-form answers, and the
airtime quick-amount chips."""

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone

import whatsapp

from . import release2, release3, router
from . import test_login_flow as login_tests, test_release2 as r2, test_release3 as r3
from .flows import sign_approve_token
from .models import ConversationState, PendingAction, WaOnboarding
from .test_release2 import MSISDN, _exchange
from .tests import make_user

_DOC = json.loads((Path(whatsapp.__file__).parent / "flow_assets" / "pin_flow.json").read_text())
_BY_ID = {s["id"]: s for s in _DOC["screens"]}
LINKS = {"API_BASE": "https://api.zitch.test", "SHORT_BASE": "https://zitch.test",
         "WEBSITE": "", "APP": "", "SUPPORT_WA": "", "SUPPORT_EMAIL": ""}


def _pending(user, state="pin"):
    return PendingAction.objects.create(
        user=user, msisdn=MSISDN, action_type="transfer", state=state,
        payload={"amount": "1000"}, expires_at=timezone.now() + timedelta(minutes=5))


@override_settings(ZITCH_LINKS=LINKS)
class ShortApproveLinkTests(TestCase):

    def setUp(self):
        self.user, _ = make_user()

    def test_the_confirm_card_carries_the_short_link(self):
        pa = _pending(self.user)
        url = router._approve_url(pa)  # noqa: SLF001
        self.assertRegex(url, r"^https://zitch\.test/a/[2-9a-km-zA-HJ-NP-Z]{6}$")
        self.assertEqual(router._approve_url(pa), url)  # noqa: SLF001 - minted once
        line = router._approve_link_line(pa, primary=True)  # noqa: SLF001
        self.assertEqual(line, f"📲 *Approve with your fingerprint or Face ID*: {url}")

    def test_the_short_link_serves_the_same_bounce_page(self):
        pa = _pending(self.user)
        code = router._approve_url(pa).rsplit("/", 1)[1]  # noqa: SLF001
        page = Client().get(f"/a/{code}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(f"zitch://waapprove?token={sign_approve_token(pa)}", page.content.decode())
        self.assertEqual(page["Cache-Control"], "no-store")

    def test_unknown_used_or_expired_codes_show_nothing(self):
        pa = _pending(self.user)
        code = router._approve_url(pa).rsplit("/", 1)[1]  # noqa: SLF001
        self.assertEqual(Client().get("/a/zzzzzz").status_code, 404)
        PendingAction.objects.filter(pk=pa.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        page = Client().get(f"/a/{code}")
        self.assertEqual(page.status_code, 404)
        self.assertNotIn("waapprove", page.content.decode())

    @override_settings(ZITCH_LINKS={**LINKS, "SHORT_BASE": ""})
    def test_without_a_short_origin_the_long_link_stays(self):
        pa = _pending(self.user)
        self.assertEqual(router._approve_url(pa),  # noqa: SLF001
                         f"https://api.zitch.test/wa/approve/{sign_approve_token(pa)}")


class LoginSmsTests(login_tests.WhatsAppLoginTests):
    """Every sign-in test again, plus the code by SMS."""

    def test_the_code_also_goes_by_sms_to_the_account_phone(self):
        with patch("utility.providers.send_sms", return_value={"success": True}) as sms:
            self.start()
        self.assertEqual(sms.call_args.args[0], self.user.phone)
        self.assertIn("123456", sms.call_args.args[1])
        ob = WaOnboarding.objects.get(msisdn=self.msisdn)
        self.assertTrue(ob.payload["sms_sent"])

    @override_settings(WHATSAPP_LOGIN_SMS_CODE=False)
    def test_the_sms_copy_can_be_switched_off(self):
        with patch("utility.providers.send_sms") as sms:
            self.start()
        sms.assert_not_called()
        self.assertNotIn("sms_sent", WaOnboarding.objects.get(msisdn=self.msisdn).payload)

    def test_a_failed_sms_still_signs_in_by_email(self):
        with patch("utility.providers.send_sms", side_effect=RuntimeError("down")):
            token = self.start()
        self.assertTrue(token)
        self.assertNotIn("sms_sent", WaOnboarding.objects.get(msisdn=self.msisdn).payload)


class TierCardTests(TestCase):

    def test_the_card_names_the_saved_address_for_tier_3(self):
        user, _ = make_user()
        user.address = "12 Palm Street, Ikeja, Lagos"
        user.tier = 1
        lines = router._tier_ladder_lines(user)  # noqa: SLF001
        self.assertIn("⬜ Tier 3 · Address (saved: 12 Palm Street, Ikeja)", lines)
        user.address = ""
        self.assertIn("⬜ Tier 3 · Address\n", router._tier_ladder_lines(user) + "\n")  # noqa: SLF001


@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "MENU_FORM": True, "BILL_FORM": True,
                                  "DESIGN_V5": True})
class MenuV5Tests(r2.MenuFormTests):
    """Every menu-form test again with Release 5 on, plus its second lines and
    in-form answers."""

    def test_every_row_has_its_second_line(self):
        items = release2.menu_items()
        self.assertTrue(all(item.get("description") for item in items))
        self.assertEqual(items[1]["description"], "Opens the send form")
        props = _BY_ID["MENU"]["data"]["items"]["items"]["properties"]
        self.assertIn("description", props)

    def test_the_balance_is_answered_in_the_form_when_unlocked(self):
        token = self.menu_token()
        router._mark_verified(MSISDN)  # noqa: SLF001
        with patch("whatsapp.router.send_text") as chat:
            resp = self.assertScreen(_exchange(token, {"item": "1"}), standing_on="MENU")
        chat.assert_not_called()
        self.assertEqual(resp["data"]["status"], "💰 Your balance")
        self.assertIn("₦20,000.00", resp["data"]["message"])
        self.assertNotIn("*", resp["data"]["message"])

    def test_account_details_are_answered_in_the_form(self):
        token = self.menu_token()
        router._mark_verified(MSISDN)  # noqa: SLF001
        resp = _exchange(token, {"item": "7"})
        self.assertEqual(resp["data"]["status"], "🧾 My account details")
        self.assertIn(self.user.phone, resp["data"]["message"])

    def test_after_the_idle_window_the_chat_asks_for_the_pin_first(self):
        token = self.menu_token()
        ConversationState.objects.filter(msisdn=MSISDN).update(
            last_verified=timezone.now() - timedelta(hours=2))
        with patch("whatsapp.router.reply"):
            resp = _exchange(token, {"item": "1"})
        self.assertIn("extension_message_response", resp["data"])


class DesignReviewTests(TestCase):
    """What the page-by-page review against the approved design changed."""

    def test_sign_in_pages_read_as_designed(self):
        for sid in ("LOGIN_SCREEN", "LOGIN_RETRY"):
            kids = _BY_ID[sid]["layout"]["children"]
            self.assertIn("Confirm it's you", [c.get("text") for c in kids])
            form = next(c for c in kids if c.get("type") == "Form")["children"]
            code = next(c for c in form if c.get("name") == "number")
            self.assertEqual((code["label"], code["helper-text"]), ("Code", "6 digits, by SMS or email"))

    def test_the_privacy_page_names_the_partnership_and_keeps_the_licence_notice(self):
        texts = [c.get("text") for c in _BY_ID["PRIVACY_NOTICE"]["layout"]["children"]]
        self.assertIn("Zitch is built in partnership with Wema Bank Plc.", texts)
        self.assertIn("Banking services by Wema Bank Plc, licensed by the CBN. Deposits insured by NDIC.",
                      texts)

    def test_discos_carry_their_short_names(self):
        first = release2._disco_options()[0]
        self.assertEqual({k: first[k] for k in ("id", "title")}, {"id": "1", "title": "Ikeja Electric (IKEDC)"})
        # The shipped logo rides beside it (whatsapp/assets/discos/IKEDC.png).
        self.assertTrue(first["image"].startswith("iVBORw0KGgo"))

    def test_switched_off_products_say_coming_soon_to_a_customer_with_nothing_open(self):
        from .savings_loans import show_loan, show_savings

        from .models import WhatsAppLink

        user, _ = make_user()
        WhatsAppLink.objects.create(user=user, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)
        router._mark_verified(MSISDN)  # noqa: SLF001
        with patch("whatsapp.savings_loans.product_available", return_value=False), \
             patch("whatsapp.router.reply") as reply:
            show_savings(user, MSISDN)
            show_loan(user, MSISDN)
        self.assertEqual([c.args[1] for c in reply.call_args_list],
                         ["🏦 *Savings*\n\nComing soon.", "💳 *Loans*\n\nComing soon."])
        with patch("whatsapp.router.product_available", return_value=False), \
             patch("whatsapp.router.reply") as reply:
            router._start_convert(user, MSISDN)  # noqa: SLF001
        self.assertEqual(reply.call_args.args[1], "💱 *Convert currency*\n\nComing soon.")


@override_settings(WHATSAPP_FLOW=r3.VTU_ON)
class QuickAmountTests(r3._VtuBase):

    def test_both_airtime_pages_carry_the_chips(self):
        self.assertEqual(_DOC["version"], "6.3")
        for sid in ("VTU_FORM", "VTU_FORM_CHAIN"):
            form = _BY_ID[sid]["layout"]["children"][-1]
            chips = next(c for c in form["children"] if c.get("type") == "ChipsSelector")
            self.assertEqual([o["id"] for o in chips["data-source"]], list(release3.QUICK_AMOUNTS))
            self.assertEqual(chips["max-selected-items"], 1)
            self.assertEqual(form["children"][-1]["on-click-action"]["payload"]["quick_amount"],
                             "${form.quick_amount}")

    def test_a_tapped_chip_is_the_amount_when_the_box_is_empty(self):
        pa, _ = self.open()
        resp = self.submit(pa, kind="airtime", phone=r3.OWN, network="1", amount="",
                           quick_amount=["2000"])
        self.assertEqual(resp["data"]["amount"], "₦2,000.00")

    def test_a_typed_amount_wins_and_unknown_chips_are_ignored(self):
        self.assertEqual(release3._quick_amount(["7777"]), "")  # noqa: SLF001
        self.assertEqual(release3._quick_amount("500"), "500")  # noqa: SLF001
        pa, _ = self.open()
        resp = self.submit(pa, kind="airtime", phone=r3.OWN, network="1", amount="300",
                           quick_amount=["5000"])
        self.assertEqual(resp["data"]["amount"], "₦300.00")
