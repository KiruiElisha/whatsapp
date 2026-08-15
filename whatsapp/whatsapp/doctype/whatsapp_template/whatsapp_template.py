import frappe
from frappe.model.document import Document


class WhatsAppTemplate(Document):
	def render(self, context: dict | None = None) -> str:
		return frappe.render_template(self.message, context or {})
