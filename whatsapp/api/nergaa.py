"""Client for Nergaa WhatsApp (whatsapp.nergaa.cloud).

One API key per gateway account, sent as a Bearer token. A number on the
gateway is an "instance"; WhatsApp Account rows keep its slug in Instance ID.
Every answer is JSON with `ok`, and failures add `error`.

On Nergaa Cloud the site config carries the account's key, so a site works
with nothing typed in:

    nergaa_whatsapp_url       https://whatsapp.nergaa.cloud
    nergaa_whatsapp_api_key   the account's API key
"""

import mimetypes

import requests

import frappe
from frappe import _

DEFAULT_URL = "https://whatsapp.nergaa.cloud"

# Events the site needs; qr and pairing_code are only useful while linking,
# and the Link Phone dialog polls for those instead.
WEBHOOK_EVENTS = ["message", "receipt", "connected", "disconnected", "logged_out"]

ERRORS = {
	401: _("Nergaa WhatsApp did not accept the API key."),
	403: _("The Nergaa WhatsApp account is suspended or this is beyond its plan."),
	409: _("This WhatsApp number is not connected. Link the phone again from WhatsApp Settings."),
	429: _("The Nergaa WhatsApp account has used this month's messages."),
}


class NergaaError(frappe.ValidationError):
	pass


def base_url(settings) -> str:
	return (
		(settings.get("nergaa_base_url") or "").strip()
		or frappe.conf.get("nergaa_whatsapp_url")
		or DEFAULT_URL
	).rstrip("/")


def api_key(settings) -> str:
	key = None
	if settings.get("nergaa_api_key"):
		key = settings.get_password("nergaa_api_key", raise_exception=False)
	return (key or frappe.conf.get("nergaa_whatsapp_api_key") or "").strip()


def is_configured(settings) -> bool:
	return bool(api_key(settings))


def media_kind(filename: str | None, mimetype: str | None = None) -> str:
	mimetype = mimetype or (mimetypes.guess_type(filename or "")[0] or "")
	for kind in ("image", "video", "audio"):
		if mimetype.startswith(kind + "/"):
			return kind
	return "document"


class NergaaAPI:
	"""Account-level calls: no WhatsApp Account row needed."""

	def __init__(self, settings=None, key: str | None = None):
		self.settings = settings or frappe.get_cached_doc("WhatsApp Settings")
		self.base_url = base_url(self.settings)
		self.key = key or api_key(self.settings)
		self.timeout = self.settings.request_timeout or 30
		if not self.key:
			frappe.throw(
				_("Add your Nergaa WhatsApp API key in WhatsApp Settings (Connection tab)."),
				NergaaError,
			)

	def request(self, method: str, path: str, payload: dict | None = None, params=None, timeout=None) -> dict:
		url = f"{self.base_url}/api/v1/{path.lstrip('/')}"
		headers = {
			"Authorization": f"Bearer {self.key}",
			"Accept": "application/json",
			"User-Agent": "Frappe-WhatsApp/1.0",
		}
		try:
			response = requests.request(
				method,
				url,
				json=payload if method.upper() not in ("GET", "DELETE") else None,
				params=params,
				headers=headers,
				timeout=timeout or self.timeout,
			)
		except requests.RequestException as exc:
			raise NergaaError(_("Could not reach Nergaa WhatsApp: {0}").format(exc)) from exc

		try:
			body = response.json()
		except ValueError:
			raise NergaaError(
				_("Nergaa WhatsApp returned a non-JSON response ({0}): {1}").format(
					response.status_code, (response.text or "")[:300]
				)
			)

		if response.status_code >= 400 or body.get("ok") is False:
			detail = body.get("error") or body.get("message") or ""
			known = ERRORS.get(response.status_code)
			message = f"{known} {detail}".strip() if known else detail or f"HTTP {response.status_code}"
			error = NergaaError(message)
			error.http_status = response.status_code
			raise error

		return body

	def me(self) -> dict:
		return self.request("GET", "me")

	def instances(self) -> list[dict]:
		return self.request("GET", "instances").get("instances") or []

	def create_instance(self, name: str, slug: str | None = None) -> dict:
		payload = {"name": name}
		if slug:
			payload["slug"] = slug
		return self.request("POST", "instances", payload)

	def instance(self, instance: str) -> dict:
		return self.request("GET", f"instances/{instance}")

	def start(self, instance: str, phone: str | None = None) -> dict:
		"""Open the socket. With a phone, ask for a pairing code instead of a QR."""
		if not phone:
			return self.request("POST", f"instances/{instance}/start", {}, timeout=40)
		try:
			return self.request("POST", f"instances/{instance}/pair", {"phone": phone}, timeout=40)
		except NergaaError as exc:
			if getattr(exc, "http_status", None) != 404:
				raise
			return self.request("POST", f"instances/{instance}/start", {"phone": phone}, timeout=40)

	def qr(self, instance: str) -> dict:
		return self.request("GET", f"instances/{instance}/qr")

	def stop(self, instance: str) -> dict:
		return self.request("POST", f"instances/{instance}/stop", {})

	def logout(self, instance: str) -> dict:
		return self.request("POST", f"instances/{instance}/logout", {})

	def delete(self, instance: str) -> dict:
		return self.request("DELETE", f"instances/{instance}")

	def check(self, instance: str, number: str) -> dict:
		return self.request("POST", f"instances/{instance}/check", {"number": number})

	def webhooks(self) -> list[dict]:
		return self.request("GET", "webhooks").get("webhooks") or []

	def create_webhook(self, url: str, events: list[str] | None = None, name: str | None = None) -> dict:
		payload = {"url": url, "events": events or WEBHOOK_EVENTS}
		if name:
			payload["name"] = name
		return self.request("POST", "webhooks", payload).get("webhook") or {}

	def delete_webhook(self, webhook_id: str) -> dict:
		return self.request("DELETE", f"webhooks/{webhook_id}")


class NergaaClient(NergaaAPI):
	"""Sends through one WhatsApp Account row, like WaClient does."""

	def __init__(self, account_name: str | None = None, settings=None):
		settings = settings or frappe.get_cached_doc("WhatsApp Settings")
		self.account = settings.get_account(account_name)
		# A row may carry its own key (another gateway account); else the shared one.
		super().__init__(settings, key=self.account.get("access_token") or None)
		self.slug = self.account["instance_id"]

	@property
	def account_name(self) -> str:
		return self.account["account_name"]

	def send_text(self, number: str, message: str) -> dict:
		return self.request("POST", f"instances/{self.slug}/send", {"to": number, "text": message})

	def send_media(
		self,
		number: str,
		message: str,
		media_url: str | None = None,
		filename: str | None = None,
		base64: str | None = None,
		mimetype: str | None = None,
	) -> dict:
		media = {"kind": media_kind(filename or media_url, mimetype), "caption": message or ""}
		if base64:
			media["base64"] = base64
		else:
			media["url"] = media_url
		if filename:
			media["filename"] = filename
		if mimetype:
			media["mimetype"] = mimetype
		return self.request("POST", f"instances/{self.slug}/send", {"to": number, "media": media}, timeout=90)

	def get_status(self) -> dict:
		return self.instance(self.slug)
