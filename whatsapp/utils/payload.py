"""Flatten WaClient webhook payloads into one predictable shape.

WaClient wraps Baileys output and the nesting has changed between versions, so
every lookup here falls back through the shapes seen in the wild rather than
assuming one layout.
"""

from datetime import datetime

from whatsapp.utils.phone import is_group_id, strip_suffix

# Message containers that carry a caption plus a downloadable file.
MEDIA_KEYS = (
	"imageMessage",
	"videoMessage",
	"documentMessage",
	"audioMessage",
	"stickerMessage",
	"documentWithCaptionMessage",
)

# Containers whose text lives under a differently named key.
TEXT_KEYS = (
	("conversation", None),
	("extendedTextMessage", "text"),
	("buttonsResponseMessage", "selectedDisplayText"),
	("templateButtonReplyMessage", "selectedDisplayText"),
	("listResponseMessage", "title"),
	("ephemeralMessage", None),
)


def as_dict(value) -> dict:
	return value if isinstance(value, dict) else {}


def first_entry(value):
	"""Webhooks send either one message object or a list of them."""
	if isinstance(value, list):
		return value[0] if value else {}
	return value


def is_flat(payload: dict) -> bool:
	"""Nergaa WhatsApp's envelope: {event, session, instance_id, ..., at}."""
	payload = as_dict(payload)
	return bool(payload.get("event")) and "session" in payload and "at" in payload


def parse_flat(payload: dict) -> dict | None:
	"""A Nergaa WhatsApp `message` event, already flat, in our shape."""
	message = as_dict(payload.get("message"))
	if not message:
		return None

	chat_id = message.get("chat_id") or ""
	media = as_dict(message.get("media"))
	message_type = message.get("message_type") or media.get("type") or "text"
	text = message.get("text") or ""
	if not text and message_type == "location" and media:
		text = f"{media.get('latitude')}, {media.get('longitude')}"

	return {
		"event": payload.get("event"),
		"instance_id": payload.get("session") or payload.get("instance_id"),
		"chat_id": strip_suffix(chat_id),
		# Like the WaClient path: a group's messages belong to the group contact.
		"wa_id": strip_suffix(chat_id) if chat_id and not chat_id.endswith("@lid") else str(message.get("wa_id") or ""),
		"is_group": bool(message.get("is_group")) or is_group_id(chat_id),
		"from_me": bool(message.get("from_me")),
		"message_id": message.get("message_id"),
		"push_name": message.get("push_name"),
		"message": text,
		"message_type": message_type,
		"media_url": media.get("url"),
		"media_filename": media.get("filename"),
		"timestamp": extract_timestamp({"messageTimestamp": message.get("timestamp")}, {}),
	}


def parse_webhook(payload: dict) -> dict | None:
	"""Return a normalised message dict, or None if the payload has no message."""
	payload = as_dict(payload)
	if is_flat(payload):
		return parse_flat(payload)
	body = as_dict(payload.get("data")) or payload

	event = body.get("event") or payload.get("event") or ""
	instance_id = payload.get("instance_id") or body.get("instance_id")

	block = as_dict(body.get("message")) or body
	envelope, content = split_envelope(block)

	key = as_dict(envelope.get("key"))
	chat_id = (
		key.get("remoteJid")
		or block.get("from_contact")
		or block.get("from")
		or envelope.get("remoteJid")
		or ""
	)

	if not chat_id and not content:
		return None

	text, message_type, media = extract_content(content)

	return {
		"event": event,
		"instance_id": instance_id,
		"chat_id": strip_suffix(chat_id),
		"wa_id": strip_suffix(chat_id),
		"is_group": is_group_id(chat_id),
		"from_me": bool(key.get("fromMe")),
		"message_id": key.get("id") or envelope.get("id") or block.get("id"),
		"push_name": envelope.get("pushName") or block.get("push_name") or block.get("pushName"),
		"message": text,
		"message_type": message_type,
		"media_url": media.get("url"),
		"media_filename": media.get("filename"),
		"timestamp": extract_timestamp(envelope, content),
	}


def split_envelope(block: dict) -> tuple[dict, dict]:
	"""Separate the message metadata from the message content.

	Older WaClient builds put the content straight under `body_message.messages`,
	newer ones keep the Baileys envelope with `key` and `message` intact.
	"""
	candidate = block
	body_message = as_dict(block.get("body_message"))
	if body_message:
		candidate = first_entry(body_message.get("messages")) or body_message

	candidate = as_dict(first_entry(candidate))

	if "message" in candidate or "key" in candidate:
		envelope = candidate
		content = as_dict(candidate.get("message"))
	else:
		envelope = block
		content = candidate

	# Disappearing messages nest the real content one level deeper.
	ephemeral = as_dict(content.get("ephemeralMessage"))
	if ephemeral:
		content = as_dict(ephemeral.get("message")) or content

	viewonce = as_dict(content.get("viewOnceMessage")) or as_dict(
		content.get("viewOnceMessageV2")
	)
	if viewonce:
		content = as_dict(viewonce.get("message")) or content

	return envelope, content


def extract_content(content: dict) -> tuple[str, str, dict]:
	"""Return the readable text, a message type label, and any media details."""
	if not content:
		return "", "text", {}

	for key in MEDIA_KEYS:
		media = as_dict(content.get(key))
		if media:
			return (
				media.get("caption") or "",
				key.replace("Message", "").lower(),
				{
					"url": media.get("url") or media.get("directPath"),
					"filename": media.get("fileName") or media.get("filename"),
				},
			)

	if as_dict(content.get("locationMessage")):
		location = as_dict(content.get("locationMessage"))
		lat, lng = location.get("degreesLatitude"), location.get("degreesLongitude")
		return f"{lat}, {lng}", "location", {}

	if as_dict(content.get("contactMessage")):
		return as_dict(content["contactMessage"]).get("displayName") or "", "contact", {}

	for key, subkey in TEXT_KEYS:
		value = content.get(key)
		if value is None:
			continue
		if subkey is None and isinstance(value, str):
			return value, "text", {}
		nested = as_dict(value)
		if nested.get(subkey):
			return nested[subkey], "text", {}

	# Unknown container: report its name so the log still says something useful.
	label = next((k for k in content if k != "messageContextInfo"), "unknown")
	return "", label.replace("Message", "").lower(), {}


def extract_timestamp(envelope: dict, content: dict) -> datetime | None:
	raw = (
		envelope.get("messageTimestamp")
		or envelope.get("t")
		or as_dict(as_dict(content.get("messageContextInfo")).get("deviceListMetadata")).get(
			"senderTimestamp"
		)
	)

	if isinstance(raw, dict):
		raw = raw.get("low") or raw.get("value")

	try:
		seconds = int(raw)
	except (TypeError, ValueError):
		return None

	if seconds <= 0:
		return None

	# Some builds send milliseconds.
	if seconds > 10_000_000_000:
		seconds //= 1000

	try:
		return datetime.fromtimestamp(seconds)
	except (OverflowError, OSError, ValueError):
		return None
