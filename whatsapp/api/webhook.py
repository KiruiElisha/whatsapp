"""Inbound webhook endpoint for WaClient."""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from whatsapp.services import auto_reply
from whatsapp.utils.payload import parse_webhook
from whatsapp.whatsapp.doctype.whatsapp_contact.whatsapp_contact import (
	get_or_create,
	record_activity,
)

# Provider status names mapped onto the WhatsApp Chat Message status field.
ACK_STATUS = {
	"1": "Sent",
	"2": "Delivered",
	"3": "Read",
	"4": "Read",
	"sent": "Sent",
	"server_ack": "Sent",
	"delivered": "Delivered",
	"delivery_ack": "Delivered",
	"read": "Read",
	"played": "Read",
	"failed": "Failed",
	"error": "Failed",
}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive() -> dict:
	"""Accept an inbound WaClient event.

	Always answers 200 with a status body: a webhook that returns errors gets
	retried or disabled by the provider, which is worse than dropping one event.
	"""
	settings = frappe.get_cached_doc("WhatsApp Settings")

	if not settings.webhook_enabled:
		return {"status": "ignored", "reason": "webhook disabled"}

	if not verify_token(settings):
		frappe.local.response["http_status_code"] = 401
		return {"status": "unauthorised"}

	payload = read_payload()
	if payload is None:
		return {"status": "ignored", "reason": "unreadable payload"}

	try:
		return handle(payload, settings)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(
			f"{frappe.get_traceback()}\n\nPayload:\n{json.dumps(payload, default=str)[:3000]}",
			"WhatsApp webhook failed",
		)
		return {"status": "error"}


def verify_token(settings) -> bool:
	if not settings.verify_webhook_token:
		return True

	expected = settings.get_password("webhook_token", raise_exception=False)
	if not expected:
		return True

	provided = (
		frappe.form_dict.get("token")
		or frappe.get_request_header("X-Webhook-Token")
		or frappe.get_request_header("X-Token")
		or ""
	)

	import hmac

	return hmac.compare_digest(str(provided), str(expected))


def read_payload() -> dict | None:
	try:
		raw = frappe.request.get_data(as_text=True)
		if raw:
			data = json.loads(raw)
			if isinstance(data, dict):
				return data
	except (ValueError, TypeError, AttributeError):
		pass

	# Some providers post form-encoded bodies instead of JSON.
	data = {k: v for k, v in frappe.form_dict.items() if k not in ("cmd", "token")}
	return data or None


def handle(payload: dict, settings) -> dict:
	parsed = parse_webhook(payload)
	if not parsed:
		return {"status": "ignored", "reason": "no message in payload"}

	if update_delivery_status(payload, parsed):
		frappe.db.commit()
		return {"status": "ok", "handled": "status update"}

	# Our own outgoing messages echo back through the webhook.
	if parsed.get("from_me"):
		return {"status": "ignored", "reason": "outgoing echo"}

	if not parsed.get("wa_id"):
		return {"status": "ignored", "reason": "no sender"}

	account = resolve_account(parsed.get("instance_id"), settings)
	log_name = log_incoming(parsed, payload, settings, account)

	frappe.db.commit()

	result = None
	try:
		result = auto_reply.process({**parsed, "account": account}, log_name)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "WhatsApp auto reply failed")

	frappe.db.commit()

	return {
		"status": "ok",
		"whatsapp_message": log_name,
		"auto_reply": bool(result),
	}


def resolve_account(instance_id: str | None, settings) -> str | None:
	if not instance_id:
		return None
	for row in settings.accounts:
		if row.instance_id == instance_id:
			return row.account_name
	return None


def log_incoming(parsed: dict, payload: dict, settings, account: str | None) -> str | None:
	contact = get_or_create(parsed["wa_id"], parsed.get("push_name"), parsed.get("is_group"))
	record_activity(contact, "Incoming")

	if not settings.log_incoming:
		return None

	# Providers retry, so do not store the same message twice.
	if parsed.get("message_id"):
		existing = frappe.db.get_value(
			"WhatsApp Chat Message",
			{"message_id": parsed["message_id"], "direction": "Incoming"},
			"name",
		)
		if existing:
			return existing

	doc = frappe.get_doc(
		{
			"doctype": "WhatsApp Chat Message",
			"direction": "Incoming",
			"status": "Received",
			"account": account,
			"contact": contact.name,
			"from_number": parsed["wa_id"],
			"to_number": None,
			"chat_id": parsed.get("chat_id"),
			"is_group": 1 if parsed.get("is_group") else 0,
			"push_name": parsed.get("push_name"),
			"message_type": parsed.get("message_type") or "text",
			"message": parsed.get("message"),
			"media_url": parsed.get("media_url"),
			"media_filename": parsed.get("media_filename"),
			"message_id": parsed.get("message_id"),
			"sent_on": parsed.get("timestamp") or now_datetime(),
			"raw_payload": frappe.as_json(payload) if settings.store_raw_payload else None,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def update_delivery_status(payload: dict, parsed: dict) -> bool:
	"""Apply an ack/status event to the matching outgoing message."""
	event = str(parsed.get("event") or "").lower()
	if "ack" not in event and "status" not in event:
		return False

	message_id = parsed.get("message_id")
	if not message_id:
		return False

	data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
	raw_status = (
		data.get("status")
		or data.get("ack")
		or payload.get("status")
		or payload.get("ack")
		or ""
	)
	status = ACK_STATUS.get(str(raw_status).lower().strip())
	if not status:
		return False

	name = frappe.db.get_value(
		"WhatsApp Chat Message", {"message_id": message_id, "direction": "Outgoing"}, "name"
	)
	if not name:
		return False

	frappe.db.set_value("WhatsApp Chat Message", name, "status", status, update_modified=False)
	return True
