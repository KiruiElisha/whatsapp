import frappe
from frappe.model.document import Document
from frappe.utils import add_days, today


class WhatsAppChatMessage(Document):
	pass


def delete_old_logs() -> None:
	"""Trim the message log to the retention window set in WhatsApp Settings."""
	days = frappe.db.get_single_value("WhatsApp Settings", "log_retention_days")
	if not days or int(days) <= 0:
		return

	frappe.db.delete(
		"WhatsApp Chat Message", {"creation": ("<", add_days(today(), -int(days)))}
	)
