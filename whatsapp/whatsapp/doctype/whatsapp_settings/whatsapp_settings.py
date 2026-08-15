import datetime

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, get_url, get_time

WEBHOOK_PATH = "/api/method/whatsapp.api.webhook.receive"

DAY_ALIASES = {
	"mon": 0,
	"monday": 0,
	"tue": 1,
	"tues": 1,
	"tuesday": 1,
	"wed": 2,
	"weds": 2,
	"wednesday": 2,
	"thu": 3,
	"thur": 3,
	"thurs": 3,
	"thursday": 3,
	"fri": 4,
	"friday": 4,
	"sat": 5,
	"saturday": 5,
	"sun": 6,
	"sunday": 6,
}


class WhatsAppSettings(Document):
	def validate(self) -> None:
		self.api_base_url = (self.api_base_url or "").strip().rstrip("/")
		self.instance_api_base_url = (self.instance_api_base_url or "").strip().rstrip("/")
		self.public_base_url = (self.public_base_url or "").strip().rstrip("/")
		self.webhook_endpoint = self.build_webhook_endpoint()
		self.default_country_code = "".join(
			c for c in (self.default_country_code or "") if c.isdigit()
		)
		self.validate_accounts()
		self.validate_working_days()
		self.validate_excluded_numbers()

	def build_webhook_endpoint(self) -> str:
		"""Public URL WaClient should post to.

		get_url() reflects the current request, so it yields a loopback address
		when the webhook is registered from the console or a scheduled job.
		The override exists so that address is never what we register.
		"""
		if self.public_base_url:
			return f"{self.public_base_url}{WEBHOOK_PATH}"
		return get_url(WEBHOOK_PATH)

	def validate_webhook_reachable(self, url: str) -> None:
		"""Refuse to register a URL WaClient's servers could never reach."""
		from urllib.parse import urlparse

		parsed = urlparse(url)
		host = (parsed.hostname or "").lower()

		local = host in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "") or host.startswith(
			("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.3")
		)
		if local:
			frappe.throw(
				_(
					"The webhook URL resolves to a local address ({0}), which WaClient cannot reach "
					"from the internet. Set Public Base URL to this site's public HTTPS address first."
				).format(url)
			)

		if parsed.scheme != "https":
			frappe.msgprint(
				_(
					"WaClient expects an HTTPS webhook URL. {0} uses plain HTTP, so events may be "
					"rejected or dropped."
				).format(url),
				title=_("Insecure Webhook URL"),
				indicator="orange",
			)

	def validate_accounts(self) -> None:
		seen: set[str] = set()
		defaults: list[str] = []

		for row in self.accounts:
			row.account_name = (row.account_name or "").strip()
			row.instance_id = (row.instance_id or "").strip()

			key = row.account_name.casefold()
			if key in seen:
				frappe.throw(
					_("Row {0}: account name {1} is used more than once.").format(
						row.idx, frappe.bold(row.account_name)
					)
				)
			seen.add(key)

			if row.is_default:
				defaults.append(row.account_name)

		if len(defaults) > 1:
			frappe.throw(
				_("Only one account can be the default. Currently marked: {0}.").format(
					", ".join(defaults)
				)
			)

		enabled = [row for row in self.accounts if row.enabled]
		if enabled and not defaults:
			enabled[0].is_default = 1

	def validate_working_days(self) -> None:
		if not self.restrict_business_hours:
			return

		unknown = [
			token
			for token in split_days(self.working_days)
			if token.casefold() not in DAY_ALIASES
		]
		if unknown:
			frappe.throw(
				_("Unrecognised working days: {0}. Use short day names like Mon,Tue,Wed.").format(
					", ".join(unknown)
				)
			)

	def validate_excluded_numbers(self) -> None:
		"""Store excluded numbers normalised so any format the user types still matches."""
		from whatsapp.utils.phone import digits_only

		for row in self.excluded_numbers:
			cleaned = digits_only(row.phone_number)
			if not cleaned:
				frappe.throw(_("Row {0}: {1} is not a usable phone number.").format(row.idx, row.phone_number))
			row.phone_number = cleaned

	def is_excluded(self, number: str) -> bool:
		"""True when this number must never receive an automated reply."""
		from whatsapp.utils.phone import digits_only

		target = digits_only(number)
		if not target:
			return False

		for row in self.excluded_numbers:
			listed = digits_only(row.phone_number)
			if not listed:
				continue
			# Compare on the trailing digits so 0712345678 and 254712345678 match.
			if target == listed or target.endswith(listed[-9:]) and len(listed) >= 9:
				return True

		return False

	def get_account(self, account_name: str | None = None) -> dict:
		"""Return usable credentials for the named account, or the default one."""
		if not self.enabled:
			frappe.throw(_("WhatsApp integration is disabled in WhatsApp Settings."))

		candidates = [row for row in self.accounts if row.enabled]
		if not candidates:
			frappe.throw(_("No enabled WhatsApp account is configured in WhatsApp Settings."))

		row = None
		if account_name:
			row = next(
				(r for r in candidates if r.account_name.casefold() == account_name.casefold()),
				None,
			)
			if not row:
				frappe.throw(_("WhatsApp account {0} is not configured or not enabled.").format(account_name))
		else:
			row = next((r for r in candidates if r.is_default), candidates[0])

		token = row.get_password("access_token", raise_exception=False)
		if not token or not row.instance_id:
			frappe.throw(_("WhatsApp account {0} is missing an instance ID or access token.").format(row.account_name))

		return {
			"account_name": row.account_name,
			"instance_id": row.instance_id,
			"access_token": token,
			"phone_number": row.phone_number,
		}

	def is_within_business_hours(self, moment: datetime.datetime | None = None) -> bool:
		if not self.restrict_business_hours:
			return True

		moment = get_datetime(moment) if moment else frappe.utils.now_datetime()

		allowed = {DAY_ALIASES[t.casefold()] for t in split_days(self.working_days) if t.casefold() in DAY_ALIASES}
		if allowed and moment.weekday() not in allowed:
			return False

		if not (self.business_hours_start and self.business_hours_end):
			return True

		start = to_time(self.business_hours_start)
		end = to_time(self.business_hours_end)
		now = moment.time()

		if start <= end:
			return start <= now <= end

		# Window wraps past midnight, e.g. 20:00 to 06:00.
		return now >= start or now <= end

	@frappe.whitelist()
	def register_webhook(self) -> dict:
		"""Tell WaClient where to deliver inbound messages."""
		from whatsapp.api.client import WaClient

		if not self.webhook_enabled:
			frappe.throw(_("Enable the incoming webhook before registering it."))

		self.save()
		url = self.webhook_endpoint
		self.validate_webhook_reachable(url)

		if self.verify_webhook_token:
			token = self.get_password("webhook_token", raise_exception=False)
			if token:
				url = f"{url}?token={token}"

		result = WaClient().set_webhook(url)
		return {"webhook_url": url, "response": result}

	@frappe.whitelist()
	def test_connection(self, account: str | None = None) -> dict:
		"""Check that WaClient accepts the credentials and the phone is linked."""
		from whatsapp.api.client import WaClient

		client = WaClient(account)
		status = client.get_status().get("data") or {}

		info = {}
		try:
			info = client.get_instance_info().get("data") or {}
		except Exception:
			# Status already told us what we need; info is a bonus.
			pass

		state = status.get("connection_state") or "unknown"

		return {
			"account": client.account_name,
			"connection_state": state,
			"connected": state == "connected",
			"relogin_required": bool(status.get("relogin_required")),
			"phone": (info.get("account") or {}).get("phone"),
			"webhook_url": (info.get("webhook") or {}).get("webhook_url"),
			"webhook_enabled": (info.get("webhook") or {}).get("enabled"),
		}

	@frappe.whitelist()
	def test_ai(self, message: str = "Hi, how much is delivery to Nairobi?") -> dict:
		"""Run one message through the AI without sending anything to WhatsApp."""
		from whatsapp.services import ai

		if not ai.is_configured(self):
			frappe.throw(
				_("Set the AI provider, API key, and business context before testing.")
			)

		stub = frappe._dict({"name": None, "contact_name": "Test Contact", "wa_id": "test"})
		return ai.generate(stub, message, self, history=[])


def split_days(value: str | None) -> list[str]:
	return [token.strip() for token in (value or "").replace(";", ",").split(",") if token.strip()]


def to_time(value) -> datetime.time:
	value = get_time(value)
	return value if isinstance(value, datetime.time) else datetime.time(0, 0)


def get_settings() -> "WhatsAppSettings":
	return frappe.get_cached_doc("WhatsApp Settings")
