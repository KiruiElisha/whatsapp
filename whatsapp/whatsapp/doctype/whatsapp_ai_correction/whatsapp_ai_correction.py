"""Lessons recorded from AI replies that went wrong.

Each record is fed back into the system prompt so the model stops repeating a
mistake it has already made.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class WhatsAppAICorrection(Document):
	def validate(self) -> None:
		self.title = (self.title or "").strip()
		self.applies_when = (self.applies_when or "").strip()
		self.wrong_reply = (self.wrong_reply or "").strip()
		self.correct_behaviour = (self.correct_behaviour or "").strip()

		if self.applies_when and self.applies_when == self.correct_behaviour:
			frappe.throw(
				_("'Applies When' describes the situation and 'What It Should Do Instead' the fix. They cannot be identical.")
			)

	def on_update(self) -> None:
		frappe.cache().delete_value(CACHE_KEY)

	def on_trash(self) -> None:
		frappe.cache().delete_value(CACHE_KEY)


CACHE_KEY = "whatsapp_ai_corrections"


def get_active(account: str | None = None, limit: int = 20) -> list[dict]:
	"""Enabled lessons for this account, most important first."""
	if not limit or limit <= 0:
		return []

	rows = frappe.cache().get_value(CACHE_KEY)
	if rows is None:
		rows = frappe.get_all(
			"WhatsApp AI Correction",
			filters={"enabled": 1},
			fields=[
				"name",
				"account",
				"applies_when",
				"wrong_reply",
				"correct_behaviour",
				"priority",
			],
			order_by="priority asc, modified desc",
		)
		frappe.cache().set_value(CACHE_KEY, rows)

	scoped = [r for r in rows if not r.get("account") or r["account"] == account]
	return scoped[:limit]


def record_applied(names: list[str]) -> None:
	"""Bump usage counters. Best effort, never breaks a reply."""
	if not names:
		return

	try:
		now = now_datetime()
		for name in names:
			frappe.db.sql(
				"""update `tabWhatsApp AI Correction`
				   set times_applied = ifnull(times_applied, 0) + 1, last_applied_on = %s
				 where name = %s""",
				(now, name),
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "WhatsApp AI correction counter failed")


@frappe.whitelist()
def create_from_message(message: str, correct_behaviour: str, title: str | None = None) -> str:
	"""Turn a bad reply into a lesson, straight from the chat message form."""
	msg = frappe.get_doc("WhatsApp Chat Message", message)

	doc = frappe.get_doc(
		{
			"doctype": "WhatsApp AI Correction",
			"title": (title or "").strip() or f"Correction from {msg.name}",
			"applies_when": _("A customer sends a message like this one."),
			"wrong_reply": msg.message,
			"correct_behaviour": correct_behaviour,
			"source_message": msg.name,
			"contact": msg.contact,
			"account": msg.account,
		}
	)
	doc.insert()
	return doc.name
