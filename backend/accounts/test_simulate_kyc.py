"""Tests for the TEST-ONLY simulated-KYC endpoint (/api/dev/simulate-kyc/).

Gated three ways — WEMA_SIMULATION on, a matching SIMULATE_DEPOSIT_TOKEN (shared
with simulate-deposit), and a valid tier for an existing user — so it can verify a
test user and provision a mock NUBAN for end-to-end testing, but can NEVER run on a
live-money deploy (simulation off => 404).
"""
import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from wallet.services import get_or_create_wallet

User = get_user_model()

_SIM_ON = {"SIMULATION": True}
_SIM_OFF = {"SIMULATION": False}
_TOKEN = "sim-secret-token"
_PHONE = "08160000001"
