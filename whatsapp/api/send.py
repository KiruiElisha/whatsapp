"""Whitelisted endpoints for sending WhatsApp messages.

These require an authenticated session on purpose: an open send endpoint lets
anyone use the connected number to message arbitrary people.
"""

import frappe
from frappe import _

from whatsapp.services import messaging


def check_permission() -> None:
	# Sending speaks for the company to anyone: reading the message log isn't enough.
	if not frappe.has_permission("WhatsApp Chat Message", "create"):
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


# Fields that commonly hold a party's mobile number, most specific first.
PHONE_FIELDS = (
	"whatsapp_no",
	"mobile_no",
	"contact_mobile",
	"contact_phone",
	"phone",
	"phone_no",
	"cell_number",
)


def first_number(record) -> str | None:
	return next((record.get(f) for f in PHONE_FIELDS if record.get(f)), None)


def document_number(doc) -> str | None:
	"""The document's own number, else its contact person's, else its customer's or supplier's."""
	number = first_number(doc)
	if not number and doc.get("contact_person"):
		number = first_number(frappe.get_doc("Contact", doc.contact_person))
	party_type = doc.get("party_type") or doc.get("quotation_to") or ("Customer" if doc.get("customer") else
		"Supplier" if doc.get("supplier") else None)
	party = doc.get("party") or doc.get("party_name") or doc.get("customer") or doc.get("supplier")
	if not number and party_type in ("Customer", "Supplier") and party:
		number = first_number(frappe.get_doc(party_type, party))
	return number


@frappe.whitelist()
def document_defaults(doctype: str, name: str) -> dict:
	"""Prefill the Send on WhatsApp dialog: a number from the document and a message."""
	check_permission()
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")

	number = document_number(doc)
	party = (
		doc.get("customer_name")
		or doc.get("supplier_name")
		or doc.get("party_name")
		or doc.get("contact_display")
	)

	greeting = _("Dear {0},").format(party) if party else _("Hello,")
	title = _(doctype)
	message = _("{0} please find attached {1} {2}.").format(greeting, title, name)
	if doc.get("grand_total") and doc.get("currency"):
		message += " " + _("Amount: {0}.").format(
			frappe.format_value(doc.grand_total, {"fieldtype": "Currency", "options": doc.currency}, doc)
		)

	from whatsapp.api.client import NERGAA, provider_of

	settings = frappe.get_cached_doc("WhatsApp Settings")
	return {
		"to": number,
		"message": message,
		"can_attach": provider_of(settings) == NERGAA,
		"print_format": frappe.get_meta(doctype).default_print_format or "Standard",
	}


@frappe.whitelist(methods=["POST"])
def send_document(
	doctype: str,
	name: str,
	to: str,
	message: str | None = None,
	attach_pdf: int | str = 1,
	print_format: str | None = None,
	account: str | None = None,
) -> dict:
	"""Send a document on WhatsApp, with its print PDF attached."""
	import base64

	check_permission()
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("print" if frappe.utils.cint(attach_pdf) else "read")

	kwargs = {
		"to": to,
		"message": message,
		"account": account,
		"reference_doctype": doctype,
		"reference_name": name,
	}

	if frappe.utils.cint(attach_pdf):
		pdf = frappe.get_print(doctype, name, print_format=print_format or None, doc=doc, as_pdf=True)
		safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in name)
		kwargs.update(
			media_base64=base64.b64encode(pdf).decode(),
			media_filename=f"{safe}.pdf",
			media_mimetype="application/pdf",
		)

	result = messaging.send_message(**kwargs)
	doc.add_comment("Info", _("Sent on WhatsApp to {0}").format(result.get("to")))
	return result
