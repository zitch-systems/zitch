"""Meta will only open a Flow on a ROOT of its routing graph.

The rejection is invisible until a real send hits the live API:

    (#131009) Specified screen IDENTITY_SCREEN is not allowed as first screen
    of this flow. Allowed screen name is: EMAIL_SCREEN,SIGNUP_SCREEN,TRANSFER_FORM

It cost two publish cycles and a latent break of every PIN send — adding
SIGNUP_SCREEN -> PIN_SCREEN gave PIN_SCREEN an incoming route, which silently
disqualified it as an opening screen while every unit test still passed. These
assert the graph property directly, against the same file that gets published.
"""
import collections
import json
from pathlib import Path

from django.test import SimpleTestCase

import whatsapp
from whatsapp.flows import (CODE_RETRY, CODE_SCREEN, EMAIL_SCREEN, IDENTITY_CHAIN,
                            IDENTITY_RETRY, IDENTITY_SCREEN,
                            PIN_CHAIN, PIN_CONFIRM, PIN_CONFIRM_RETRY, PIN_RETRY, PIN_SCREEN,
                            SIGNUP_SCREEN, TRANSFER_FORM)

FLOW = json.loads((Path(whatsapp.__file__).parent / "flow_assets" / "pin_flow.json").read_text())

#: Every screen the router opens a Flow message on (send_flow(screen=...)).
OPENING_SCREENS = {PIN_SCREEN, IDENTITY_SCREEN, EMAIL_SCREEN, SIGNUP_SCREEN, TRANSFER_FORM,
                   CODE_SCREEN}


def _incoming():
    edges = collections.defaultdict(set)
    for src, targets in FLOW["routing_model"].items():
        for target in targets:
            edges[target].add(src)
    return edges


class OpeningScreensAreRoutingRootsTests(SimpleTestCase):

    def test_every_screen_we_open_on_has_no_incoming_route(self):
        incoming = _incoming()
        offenders = {s: sorted(incoming[s]) for s in OPENING_SCREENS if incoming[s]}
        self.assertEqual(offenders, {},
                         f"Meta will refuse to open the flow on these: {offenders}")

    def test_the_router_opens_on_nothing_the_flow_does_not_define(self):
        defined = {s["id"] for s in FLOW["screens"]}
        self.assertLessEqual(OPENING_SCREENS, defined)

    def test_the_chained_twins_exist_and_are_reachable(self):
        """PIN_CHAIN/IDENTITY_CHAIN carry the chained arrivals so their roots stay
        roots. A twin with no incoming route would mean the chain is dead."""
        incoming = _incoming()
        self.assertTrue(incoming[PIN_CHAIN], "PIN_CHAIN is unreachable")
        self.assertTrue(incoming[IDENTITY_CHAIN], "IDENTITY_CHAIN is unreachable")
        self.assertTrue(incoming[IDENTITY_RETRY], "IDENTITY_RETRY is unreachable")
        self.assertTrue(incoming[PIN_RETRY], "PIN_RETRY is unreachable")
        self.assertTrue(incoming[PIN_CONFIRM_RETRY], "PIN_CONFIRM_RETRY is unreachable")
        self.assertTrue(incoming[CODE_RETRY], "CODE_RETRY is unreachable")

    def test_a_twin_renders_exactly_what_its_root_renders(self):
        """Same layout, different id — otherwise a customer arriving by chain
        gets a different screen from one arriving directly."""
        by_id = {s["id"]: s for s in FLOW["screens"]}
        # IDENTITY_CHAIN is no longer IDENTITY_SCREEN's twin: identity fields
        # are 11/11 and code fields 6/6, so the chained CODE pages twin with
        # CODE_SCREEN instead.
        for root, twin in ((PIN_SCREEN, PIN_CHAIN), (IDENTITY_SCREEN, IDENTITY_RETRY),
                           (PIN_SCREEN, PIN_RETRY), (PIN_CONFIRM, PIN_CONFIRM_RETRY),
                           (CODE_SCREEN, CODE_RETRY), (CODE_SCREEN, IDENTITY_CHAIN)):
            self.assertEqual(by_id[root]["layout"], by_id[twin]["layout"], f"{twin} drifted")
            self.assertEqual(by_id[root]["data"], by_id[twin]["data"], f"{twin} drifted")

    def test_routing_stays_forward_only(self):
        """Meta rejects a backward edge; the screen order is the ordering."""
        order = [s["id"] for s in FLOW["screens"]]
        backward = [(src, t) for src, ts in FLOW["routing_model"].items() for t in ts
                    if order.index(t) < order.index(src)]
        self.assertEqual(backward, [])
