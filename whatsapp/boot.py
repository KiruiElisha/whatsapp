import frappe


def boot_session(bootinfo) -> None:
	"""Tell the desk whether to offer Send on WhatsApp."""
	try:
		enabled = frappe.db.get_single_value("WhatsApp Settings", "enabled")
	except Exception:
		enabled = 0
	bootinfo.whatsapp_enabled = bool(enabled) and frappe.has_permission("WhatsApp Chat Message", "create")
