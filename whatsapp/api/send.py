"""Whitelisted endpoints for sending WhatsApp messages.

These require an authenticated session on purpose: an open send endpoint lets
anyone use the connected number to message arbitrary people.
"""

import frappe
from frappe import _

from whatsapp.services import messaging


def check_permission() -> None:
	if not frappe.has_permission("WhatsApp Chat Message", "read"):
		frappe.throw(_("You are not permitted to send WhatsApp messages."), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def send_text(
	to: str,
	message: str,
	account: str | None = None,
	country_code: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> dict:
	"""Send a plain text WhatsApp message."""
	check_permission()
	return messaging.send_message(
		to=to,
		message=message,
		account=account,
		country_code=country_code,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)


@frappe.whitelist(methods=["POST"])
def send_media(
	to: str,
	media_url: str,
	message: str | None = None,
	media_filename: str | None = None,
	account: str | None = None,
	country_code: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> dict:
	"""Send a file or image, optionally with a caption."""
	check_permission()
	return messaging.send_message(
		to=to,
		message=message,
		media_url=media_url,
		media_filename=media_filename,
		account=account,
		country_code=country_code,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)


@frappe.whitelist(methods=["POST"])
def send_template(
	to: str,
	template: str,
	context: dict | str | None = None,
	account: str | None = None,
	country_code: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> dict:
	"""Render a WhatsApp Template and send it."""
	check_permission()

	if isinstance(context, str):
		context = frappe.parse_json(context)

	return messaging.send_template(
		to=to,
		template=template,
		context=context or {},
		account=account,
		country_code=country_code,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)
