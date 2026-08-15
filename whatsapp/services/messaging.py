"""Outbound sending, with every message logged against a contact."""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from whatsapp.api.client import WaClient, extract_message_id
from whatsapp.utils.phone import is_group_id, normalize
from whatsapp.whatsapp.doctype.whatsapp_contact.whatsapp_contact import (
	get_or_create,
	record_activity,
)


def send_message(
	to: str,
	message: str | None = None,
	media_url: str | None = None,
	media_filename: str | None = None,
	account: str | None = None,
	country_code: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	in_reply_to: str | None = None,
	matched_rule: str | None = None,
) -> dict:
	"""Send a WhatsApp message and record it.

	Returns the created WhatsApp Chat Message name alongside the provider response.
	"""
	settings = frappe.get_cached_doc("WhatsApp Settings")
	if not settings.enabled:
		frappe.throw(_("WhatsApp integration is disabled in WhatsApp Settings."))

	if not (message or media_url):
		frappe.throw(_("Provide a message, a media URL, or both."))

	number = normalize(to, country_code=country_code, settings=settings)
	contact = get_or_create(number, is_group=is_group_id(to))

	if contact.blocked:
		frappe.throw(_("{0} is blocked in WhatsApp Contact, so nothing was sent.").format(number))

	client = WaClient(account, settings=settings)

	log = None
	if settings.log_outgoing:
		log = frappe.get_doc(
			{
				"doctype": "WhatsApp Chat Message",
				"direction": "Outgoing",
				"status": "Queued",
				"account": client.account_name,
				"contact": contact.name,
				"from_number": client.account.get("phone_number"),
				"to_number": number,
				"chat_id": number,
				"is_group": contact.is_group,
				"message_type": "media" if media_url else "text",
				"message": message,
				"media_url": media_url,
				"media_filename": media_filename,
				"sent_on": now_datetime(),
				"in_reply_to": in_reply_to,
				"matched_rule": matched_rule,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
			}
		)
		log.insert(ignore_permissions=True)

	try:
		if media_url:
			response = client.send_media(number, message or "", media_url, media_filename)
		else:
			response = client.send_text(number, message)
	except Exception as exc:
		if log:
			log.db_set(
				{"status": "Failed", "error_message": str(exc)[:2000]}, update_modified=False
			)
		frappe.log_error(frappe.get_traceback(), f"WhatsApp send failed to {number}")
		raise

	if log:
		updates = {"status": "Sent", "message_id": extract_message_id(response)}
		if settings.store_raw_payload:
			updates["raw_payload"] = frappe.as_json(response)
		log.db_set(updates, update_modified=False)

	record_activity(contact, "Outgoing")

	return {
		"whatsapp_message": log.name if log else None,
		"to": number,
		"response": response,
	}


def send_template(
	to: str,
	template: str,
	context: dict | None = None,
	account: str | None = None,
	**kwargs,
) -> dict:
	"""Render a WhatsApp Template and send it."""
	doc = frappe.get_cached_doc("WhatsApp Template", template)
	if not doc.enabled:
		frappe.throw(_("WhatsApp Template {0} is disabled.").format(template))

	return send_message(
		to=to,
		message=doc.render(context),
		media_url=doc.media_url or None,
		media_filename=doc.media_filename or None,
		account=account,
		**kwargs,
	)


def enqueue_message(**kwargs) -> None:
	"""Queue a send so slow API calls never block the request."""
	frappe.enqueue(
		"whatsapp.services.messaging.send_message",
		queue="short",
		enqueue_after_commit=True,
		**kwargs,
	)
