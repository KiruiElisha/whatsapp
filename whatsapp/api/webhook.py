"""Inbound webhook endpoint for WaClient and Nergaa WhatsApp."""

import hashlib
import hmac
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from whatsapp.services import auto_reply
from whatsapp.utils.payload import is_flat, parse_webhook
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

# Nergaa WhatsApp receipts carry Baileys' numbers: 1 pending, 2 server ack,
# 3 delivered, 4 read, 5 played.
RECEIPT_STATUS = {1: "Sent", 2: "Sent", 3: "Delivered", 4: "Read", 5: "Read"}

CONNECTION_EVENTS = ("connected", "disconnected", "logged_out")


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


def verify_signature(settings) -> bool | None:
	"""Check Nergaa WhatsApp's X-Webhook-Signature: sha256=hex(HMAC-SHA256(body, secret)).

	None when no secret is stored (nothing to check against); otherwise whether
	the delivery is genuine. Once a secret exists, unsigned posts are refused.
	"""
	secret = None
	if settings.get("nergaa_webhook_id"):
		secret = settings.get_password("nergaa_webhook_secret", raise_exception=False)
	if not secret:
		return None

	provided = frappe.get_request_header("X-Webhook-Signature") or ""
	if not provided:
		return False
	body = frappe.request.get_data(cache=True) or b""
	expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
	return hmac.compare_digest(provided.strip(), expected)


def verify_token(settings) -> bool:
	signed = verify_signature(settings)
	if signed is not None:
		return signed

	from whatsapp.api.client import NERGAA, provider_of

	if provider_of(settings) == NERGAA:
		# Nergaa WhatsApp always signs; until this site has its webhook secret (Link Phone or Register
		# Webhook), nothing unsigned is let in: it could be anyone posting fake customer messages.
		return False

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
	if is_flat(payload) and payload.get("event") != "message":
		return handle_event(payload, settings)

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


def handle_event(payload: dict, settings) -> dict:
	"""Nergaa WhatsApp events other than an incoming message."""
	event = payload.get("event")

	if event == "receipt":
		status = RECEIPT_STATUS.get(frappe.utils.cint(payload.get("status")))
		message_id = payload.get("message_id")
		if not (status and message_id):
			return {"status": "ignored", "reason": "receipt without status"}
		name = frappe.db.get_value(
			"WhatsApp Chat Message",
			{"message_id": message_id, "direction": "Outgoing"},
			["name", "status"],
			as_dict=True,
		)
		if not name:
			return {"status": "ignored", "reason": "unknown message"}
		# Receipts can arrive out of order; never step back from Read to Delivered.
		order = ["Queued", "Sent", "Delivered", "Read"]
		if name.status in order and order.index(status) <= order.index(name.status):
			return {"status": "ok", "handled": "receipt (no change)"}
		frappe.db.set_value("WhatsApp Chat Message", name.name, "status", status, update_modified=False)
		frappe.db.commit()
		return {"status": "ok", "handled": "receipt"}

	if event in CONNECTION_EVENTS:
		row = account_row(payload.get("session") or payload.get("instance_id"), settings)
		if not row:
			return {"status": "ignored", "reason": "unknown number"}
		updates = {"connection_state": event}
		if event == "connected" and payload.get("phone"):
			updates["phone_number"] = payload["phone"]
		frappe.db.set_value("WhatsApp Account", row.name, updates, update_modified=False)
		frappe.db.commit()
		frappe.clear_document_cache("WhatsApp Settings", "WhatsApp Settings")
		return {"status": "ok", "handled": event}

	return {"status": "ignored", "reason": f"event {event}"}


def account_row(instance: str | None, settings):
	if not instance:
		return None
	return next((row for row in settings.accounts if row.instance_id == instance), None)


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
