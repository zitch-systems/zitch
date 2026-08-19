"""Electricity and betting on the Wema rail.

These two have no plan catalogue, so their Wema packageId lives in WemaBiller. The
property under test is that an UNMAPPED service keeps working on VTU.ng rather than
failing: the catalogue is synced from a live endpoint, and a partial sync must
degrade one service at a time.
"""
from unittest import mock

from django.test import TestCase, override_settings

from utility.models import WemaBiller
from utility.providers import _wema_vas_route, vtu_purchase, vtu_verify_customer

WEMA_ON = {"KEYS": {"wallet": "k", "airtime": "k", "bills": "k"}, "CHANNEL_ID": "c",
           "SIMULATION": False, "SOURCE_ACCOUNT": "0123456789"}


@override_settings(VAS_PROVIDER="wema", WEMA=WEMA_ON)
class BillerRoutingTests(TestCase):
    def test_an_unmapped_disco_stays_on_vtung(self):
        self.assertIsNone(_wema_vas_route("ikeja-electric", {"amount": "1000"}))

    def test_a_mapped_disco_routes_to_wema_bills(self):
        WemaBiller.objects.create(service_id="ikeja-electric", package_id="77")
        route = _wema_vas_route("ikeja-electric", {"amount": "1000"})
        self.assertEqual(route, {"type": "bill", "code": "77", "amount": "1000"})

    def test_a_mapped_bookmaker_routes_to_wema_bills(self):
        WemaBiller.objects.create(service_id="bet9ja-betting", package_id="91")
        route = _wema_vas_route("bet9ja-betting", {"amount": "500"})
        self.assertEqual(route["code"], "91")

    def test_an_inactive_mapping_is_ignored(self):
        # The off switch for one biller, without deleting the code we synced.
        WemaBiller.objects.create(service_id="eko-electric", package_id="12", active=False)
        self.assertIsNone(_wema_vas_route("eko-electric", {"amount": "1000"}))

    def test_a_variable_amount_bill_with_no_amount_falls_through(self):
        WemaBiller.objects.create(service_id="jos-electric", package_id="5")
        self.assertIsNone(_wema_vas_route("jos-electric", {}))

    def test_the_purchase_actually_goes_to_wema_pay_bill(self):
        WemaBiller.objects.create(service_id="abuja-electric", package_id="31")
        with mock.patch("utility.wema.pay_bill",
                        return_value={"success": True, "status": "SUCCESS"}) as pay:
            res = vtu_purchase("abuja-electric",
                               {"amount": "2500", "billersCode": "1234567890"}, "REF9")
        pay.assert_called_once()
        self.assertEqual(pay.call_args.kwargs["package_id"], "31")
        self.assertEqual(pay.call_args.kwargs["identifier"], "1234567890")
        self.assertEqual(res["vas_rail"], "wema")


@override_settings(VAS_PROVIDER="wema", WEMA=WEMA_ON)
class VerifyCustomerRailTests(TestCase):
    def test_validation_follows_the_purchase_rail(self):
        # Confirming a meter against one biller and paying another is how a customer
        # ends up seeing the right name on the wrong account.
        WemaBiller.objects.create(service_id="kano-electric", package_id="44")
        with mock.patch("utility.wema.validate_bill_customer",
                        return_value={"success": True, "name": "AMINA BELLO"}) as v:
            res = vtu_verify_customer("kano-electric", "555000111")
        v.assert_called_once()
        # The VTU.ng contract, which is what every caller actually reads.
        self.assertEqual(res["customer_name"], "AMINA BELLO")

    def test_an_unmapped_service_still_validates_on_vtung(self):
        with mock.patch("utility.vtung.vt_verify_customer",
                        return_value={"success": True, "name": "VTU NAME"}) as v:
            res = vtu_verify_customer("enugu-electric", "555000111")
        v.assert_called_once()
        self.assertEqual(res["name"], "VTU NAME")

    def test_a_wema_validation_failure_falls_back_rather_than_blaming_the_customer(self):
        # An unmapped package or a gateway hiccup looks exactly like a bad meter
        # number. Telling the customer their own meter is wrong on that evidence is
        # the one outcome we can be sure is unhelpful.
        WemaBiller.objects.create(service_id="ibadan-electric", package_id="60")
        with mock.patch("utility.wema.validate_bill_customer",
                        return_value={"success": False, "message": "no"}), \
             mock.patch("utility.vtung.vt_verify_customer",
                        return_value={"success": True, "name": "FALLBACK"}) as v:
            res = vtu_verify_customer("ibadan-electric", "555000111")
        v.assert_called_once()
        self.assertEqual(res["name"], "FALLBACK")


@override_settings(VAS_PROVIDER="vtung", WEMA=WEMA_ON)
class VtungRailUnaffectedTests(TestCase):
    def test_a_mapped_biller_is_not_used_when_the_rail_is_vtung(self):
        WemaBiller.objects.create(service_id="ikeja-electric", package_id="77")
        with mock.patch("utility.vtung.vt_purchase",
                        return_value={"success": True}) as p:
            vtu_purchase("ikeja-electric", {"amount": "1000"}, "REF1")
        p.assert_called_once()


@override_settings(VAS_PROVIDER="wema", WEMA=WEMA_ON)
class ValidationContractTests(TestCase):
    """Both rails must answer in the SAME shape.

    The meter-owner name is the only control that catches a mistyped meter number
    before the money leaves, and it is read as `customer_name` everywhere. A rail
    that answers `name` disables that control without failing anything.
    """

    def setUp(self):
        WemaBiller.objects.create(service_id="ikeja-electric", package_id="70")

    def test_the_wema_rail_answers_in_the_vtung_shape(self):
        with mock.patch("utility.wema.validate_bill_customer",
                        return_value={"success": True, "name": "AMINA BELLO"}):
            res = vtu_verify_customer("ikeja-electric", "555000111")
        self.assertEqual(res["customer_name"], "AMINA BELLO")

    def test_a_clean_envelope_with_no_name_is_not_a_confirmed_owner(self):
        # ALAT can answer hasError:false with no customerName. Treating that as a
        # verified meter shows the customer a blank owner and lets them confirm.
        with mock.patch("utility.wema.validate_bill_customer",
                        return_value={"success": True, "name": ""}), \
             mock.patch("utility.vtung.vt_verify_customer",
                        return_value={"success": True, "customer_name": "FALLBACK"}) as vt:
            res = vtu_verify_customer("ikeja-electric", "555000111")
        vt.assert_called_once()
        self.assertEqual(res["customer_name"], "FALLBACK")
