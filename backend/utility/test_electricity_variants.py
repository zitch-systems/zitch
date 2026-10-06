"""Electricity must validate and pay the package for the selected meter type."""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from utility.models import WemaBiller
from utility.providers import _wema_vas_route, vtu_purchase, vtu_verify_customer
from utility.test_wema_billers import WEMA_ON


def catalogue(*, category="Electricity", biller="Ikeja Electricity", packages=None):
    """Use actual nested packages, retaining biller ID as a separate identity."""
    if packages is None:
        packages = [
            {"id": 1043, "name": "Ikeja Electric Prepaid"},
            {"id": 1411, "name": "Ikeja Electric Postpaid"},
        ]
    return {
        "success": True,
        "raw": {"result": [{"name": category, "billers": [{
            "id": 49, "name": biller, "packages": packages,
        }]}]},
        # These flattened rows intentionally disagree. Their `code` could be a
        # biller ID, so a catalogue sync must obtain IDs from nested packages.
        "bills": [{"code": "49", "biller": biller,
                   "category": category, "name": "Ikeja Electric Prepaid"}],
    }


@override_settings(VAS_PROVIDER="wema", WEMA=WEMA_ON)
class ElectricityVariantRoutingTests(TestCase):
    def setUp(self):
        self.prepaid = WemaBiller.objects.create(
            service_id="ikeja-electric", meter_type="prepaid", package_id="1043")
        self.postpaid = WemaBiller.objects.create(
            service_id="ikeja-electric", meter_type="postpaid", package_id="1411")

    def route(self, meter_type):
        return _wema_vas_route("ikeja-electric", {
            "amount": "1500", "variation_code": meter_type})

    def test_both_meter_types_coexist_and_route_independently(self):
        for meter_type, package_id in (("prepaid", "1043"), ("postpaid", "1411")):
            with self.subTest(meter_type=meter_type):
                self.assertEqual(self.route(meter_type), {
                    "type": "bill", "code": package_id, "amount": "1500"})

    def test_duplicate_service_and_meter_type_is_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            WemaBiller.objects.create(
                service_id="ikeja-electric", meter_type="prepaid", package_id="999")

    def test_legacy_blank_mapping_is_preserved_but_never_used_for_electricity(self):
        legacy = WemaBiller.objects.create(service_id="ikeja-electric", package_id="49")
        self.prepaid.delete()
        for meter_type in ("prepaid", "", None, "unknown"):
            with self.subTest(meter_type=meter_type):
                self.assertIsNone(self.route(meter_type))
        self.assertEqual(self.route("postpaid")["code"], "1411")
        legacy.refresh_from_db()
        self.assertEqual(legacy.package_id, "49")
        self.assertEqual(legacy.meter_type, "")

    def test_disabled_variant_does_not_fall_back_to_other_meter_type(self):
        self.prepaid.active = False
        self.prepaid.save(update_fields=["active"])
        self.assertIsNone(self.route("prepaid"))
        self.assertEqual(self.route("postpaid")["code"], "1411")

    def test_empty_package_does_not_fall_back_to_other_meter_type(self):
        self.prepaid.package_id = ""
        self.prepaid.save(update_fields=["package_id"])
        self.assertIsNone(self.route("prepaid"))

    def test_meter_validation_and_payment_use_the_same_selected_package(self):
        with mock.patch("utility.wema.validate_bill_customer", return_value={
                "success": True, "name": "AMINA BELLO"}) as validate, \
                mock.patch("utility.wema.pay_bill", return_value={
                    "success": True, "status": "SUCCESS"}) as pay, \
                mock.patch("utility.wema.vas_status_entitlement", return_value=(True, "")), \
                mock.patch("wallet.services.biller_source_for_transaction",
                           return_value="0155500011"):
            for meter_type, package_id in (("prepaid", "1043"), ("postpaid", "1411")):
                with self.subTest(meter_type=meter_type):
                    verified = vtu_verify_customer("ikeja-electric", "555000111", meter_type)
                    self.assertEqual(verified["customer_name"], "AMINA BELLO")
                    validate.assert_called_with(package_id=package_id, identifier="555000111")
                    result = vtu_purchase("ikeja-electric", {
                        "variation_code": meter_type, "billersCode": "555000111",
                        "amount": "1500",
                    }, "REF-" + meter_type)
                    self.assertTrue(result["success"])
                    self.assertEqual(pay.call_args.kwargs["package_id"], package_id)
                    self.assertEqual(pay.call_args.kwargs["identifier"], "555000111")
                    self.assertEqual(pay.call_args.kwargs["source_account"], "0155500011")

    def test_missing_variant_refuses_validation_and_payment_before_provider_calls(self):
        self.postpaid.delete()
        with mock.patch("utility.wema.validate_bill_customer") as validate, \
                mock.patch("utility.wema.pay_bill") as pay, \
                mock.patch("wallet.services.biller_source_for_transaction") as source:
            self.assertFalse(vtu_verify_customer(
                "ikeja-electric", "555000111", "postpaid")["success"])
            self.assertFalse(vtu_purchase("ikeja-electric", {
                "variation_code": "postpaid", "billersCode": "555000111", "amount": "1500",
            }, "REF-MISSING")["success"])
        validate.assert_not_called()
        pay.assert_not_called()
        source.assert_not_called()

    def test_betting_keeps_its_existing_blank_variant_mapping(self):
        WemaBiller.objects.create(service_id="bet9ja-betting", package_id="91")
        self.assertEqual(_wema_vas_route("bet9ja-betting", {"amount": "500"}), {
            "type": "bill", "code": "91", "amount": "500"})

    def test_betting_cannot_accidentally_use_an_electricity_variant_row(self):
        WemaBiller.objects.create(
            service_id="bet9ja-betting", meter_type="prepaid", package_id="1043")
        self.assertIsNone(_wema_vas_route("bet9ja-betting", {
            "amount": "500", "variation_code": "prepaid"}))


class ElectricityVariantCatalogueTests(TestCase):
    def sync(self, response, *, dry_run=False):
        output = StringIO()
        with mock.patch("utility.wema.wema_live", return_value=True), \
                mock.patch("utility.wema.get_bills", return_value=response) as get_bills:
            call_command("seed_wema_plans", only="billers", dry_run=dry_run, stdout=output)
        get_bills.assert_called_once_with()
        return output.getvalue()

    def mappings(self):
        return set(WemaBiller.objects.values_list("service_id", "meter_type", "package_id"))

    def test_real_package_ids_are_stored_for_each_explicit_meter_type(self):
        self.sync(catalogue())
        self.assertEqual(self.mappings(), {
            ("ikeja-electric", "prepaid", "1043"),
            ("ikeja-electric", "postpaid", "1411"),
        })
        self.assertEqual(set(WemaBiller.objects.values_list("biller_id", flat=True)), {"49"})

    def test_all_observed_disco_identities_map_their_own_package_variants(self):
        identities = (
            ("abuja-electric", "AEDC"),
            ("eko-electric", "EKEDC"),
            ("enugu-electric", "Enugu Electricity Distribution Company"),
            ("ibadan-electric", "Ibadan Electricity Distribution Company"),
            ("ikeja-electric", "Ikeja Electricity"),
            ("jos-electric", "Jos Electricity Distribution Company"),
            ("kaduna-electric", "Kaduna Electric Distribution Company"),
            ("kano-electric", "Kano Electricity Distribution Company"),
            ("port harcourt-electric", "Porthacourt Electricity Distribution Company"),
        )
        billers = []
        expected = set()
        for index, (service_id, biller_name) in enumerate(identities, start=1):
            packages = []
            for offset, meter_type in enumerate(("prepaid", "postpaid")):
                package_id = index * 100 + offset
                packages.append({"id": package_id, "name": f"{biller_name} {meter_type}"})
                expected.add((service_id, meter_type, str(package_id)))
            billers.append({"id": index, "name": biller_name, "packages": packages})
        self.sync({"success": True, "raw": {"result": [
            {"name": "Electricity", "billers": billers},
        ]}})
        self.assertEqual(self.mappings(), expected)

    def test_flattened_biller_fallback_never_becomes_a_package_mapping(self):
        for response in (
            catalogue(packages=[]),
            {"success": True, "bills": catalogue()["bills"]},
        ):
            with self.subTest(response=response):
                self.sync(response)
                self.assertFalse(WemaBiller.objects.exists())

    def test_failed_catalogue_response_does_not_create_mappings(self):
        response = catalogue()
        response["success"] = False
        self.sync(response)
        self.assertFalse(WemaBiller.objects.exists())

    def test_electricity_names_in_water_or_unknown_category_are_not_mapped(self):
        for category in ("Water", "Other", ""):
            with self.subTest(category=category):
                self.sync(catalogue(category=category))
                self.assertFalse(WemaBiller.objects.exists())

    def test_near_match_biller_names_do_not_match_an_allowlisted_disco(self):
        for biller in ("Ikeja State Water Board", "Not Ikeja Electricity",
                       "Ikeja Electricity Tax", "Ikeja Electric"):
            with self.subTest(biller=biller):
                self.sync(catalogue(biller=biller))
                self.assertFalse(WemaBiller.objects.exists())

    def test_package_without_explicit_meter_type_is_not_assigned_to_both(self):
        self.sync(catalogue(packages=[{"id": 1043, "name": "Ikeja Electric"}]))
        self.assertFalse(WemaBiller.objects.exists())

    def test_package_naming_both_meter_types_is_ambiguous(self):
        self.sync(catalogue(packages=[{
            "id": 1043, "name": "Ikeja Electric Prepaid / Postpaid",
        }]))
        self.assertFalse(WemaBiller.objects.exists())

    def test_missing_package_id_cannot_use_the_biller_id(self):
        self.sync(catalogue(packages=[{"name": "Ikeja Electric Prepaid"}]))
        self.assertFalse(WemaBiller.objects.exists())

    def test_distinct_packages_for_same_variant_are_not_arbitrarily_selected(self):
        self.sync(catalogue(packages=[
            {"id": 1043, "name": "Ikeja Electric Prepaid"},
            {"id": 9999, "name": "Ikeja Electric Prepaid"},
            {"id": 1411, "name": "Ikeja Electric Postpaid"},
        ]))
        self.assertEqual(self.mappings(), {("ikeja-electric", "postpaid", "1411")})

    def test_same_disco_under_two_biller_rows_is_refused_entirely(self):
        response = catalogue(packages=[{"id": 1043, "name": "Ikeja Electric Prepaid"}])
        response["raw"]["result"][0]["billers"].append({
            "id": 50, "name": "Ikeja Electricity", "packages": [
                {"id": 1411, "name": "Ikeja Electric Postpaid"},
            ],
        })
        self.sync(response)
        self.assertFalse(WemaBiller.objects.exists())

    def test_dry_run_reports_variant_matches_without_changing_existing_rows(self):
        existing = WemaBiller.objects.create(
            service_id="ikeja-electric", meter_type="prepaid", package_id="88", active=False)
        before = list(WemaBiller.objects.values())
        output = self.sync(catalogue(), dry_run=True)
        self.assertEqual(list(WemaBiller.objects.values()), before)
        self.assertIn("1043", output)
        self.assertIn("1411", output)
        self.assertIn("prepaid", output)
        self.assertIn("postpaid", output)
        existing.refresh_from_db()
        self.assertFalse(existing.active)

    def test_sync_preserves_disabled_variant_and_legacy_row(self):
        disabled = WemaBiller.objects.create(
            service_id="ikeja-electric", meter_type="prepaid", package_id="88", active=False)
        legacy = WemaBiller.objects.create(service_id="ikeja-electric", package_id="49")
        self.sync(catalogue())
        disabled.refresh_from_db()
        legacy.refresh_from_db()
        self.assertEqual(disabled.package_id, "1043")
        self.assertFalse(disabled.active)
        self.assertEqual(legacy.package_id, "49")
        self.assertEqual(legacy.meter_type, "")
        self.assertEqual(self.mappings(), {
            ("ikeja-electric", "", "49"),
            ("ikeja-electric", "prepaid", "1043"),
            ("ikeja-electric", "postpaid", "1411"),
        })

    def test_disabled_legacy_mapping_keeps_new_variants_disabled(self):
        legacy = WemaBiller.objects.create(
            service_id="ikeja-electric", package_id="49", active=False)
        self.sync(catalogue())
        legacy.refresh_from_db()
        self.assertFalse(legacy.active)
        self.assertEqual(legacy.package_id, "49")
        variants = WemaBiller.objects.filter(service_id="ikeja-electric").exclude(meter_type="")
        self.assertEqual(set(variants.values_list("meter_type", "package_id", "active")), {
            ("prepaid", "1043", False), ("postpaid", "1411", False),
        })

    def test_sync_is_idempotent_without_creating_duplicate_variant_rows(self):
        self.sync(catalogue())
        before = set(WemaBiller.objects.values_list("pk", "meter_type", "package_id"))
        self.sync(catalogue())
        self.assertEqual(set(WemaBiller.objects.values_list("pk", "meter_type", "package_id")), before)
