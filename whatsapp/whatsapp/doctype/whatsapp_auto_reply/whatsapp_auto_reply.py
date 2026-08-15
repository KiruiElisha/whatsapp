import re

import frappe
from frappe import _
from frappe.model.document import Document


class WhatsAppAutoReply(Document):
	def validate(self) -> None:
		self.validate_keywords()
		self.validate_condition()

		if self.reply_type == "Media" and not self.media_url:
			frappe.throw(_("A media URL is required for a Media reply."))

	def validate_keywords(self) -> None:
		if self.match_type == "Any Message":
			return

		keywords = [k.strip() for k in (self.keywords or "").splitlines() if k.strip()]
		if not keywords:
			frappe.throw(_("Add at least one keyword, or set Match Type to Any Message."))

		if self.match_type == "Regex":
			for keyword in keywords:
				try:
					re.compile(keyword)
				except re.error as exc:
					frappe.throw(_("{0} is not a valid regular expression: {1}").format(keyword, exc))

	def validate_condition(self) -> None:
		if not (self.condition or "").strip():
			return

		try:
			compile(self.condition, "<condition>", "eval")
		except SyntaxError as exc:
			frappe.throw(_("The condition is not a valid Python expression: {0}").format(exc))
