"""Normalisation of phone numbers into the digits-only form WaClient expects."""

import re

import frappe
from frappe import _

CHAT_SUFFIXES = ("@c.us", "@s.whatsapp.net", "@g.us", "@lid")
GROUP_SUFFIXES = ("@g.us",)


def strip_suffix(value: str) -> str:
	"""Remove a WhatsApp JID suffix, leaving the bare identifier."""
	value = (value or "").strip()
	for suffix in CHAT_SUFFIXES:
		if value.lower().endswith(suffix):
			return value[: -len(suffix)]
	return value


def is_group_id(value: str) -> bool:
	value = (value or "").strip().lower()
	return value.endswith(GROUP_SUFFIXES) or "-" in strip_suffix(value)


def digits_only(value: str) -> str:
	return re.sub(r"\D", "", value or "")


def normalize(number: str, country_code: str | None = None, settings=None) -> str:
	"""Return a number as digits only, with a country code applied to local numbers.

	Numbers that already carry a country code are left alone. A leading 0 or a
	short number is treated as local and prefixed with the configured code.
	"""
	raw = strip_suffix(str(number or ""))

	# A group id is not a phone number; pass it through untouched.
	if is_group_id(number):
		return strip_suffix(str(number))

	cleaned = digits_only(raw)
	if not cleaned:
		frappe.throw(_("{0} is not a usable phone number.").format(number))

	if settings is None:
		settings = frappe.get_cached_doc("WhatsApp Settings")

	code = digits_only(country_code or settings.default_country_code)

	if not settings.assume_local_numbers or not code:
		return cleaned

	if cleaned.startswith(code) and len(cleaned) > len(code):
		return cleaned

	# 00 is the international dialling prefix, so what follows is already global.
	if cleaned.startswith("00"):
		return cleaned[2:]

	if raw.lstrip().startswith("+"):
		return cleaned

	if cleaned.startswith("0"):
		return code + cleaned.lstrip("0")

	if len(cleaned) <= 9:
		return code + cleaned

	return cleaned
