import json

import frappe
from frappe.model.document import Document
from frappe.utils import add_to_date, now_datetime


class WhatsAppContact(Document):
	def get_state(self) -> str | None:
		"""Current flow step, or None once it has expired."""
		if not self.conversation_state:
			return None

		if self.state_expires_on and now_datetime() > frappe.utils.get_datetime(self.state_expires_on):
			return None

		return self.conversation_state

	def get_state_data(self) -> dict:
		if not self.state_data:
			return {}
		try:
			data = json.loads(self.state_data)
		except (ValueError, TypeError):
			return {}
		return data if isinstance(data, dict) else {}

	def set_state(self, state: str | None, timeout_minutes: int = 30, data: dict | None = None) -> None:
		"""Move the contact to a flow step, or clear it when state is falsy."""
		if not state:
			self.db_set(
				{"conversation_state": None, "state_expires_on": None, "state_data": None},
				update_modified=False,
			)
			return

		expires = add_to_date(now_datetime(), minutes=timeout_minutes or 30)
		self.db_set(
			{
				"conversation_state": state,
				"state_expires_on": expires,
				"state_data": json.dumps(data or {}),
			},
			update_modified=False,
		)


def get_or_create(wa_id: str, push_name: str | None = None, is_group: bool = False) -> WhatsAppContact:
	"""Fetch the contact for a WhatsApp id, creating it on first contact."""
	wa_id = (wa_id or "").strip()
	if not wa_id:
		frappe.throw("A WhatsApp ID is required to identify a contact.")

	name = frappe.db.get_value("WhatsApp Contact", {"wa_id": wa_id}, "name")
	if name:
		contact = frappe.get_doc("WhatsApp Contact", name)
		if push_name and contact.push_name != push_name:
			contact.db_set("push_name", push_name, update_modified=False)
		return contact

	contact = frappe.get_doc(
		{
			"doctype": "WhatsApp Contact",
			"wa_id": wa_id,
			"push_name": push_name,
			"contact_name": push_name or wa_id,
			"is_group": 1 if is_group else 0,
		}
	)
	contact.insert(ignore_permissions=True)
	return contact


def record_activity(contact: WhatsAppContact, direction: str) -> None:
	field = "incoming_count" if direction == "Incoming" else "outgoing_count"
	contact.db_set(
		{field: (contact.get(field) or 0) + 1, "last_message_on": now_datetime()},
		update_modified=False,
	)
