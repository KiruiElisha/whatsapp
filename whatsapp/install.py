"""Seed a working starter flow so the app is usable straight after install.

Nothing here sends anything on its own: WhatsApp Settings ships disabled, so the
rules stay dormant until the integration is deliberately switched on.
"""

import frappe

MENU = """Hi {{ push_name or contact_name }}! 👋 Thanks for contacting us.

Please reply with a number:
1. Talk to support
2. Opening hours
3. Pricing

Reply MENU at any time to see this again."""

SAMPLE_RULES = [
	{
		"title": "Greeting Menu",
		"priority": 10,
		"match_type": "Contains",
		"keywords": "hi\nhello\nhey\nstart\nmenu",
		"reply_type": "Text",
		"reply_message": MENU,
		"set_state": "MAIN_MENU",
	},
	{
		"title": "Menu Option 1 - Support",
		"priority": 20,
		"match_type": "Exact",
		"keywords": "1",
		"require_state": "MAIN_MENU",
		"reply_type": "Text",
		"reply_message": "Sure — please describe the issue and an agent will reply shortly.",
		"set_state": "CLEAR",
	},
	{
		"title": "Menu Option 2 - Opening Hours",
		"priority": 20,
		"match_type": "Exact",
		"keywords": "2",
		"require_state": "MAIN_MENU",
		"reply_type": "Text",
		"reply_message": "We are open Monday to Friday, 8am to 5pm.",
		"set_state": "CLEAR",
	},
	{
		"title": "Menu Option 3 - Pricing",
		"priority": 20,
		"match_type": "Exact",
		"keywords": "3",
		"require_state": "MAIN_MENU",
		"reply_type": "Text",
		"reply_message": "Tell us which product you are interested in and we will send pricing.",
		"set_state": "CLEAR",
	},
	{
		"title": "Invalid Menu Choice",
		"priority": 80,
		"match_type": "Any Message",
		"require_state": "MAIN_MENU",
		"reply_type": "Text",
		"reply_message": "Sorry, that is not one of the options. Please reply 1, 2 or 3.",
	},
]


def after_install() -> None:
	create_sample_rules()
	frappe.db.commit()


def create_sample_rules() -> None:
	for rule in SAMPLE_RULES:
		if frappe.db.exists("WhatsApp Auto Reply", rule["title"]):
			continue

		doc = frappe.get_doc({"doctype": "WhatsApp Auto Reply", "enabled": 1, **rule})
		doc.insert(ignore_permissions=True)
