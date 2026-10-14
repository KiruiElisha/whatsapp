"""Link a phone to Nergaa WhatsApp from inside ERPNext.

The Link Phone dialog in WhatsApp Settings calls `start`, then polls `status`
every few seconds and shows whatever the gateway has: a QR code to scan
(WhatsApp → Linked devices → Link a device), or an 8-character pairing code to
type in when a phone number was given. Once the number connects, the WhatsApp
Account row records it and the site's webhook is registered.
"""

import re

import frappe
from frappe import _

from whatsapp.api.nergaa import NergaaAPI


def check_permission() -> None:
	if not frappe.has_permission("WhatsApp Settings", "write"):
		frappe.throw(_("Only a WhatsApp Settings manager can link phones."), frappe.PermissionError)


def settings_doc():
	return frappe.get_doc("WhatsApp Settings")


def find_row(settings, account: str):
	return next(
		(r for r in settings.accounts if (r.account_name or "").casefold() == (account or "").casefold()),
		None,
	)


def slug_for(account: str) -> str:
	site = re.sub(r"[^a-z0-9]+", "-", (frappe.local.site or "erp").lower()).strip("-")
	name = re.sub(r"[^a-z0-9]+", "-", account.lower()).strip("-") or "main"
	return f"{site}-{name}"[:60].strip("-")


def ensure_instance(settings, api: NergaaAPI, account: str):
	"""The account row, created with a gateway number behind it if it is new."""
	row = find_row(settings, account)
	if row and row.instance_id:
		return row

	slug = slug_for(account)
	existing = {i.get("session") or i.get("slug") for i in api.instances()}
	if slug not in existing:
		created = api.create_instance(name=f"{account} ({frappe.local.site})"[:80], slug=slug)
		slug = created.get("instance_id") or created.get("session") or slug

	if not row:
		row = settings.append(
			"accounts",
			{"account_name": account, "enabled": 1, "is_default": 0 if settings.accounts else 1},
		)
	row.instance_id = slug
	return row


def view(status: dict, row) -> dict:
	state = status.get("state") or "unknown"
	return {
		"account": row.account_name,
		"instance": row.instance_id,
		"state": state,
		"connected": state == "connected",
		"qr": status.get("qr"),
		"pairing_code": status.get("pairing_code"),
		"phone": status.get("phone"),
		"name": status.get("wa_name"),
		"error": status.get("last_error"),
	}


@frappe.whitelist(methods=["POST"])
def start(account: str = "Main", phone: str | None = None) -> dict:
	"""Begin linking: a QR code, or with `phone` a pairing code."""
	check_permission()
	account = (account or "Main").strip()

	settings = settings_doc()
	if not settings.provider:
		settings.provider = "Nergaa WhatsApp"
	if settings.provider != "Nergaa WhatsApp":
		frappe.throw(_("Linking phones from ERPNext needs the Nergaa WhatsApp provider."))

	api = NergaaAPI(settings)
	row = ensure_instance(settings, api, account)
	settings.enabled = 1
	settings.save()
	row = find_row(settings, account)

	digits = re.sub(r"\D", "", phone or "")
	if phone and len(digits) < 8:
		frappe.throw(_("Enter the phone number in international format, e.g. 254712345678."))

	status = api.start(row.instance_id, digits or None)
	if not digits and not status.get("qr") and status.get("state") != "connected":
		status = {**status, **api.qr(row.instance_id)}

	result = view(status, row)
	if result["connected"]:
		finish(settings, row, status)
	return result


@frappe.whitelist()
def status(account: str = "Main") -> dict:
	"""Where linking has got to; the dialog polls this."""
	check_permission()
	settings = settings_doc()
	row = find_row(settings, account)
	if not row or not row.instance_id:
		frappe.throw(_("No WhatsApp account named {0}.").format(account))

	api = NergaaAPI(settings)
	current = api.qr(row.instance_id)
	if current.get("state") == "connected":
		current = {**current, **api.instance(row.instance_id)}
		finish(settings, row, current)
	return view(current, row)


def finish(settings, row, current: dict) -> None:
	"""The phone is linked: remember the number and make sure messages come in."""
	updates = {"connection_state": "connected"}
	if current.get("phone"):
		updates["phone_number"] = current["phone"]
	frappe.db.set_value("WhatsApp Account", row.name, updates, update_modified=False)

	if not settings.nergaa_webhook_id:
		try:
			settings.reload()
			settings.register_nergaa_webhook()
		except Exception:
			# The phone is still linked; the webhook can be registered from Settings.
			frappe.log_error(
				frappe.get_traceback(), "Nergaa WhatsApp: webhook registration after linking failed"
			)
			frappe.clear_messages()

	frappe.clear_document_cache("WhatsApp Settings", "WhatsApp Settings")


@frappe.whitelist(methods=["POST"])
def unlink(account: str) -> dict:
	"""Log the phone out on the gateway. The account row stays for history."""
	check_permission()
	settings = settings_doc()
	row = find_row(settings, account)
	if not row or not row.instance_id:
		frappe.throw(_("No WhatsApp account named {0}.").format(account))

	result = NergaaAPI(settings).logout(row.instance_id)
	frappe.db.set_value("WhatsApp Account", row.name, "connection_state", "logged_out", update_modified=False)
	frappe.clear_document_cache("WhatsApp Settings", "WhatsApp Settings")
	return {"account": row.account_name, "state": result.get("state") or "logged_out"}


@frappe.whitelist()
def accounts() -> list[dict]:
	"""Numbers on the gateway account, with their live state."""
	check_permission()
	settings = settings_doc()
	live = {i.get("session") or i.get("slug"): i for i in NergaaAPI(settings).instances()}
	return [
		{
			"account": r.account_name,
			"instance": r.instance_id,
			"phone": (live.get(r.instance_id) or {}).get("phone") or r.phone_number,
			"state": (live.get(r.instance_id) or {}).get("state") or "unknown",
			"enabled": r.enabled,
			"is_default": r.is_default,
		}
		for r in settings.accounts
	]
