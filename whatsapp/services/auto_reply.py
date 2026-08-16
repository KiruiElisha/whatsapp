"""Rule driven automated replies, with an AI assistant behind the rules.

Order of play for each inbound message:
  1. Skip anything excluded (groups, excluded numbers, opted out contacts).
  2. Walk the WhatsApp Auto Reply rules by priority. A rule may reply itself or
     delegate to the AI.
  3. If no rule replied, fall back to the AI, then to the static fallback text.
"""

import re

import frappe
from frappe.utils import now_datetime

from whatsapp.services import ai, lead
from whatsapp.services.messaging import send_message
from whatsapp.whatsapp.doctype.whatsapp_contact.whatsapp_contact import get_or_create

CLEAR_STATE = "CLEAR"


def process(inbound: dict, message_log: str | None = None) -> dict | None:
	"""Run the automation for one inbound message.

	Returns a summary of what was sent, or None when nothing was sent.
	"""
	settings = frappe.get_cached_doc("WhatsApp Settings")
	if not (settings.enabled and settings.auto_reply_enabled):
		return None

	if inbound.get("is_group") and not settings.reply_to_groups:
		return None

	text = (inbound.get("message") or "").strip()
	wa_id = inbound.get("wa_id")
	if not wa_id:
		return None

	if settings.is_excluded(wa_id):
		return None

	contact = get_or_create(wa_id, inbound.get("push_name"), bool(inbound.get("is_group")))
	if contact.opted_out or contact.blocked or contact.exclude_from_auto_reply:
		return None

	state = contact.get_state()
	within_hours = settings.is_within_business_hours()

	context = {
		"message": text,
		"wa_id": wa_id,
		"push_name": inbound.get("push_name") or "",
		"contact_name": contact.contact_name or inbound.get("push_name") or wa_id,
		"contact": contact,
		"state": state or "",
		"is_group": bool(inbound.get("is_group")),
	}

	for rule in get_rules(inbound.get("account")):
		if not matches(rule, text, state, within_hours, context):
			continue

		result = apply_rule(rule, contact, context, settings, message_log)

		frappe.db.set_value(
			"WhatsApp Auto Reply",
			rule.name,
			{"match_count": (rule.match_count or 0) + 1, "last_matched_on": now_datetime()},
			update_modified=False,
		)

		if rule.stop_processing:
			return {"matched": rule.name, "sent": result}

	return send_default_reply(contact, context, settings, within_hours, message_log)


def get_rules(account: str | None) -> list:
	names = frappe.get_all(
		"WhatsApp Auto Reply",
		filters={"enabled": 1},
		or_filters=[["account", "is", "not set"], ["account", "=", account or ""]],
		pluck="name",
		order_by="priority asc, creation asc",
	)
	return [frappe.get_cached_doc("WhatsApp Auto Reply", name) for name in names]


def matches(rule, text: str, state: str | None, within_hours: bool, context: dict) -> bool:
	if rule.business_hours_only and not within_hours:
		return False

	if rule.require_state and (state or "") != rule.require_state:
		return False

	if not matches_keywords(rule, text):
		return False

	return passes_condition(rule, context)


def matches_keywords(rule, text: str) -> bool:
	if rule.match_type == "Any Message":
		return True

	subject = text if rule.case_sensitive else text.casefold()
	keywords = [k.strip() for k in (rule.keywords or "").splitlines() if k.strip()]

	for keyword in keywords:
		needle = keyword if rule.case_sensitive else keyword.casefold()

		if rule.match_type == "Exact" and subject == needle:
			return True
		if rule.match_type == "Contains" and needle in subject:
			return True
		if rule.match_type == "Starts With" and subject.startswith(needle):
			return True
		if rule.match_type == "Regex":
			flags = 0 if rule.case_sensitive else re.IGNORECASE
			try:
				if re.search(keyword, text, flags):
					return True
			except re.error:
				frappe.log_error(
					f"Invalid regex in WhatsApp Auto Reply {rule.name}: {keyword}",
					"WhatsApp Auto Reply",
				)

	return False


def passes_condition(rule, context: dict) -> bool:
	if not (rule.condition or "").strip():
		return True

	try:
		return bool(frappe.safe_eval(rule.condition, None, dict(context)))
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"WhatsApp Auto Reply condition failed: {rule.name}")
		return False


def apply_rule(rule, contact, context: dict, settings, message_log: str | None) -> dict | None:
	reply = None

	if rule.reply_type == "Text":
		reply = send_message(
			to=contact.wa_id,
			message=render(rule.reply_message, context, rule.name),
			in_reply_to=message_log,
			matched_rule=rule.name,
		)
	elif rule.reply_type == "Template":
		from whatsapp.services.messaging import send_template

		reply = send_template(
			to=contact.wa_id,
			template=rule.template,
			context=context,
			in_reply_to=message_log,
			matched_rule=rule.name,
		)
	elif rule.reply_type == "Media":
		reply = send_message(
			to=contact.wa_id,
			message=render(rule.reply_message, context, rule.name),
			media_url=rule.media_url,
			media_filename=rule.media_filename,
			in_reply_to=message_log,
			matched_rule=rule.name,
		)
	elif rule.reply_type == "AI":
		reply = send_ai_reply(contact, context, settings, message_log, rule.name)

	if rule.set_state:
		if rule.set_state == CLEAR_STATE:
			contact.set_state(None)
		else:
			contact.set_state(rule.set_state, settings.session_timeout_minutes or 30)

	if reply and message_log:
		frappe.db.set_value(
			"WhatsApp Chat Message",
			message_log,
			{"auto_replied": 1, "matched_rule": rule.name},
			update_modified=False,
		)

	return reply


def send_default_reply(contact, context: dict, settings, within_hours: bool, message_log: str | None):
	"""Nothing matched: try the AI, then the configured fallback text."""
	if settings.ai_enabled and settings.ai_reply_when_no_rule_matches:
		reply = send_ai_reply(contact, context, settings, message_log)
		if reply:
			return {"matched": "ai", "sent": reply}

	message = None
	if not within_hours and settings.outside_hours_reply:
		message = settings.outside_hours_reply
	elif settings.send_fallback_reply and settings.fallback_reply:
		message = settings.fallback_reply

	if not message:
		return None

	reply = send_message(
		to=contact.wa_id,
		message=render(message, context, "fallback"),
		in_reply_to=message_log,
	)

	if message_log:
		frappe.db.set_value("WhatsApp Chat Message", message_log, "auto_replied", 1, update_modified=False)

	return {"matched": None, "sent": reply}


def send_ai_reply(
	contact, context: dict, settings, message_log: str | None, rule: str | None = None
) -> dict | None:
	"""Ask the AI for a reply, record what it concluded, and send it."""
	try:
		result = ai.generate(
			contact, context["message"], settings, account=context.get("account")
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "WhatsApp AI reply failed")
		return None

	if not result:
		return None

	if settings.ai_qualify_leads:
		record_qualification(contact, result, message_log, settings)

	text = result["reply"]
	if result["needs_human"] and settings.ai_escalate_to_human and settings.ai_handoff_message:
		text = (text + "\n\n" + settings.ai_handoff_message).strip() if text else settings.ai_handoff_message

	if not text:
		return None

	reply = send_message(
		to=contact.wa_id,
		message=text,
		in_reply_to=message_log,
		matched_rule=rule,
	)

	if reply.get("whatsapp_message"):
		frappe.db.set_value(
			"WhatsApp Chat Message", reply["whatsapp_message"], "generated_by_ai", 1, update_modified=False
		)

	if message_log:
		frappe.db.set_value("WhatsApp Chat Message", message_log, "auto_replied", 1, update_modified=False)

	return reply


def record_qualification(contact, result: dict, message_log: str | None, settings) -> None:
	analysis = {
		"lead_status": result["lead_status"],
		"lead_score": result["lead_score"],
		"intent": result["intent"],
		"ai_summary": result["summary"],
		"needs_human": 1 if result["needs_human"] else 0,
	}

	if message_log:
		frappe.db.set_value("WhatsApp Chat Message", message_log, analysis, update_modified=False)

	# A contact marked Customer by a human should not be demoted by the model.
	if contact.lead_status == "Customer":
		analysis.pop("lead_status")

	# Keep the best score the contact has ever reached.
	if (contact.lead_score or 0) > result["lead_score"]:
		analysis.pop("lead_score")

	contact.db_set(analysis, update_modified=False)

	try:
		lead.capture(contact, result, settings)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "WhatsApp lead capture failed")


def render(template: str | None, context: dict, source: str) -> str:
	if not template:
		return ""
	try:
		return frappe.render_template(template, context)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"WhatsApp reply template failed: {source}")
		return template
