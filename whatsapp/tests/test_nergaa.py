"""Nergaa WhatsApp: client calls, the flat webhook envelope, signatures, receipts."""

import hashlib
import hmac
import json
from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase as TestCase
except ImportError:
	from frappe.tests.utils import FrappeTestCase as TestCase

from whatsapp.api import link, nergaa, send, webhook
from whatsapp.api.client import NERGAA, get_client, provider_of
from whatsapp.utils.payload import parse_webhook

SECRET = "s" * 40


class Response:
	def __init__(self, status, body):
		self.status_code = status
		self.body = body
		self.text = json.dumps(body)

	def json(self):
		return self.body


class FakeGateway:
	"""Just enough of whatsapp.nergaa.cloud's /api/v1."""

	def __init__(self):
		self.calls = []
		self.state = "logged_out"
		self.instances = {}
		self.hooks = {}

	def __call__(self, method, url, json=None, params=None, headers=None, timeout=None):
		path = url.split("/api/v1/", 1)[1]
		self.calls.append((method, path, json, headers.get("Authorization")))
		if headers.get("Authorization") != "Bearer key-123":
			return Response(401, {"ok": False, "error": "Invalid API key"})
		parts = path.split("/")
		if path == "me":
			return Response(200, {"ok": True, "account": {"name": "Acme"}, "usage": {"messages": 3}})
		if path == "instances" and method == "GET":
			return Response(200, {"ok": True, "instances": list(self.instances.values())})
		if path == "instances" and method == "POST":
			self.instances[json["slug"]] = {"session": json["slug"], "state": "logged_out"}
			return Response(201, {"ok": True, "instance_id": json["slug"], "session": json["slug"]})
		if path == "webhooks" and method == "GET":
			return Response(200, {"ok": True, "webhooks": list(self.hooks.values())})
		if path == "webhooks" and method == "POST":
			hook = {
				"id": f"whk_{len(self.hooks) + 1}",
				"url": json["url"],
				"events": json["events"],
				"enabled": True,
			}
			self.hooks[hook["id"]] = hook
			return Response(201, {"ok": True, "webhook": {**hook, "secret": SECRET}})
		if parts[0] == "webhooks" and method == "DELETE":
			self.hooks.pop(parts[1], None)
			return Response(200, {"ok": True})
		slug = parts[1]
		action = parts[2] if len(parts) > 2 else ""
		if action == "start":
			return Response(200, {"ok": True, "state": "qr", "qr": "data:image/png;base64,AAAA"})
		if action == "pair":
			return Response(200, {"ok": True, "state": "pairing", "pairing_code": "ABCD1234"})
		if action == "qr":
			return Response(
				200, {"ok": True, "session": slug, "state": self.state, "qr": None, "pairing_code": None}
			)
		if action == "" and method == "GET":
			return Response(200, {"ok": True, "session": slug, "state": self.state, "phone": "254700000001"})
		if action == "send":
			if self.state != "connected":
				return Response(409, {"ok": False, "error": "not connected"})
			return Response(
				200, {"ok": True, "message_id": "MSG1", "chat_id": f"{json['to']}@s.whatsapp.net"}
			)
		if action == "logout":
			return Response(200, {"ok": True, "state": "logged_out"})
		return Response(404, {"ok": False, "error": "No such API route."})


def flat(event, **data):
	return {"event": event, "session": "main", "instance_id": "ins_1", **data, "at": "2026-10-14T10:00:00Z"}


class TestPayload(TestCase):
	def test_flat_message(self):
		parsed = parse_webhook(
			flat(
				"message",
				message={
					"message_id": "ABC",
					"chat_id": "254712345678@s.whatsapp.net",
					"from_me": False,
					"is_group": False,
					"wa_id": "254712345678",
					"push_name": "Jane",
					"message_type": "text",
					"text": "Hi there",
					"media": None,
					"timestamp": 1791974600,
				},
			)
		)
		self.assertEqual(parsed["wa_id"], "254712345678")
		self.assertEqual(parsed["message"], "Hi there")
		self.assertEqual(parsed["message_id"], "ABC")
		self.assertEqual(parsed["instance_id"], "main")
		self.assertFalse(parsed["from_me"])
		self.assertIsNotNone(parsed["timestamp"])

	def test_waclient_payload_still_parses(self):
		parsed = parse_webhook(
			{
				"instance_id": "X",
				"data": {
					"event": "messages.upsert",
					"message": {
						"key": {"remoteJid": "254711111111@s.whatsapp.net", "fromMe": False, "id": "K1"},
						"message": {"conversation": "hello"},
					},
				},
			}
		)
		self.assertEqual(parsed["message"], "hello")
		self.assertEqual(parsed["message_id"], "K1")


class NergaaCase(TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.gateway = FakeGateway()
		self.patch = patch("whatsapp.api.nergaa.requests.request", self.gateway)
		self.patch.start()
		settings = frappe.get_doc("WhatsApp Settings")
		settings.enabled = 1
		settings.provider = NERGAA
		settings.nergaa_api_key = "key-123"
		settings.public_base_url = "https://erp.example.com"
		settings.webhook_enabled = 0
		settings.nergaa_webhook_id = None
		settings.log_incoming = 1
		settings.log_outgoing = 1
		settings.auto_reply_enabled = 0
		settings.set("accounts", [])
		settings.save()
		frappe.clear_document_cache("WhatsApp Settings", "WhatsApp Settings")

	def tearDown(self):
		self.patch.stop()
		frappe.db.rollback()
		frappe.clear_document_cache("WhatsApp Settings", "WhatsApp Settings")

	def settings(self):
		return frappe.get_doc("WhatsApp Settings")


class TestLinkAndSend(NergaaCase):
	def test_link_by_qr_then_connected(self):
		r = link.start("Sales")
		self.assertEqual(r["qr"], "data:image/png;base64,AAAA")
		self.assertFalse(r["connected"])
		row = self.settings().accounts[0]
		self.assertEqual(row.account_name, "Sales")
		self.assertTrue(row.instance_id.endswith("-sales"))
		self.assertTrue(row.is_default)

		self.gateway.state = "connected"
		r = link.status("Sales")
		self.assertTrue(r["connected"])
		settings = self.settings()
		self.assertEqual(settings.accounts[0].phone_number, "254700000001")
		self.assertEqual(settings.accounts[0].connection_state, "connected")
		# The site's webhook is registered once the phone is linked, and its secret kept.
		self.assertTrue(settings.nergaa_webhook_id)
		self.assertEqual(settings.get_password("nergaa_webhook_secret"), SECRET)
		self.assertEqual(
			next(iter(self.gateway.hooks.values()))["url"],
			"https://erp.example.com/api/method/whatsapp.api.webhook.receive",
		)

	def test_link_by_pairing_code(self):
		r = link.start("Main", phone="+254 712 345 678")
		self.assertEqual(r["pairing_code"], "ABCD1234")
		pair = next(c for c in self.gateway.calls if c[1].endswith("/pair"))
		self.assertEqual(pair[2], {"phone": "254712345678"})

	def test_reregistering_replaces_the_webhook(self):
		settings = self.settings()
		settings.register_webhook()
		self.settings().register_webhook()
		self.assertEqual(len(self.gateway.hooks), 1)

	def test_send_text_and_document(self):
		link.start("Main")
		self.gateway.state = "connected"
		link.status("Main")
		from whatsapp.services import messaging

		out = messaging.send_message(to="254712345678", message="Hello")
		self.assertEqual(
			frappe.db.get_value("WhatsApp Chat Message", out["whatsapp_message"], "message_id"), "MSG1"
		)
		sent = [c for c in self.gateway.calls if c[1].endswith("/send")][-1]
		self.assertEqual(sent[2], {"to": "254712345678", "text": "Hello"})

		out = messaging.send_message(
			to="254712345678",
			message="Invoice",
			media_filename="INV-1.pdf",
			media_base64="JVBERi0=",
			media_mimetype="application/pdf",
		)
		media = [c for c in self.gateway.calls if c[1].endswith("/send")][-1][2]["media"]
		self.assertEqual(media["kind"], "document")
		self.assertEqual(media["base64"], "JVBERi0=")
		self.assertEqual(media["filename"], "INV-1.pdf")

	def test_not_connected_is_explained(self):
		link.start("Main")
		client = get_client(settings=self.settings())
		with self.assertRaises(nergaa.NergaaError) as ctx:
			client.send_text("254712345678", "x")
		self.assertIn("not connected", str(ctx.exception))

	def test_key_from_site_config(self):
		settings = self.settings()
		settings.nergaa_api_key = None
		settings.provider = None
		settings.save()
		with patch.dict(frappe.conf, {"nergaa_whatsapp_api_key": "key-123"}):
			self.assertEqual(provider_of(self.settings()), NERGAA)
			self.assertEqual(nergaa.NergaaAPI(self.settings()).me()["account"]["name"], "Acme")
		self.assertNotEqual(provider_of(self.settings()), NERGAA)

	def test_document_defaults(self):
		todo = frappe.get_doc({"doctype": "ToDo", "description": "Call back"}).insert()
		d = send.document_defaults("ToDo", todo.name)
		self.assertTrue(d["can_attach"])
		self.assertIn(todo.name, d["message"])


class TestWebhook(NergaaCase):
	def setUp(self):
		super().setUp()
		link.start("Main")
		self.gateway.state = "connected"
		link.status("Main")
		self.slug = self.settings().accounts[0].instance_id

	def deliver(self, payload, secret=SECRET, sign=True):
		body = json.dumps(payload).encode()
		headers = {"Content-Type": "application/json"}
		if sign:
			headers["X-Webhook-Signature"] = (
				"sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
			)
		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		frappe.local.request = Request(
			EnvironBuilder(method="POST", data=body, headers=headers).get_environ()
		)
		frappe.local.response = frappe._dict()
		frappe.local.form_dict = frappe._dict()
		try:
			return webhook.receive()
		finally:
			del frappe.local.request

	def message(self, mid="IN1", text="Hello"):
		return {
			"event": "message",
			"session": self.slug,
			"instance_id": "ins_1",
			"message": {
				"message_id": mid,
				"chat_id": "254733333333@s.whatsapp.net",
				"from_me": False,
				"is_group": False,
				"wa_id": "254733333333",
				"push_name": "Bob",
				"message_type": "text",
				"text": text,
				"media": None,
				"timestamp": 1791974600,
			},
			"at": "2026-10-14T10:00:00Z",
		}

	def test_signed_message_is_logged(self):
		r = self.deliver(self.message())
		self.assertEqual(r["status"], "ok")
		row = frappe.get_doc("WhatsApp Chat Message", r["whatsapp_message"])
		self.assertEqual((row.message, row.from_number, row.account), ("Hello", "254733333333", "Main"))

	def test_bad_or_missing_signature_is_refused(self):
		self.assertEqual(self.deliver(self.message("X1"), secret="wrong")["status"], "unauthorised")
		self.assertEqual(self.deliver(self.message("X2"), sign=False)["status"], "unauthorised")
		self.assertFalse(frappe.db.exists("WhatsApp Chat Message", {"message_id": ["in", ["X1", "X2"]]}))

	def test_receipts_move_status_forward_only(self):
		from whatsapp.services import messaging

		out = messaging.send_message(to="254712345678", message="Hi")
		name = out["whatsapp_message"]

		def receipt(status):
			return {
				"event": "receipt",
				"session": self.slug,
				"instance_id": "i",
				"message_id": "MSG1",
				"status": status,
				"at": "x",
			}

		self.deliver(receipt(4))
		self.assertEqual(frappe.db.get_value("WhatsApp Chat Message", name, "status"), "Read")
		self.deliver(receipt(3))
		self.assertEqual(frappe.db.get_value("WhatsApp Chat Message", name, "status"), "Read")

	def test_disconnect_is_recorded(self):
		self.deliver({"event": "logged_out", "session": self.slug, "instance_id": "i", "at": "x"})
		self.assertEqual(self.settings().accounts[0].connection_state, "logged_out")


class TestUnsignedRefused(NergaaCase):
	def test_no_secret_yet_means_nothing_unsigned_gets_in(self):
		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		settings = self.settings()
		settings.webhook_enabled = 1
		settings.save()
		frappe.clear_document_cache("WhatsApp Settings", "WhatsApp Settings")
		body = json.dumps(
			flat(
				"message",
				message={
					"message_id": "FAKE",
					"chat_id": "254700000009@s.whatsapp.net",
					"text": "hi",
					"timestamp": 1,
				},
			)
		).encode()
		frappe.local.request = Request(EnvironBuilder(method="POST", data=body).get_environ())
		frappe.local.response = frappe._dict()
		frappe.local.form_dict = frappe._dict()
		try:
			self.assertEqual(webhook.receive()["status"], "unauthorised")
		finally:
			del frappe.local.request
		self.assertFalse(frappe.db.exists("WhatsApp Chat Message", {"message_id": "FAKE"}))


class TestDocumentNumber(TestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_number_falls_back_to_the_contact_person_when_the_document_has_none(self):
		contact = frappe.get_doc({"doctype": "Contact", "first_name": "Jane", "mobile_no": "0712345678"}).insert()
		doc = frappe._dict(contact_person=contact.name)
		self.assertEqual(send.document_number(doc), "0712345678")

	def test_the_documents_own_number_wins_over_its_contact_person(self):
		contact = frappe.get_doc({"doctype": "Contact", "first_name": "Jane", "mobile_no": "0712345678"}).insert()
		doc = frappe._dict(contact_mobile="0799999999", contact_person=contact.name)
		self.assertEqual(send.document_number(doc), "0799999999")
