"""Turn qualified WhatsApp conversations into ERPNext Leads.

Everything here is optional: if ERPNext is not installed the functions return
None and the rest of the automation carries on unaffected.
"""

import frappe
from frappe.utils import now_datetime

# Lead.status values that mean a human has already moved the lead on. The AI
# must not drag these backwards.
SETTLED_STATUSES = ("Converted", "Lost Quotation", "Do Not Contact", "Opportunity", "Quotation")


def is_available() -> bool:
	return bool(frappe.db.exists("DocType", "Lead"))


def capture(contact, result: dict, settings) -> str | None:
	"""Create or update the Lead behind a prospect. Returns the Lead name."""
	if not (settings.create_lead_for_prospects and result.get("is_prospect")):
		return None

	if not is_available():
		return None

	existing = contact.get("lead") or find_existing(contact)
	if existing:
		update_lead(existing, contact, result)
		if contact.get("lead") != existing:
			contact.db_set("lead", existing, update_modified=False)
		return existing

	return create_lead(contact, result, settings)


def find_existing(contact) -> str | None:
	"""Match on the phone number so a Lead entered by hand is reused."""
	tail = contact.wa_id[-9:] if len(contact.wa_id) >= 9 else contact.wa_id

	for field in ("mobile_no", "phone", "whatsapp_no"):
		if not frappe.get_meta("Lead").has_field(field):
			continue
		match = frappe.db.sql(
			f"select name from `tabLead` where replace(replace(replace(`{field}`, '+', ''), ' ', ''), '-', '') like %s order by creation desc limit 1",
			f"%{tail}",
		)
		if match:
			return match[0][0]

	return None


def create_lead(contact, result: dict, settings) -> str | None:
	name = (contact.contact_name or contact.push_name or "").strip()

	lead = frappe.new_doc("Lead")
	lead.first_name = name or f"WhatsApp {contact.wa_id}"
	lead.lead_name = lead.first_name
	lead.mobile_no = f"+{contact.wa_id}"
	lead.status = "Lead"
	lead.notes = []

	meta = frappe.get_meta("Lead")
	if settings.lead_owner and meta.has_field("lead_owner"):
		lead.lead_owner = settings.lead_owner
	if settings.lead_utm_source and meta.has_field("utm_source"):
		lead.utm_source = settings.lead_utm_source
	if meta.has_field("request_type"):
		lead.request_type = "Product Enquiry"

	add_note(lead, result)

	try:
		lead.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Could not create a Lead for {contact.wa_id}")
		return None

	contact.db_set("lead", lead.name, update_modified=False)
	return lead.name


def update_lead(name: str, contact, result: dict) -> None:
	try:
		lead = frappe.get_doc("Lead", name)
	except frappe.DoesNotExistError:
		return

	if lead.status not in SETTLED_STATUSES and lead.status == "Lead":
		lead.status = "Open"

	add_note(lead, result)

	try:
		lead.save(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Could not update Lead {name}")


def add_note(lead, result: dict) -> None:
	"""Record what the AI understood, so a human has the context."""
	if not frappe.get_meta("Lead").has_field("notes"):
		return

	summary = (result.get("summary") or "").strip()
	if not summary:
		return

	stamp = frappe.utils.format_datetime(now_datetime())
	note = (
		f"<p><b>WhatsApp enquiry</b> ({stamp})</p>"
		f"<p>{frappe.utils.escape_html(summary)}</p>"
		f"<p>Intent: {frappe.utils.escape_html(result.get('intent') or 'unknown')} "
		f"&middot; Score: {result.get('lead_score', 0)}/100</p>"
	)

	lead.append("notes", {"note": note, "added_by": frappe.session.user, "added_on": now_datetime()})
