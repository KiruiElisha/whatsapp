"""Thin HTTP client for the WaClient WhatsApp Web API."""

import json

import requests

import frappe
from frappe import _


class WaClientError(frappe.ValidationError):
	pass


class WaClient:
	"""Talks to WaClient on behalf of one configured account."""

	def __init__(self, account_name: str | None = None, settings=None):
		self.settings = settings or frappe.get_cached_doc("WhatsApp Settings")
		self.account = self.settings.get_account(account_name)
		# WaClient splits its API across two hosts: sending lives under
		# waclient.com/api, while instance management lives on api.waclient.com.
		self.base_url = (self.settings.api_base_url or "https://waclient.com/api").rstrip("/")
		self.instance_url = (
			self.settings.instance_api_base_url or "https://api.waclient.com"
		).rstrip("/")
		self.timeout = self.settings.request_timeout or 30

	@property
	def account_name(self) -> str:
		return self.account["account_name"]

	def credentials(self) -> dict:
		return {
			"instance_id": self.account["instance_id"],
			"access_token": self.account["access_token"],
		}

	def request(
		self,
		path: str,
		payload: dict | None = None,
		method: str = "POST",
		base: str | None = None,
	) -> dict:
		url = f"{(base or self.base_url).rstrip('/')}/{path.lstrip('/')}"
		data = {**(payload or {}), **self.credentials()}

		headers = {
			"Accept": "application/json",
			"Content-Type": "application/json",
			"User-Agent": "Frappe-WhatsApp/1.0",
		}

		try:
			if method.upper() == "GET":
				response = requests.get(url, params=data, headers=headers, timeout=self.timeout)
			else:
				response = requests.post(url, json=data, headers=headers, timeout=self.timeout)
		except requests.RequestException as exc:
			raise WaClientError(_("Could not reach the WhatsApp API: {0}").format(exc)) from exc

		return self.parse(response)

	def parse(self, response: requests.Response) -> dict:
		try:
			body = response.json()
		except ValueError:
			snippet = (response.text or "")[:500]
			raise WaClientError(
				_("WhatsApp API returned a non-JSON response ({0}): {1}").format(
					response.status_code, snippet
				)
			)

		if response.status_code >= 400:
			raise WaClientError(
				_("WhatsApp API error {0}: {1}").format(
					response.status_code, body.get("message") or json.dumps(body)[:500]
				)
			)

		# WaClient answers 200 with {"status": "error", ...} on business failures.
		status = str(body.get("status", "")).lower()
		if status in ("error", "false", "fail", "failed"):
			raise WaClientError(
				_("WhatsApp API rejected the request: {0}").format(
					body.get("message") or json.dumps(body)[:500]
				)
			)

		return body

	def send_text(self, number: str, message: str) -> dict:
		return self.request(
			"send",
			{"number": number, "type": "text", "message": message},
		)

	def send_media(
		self, number: str, message: str, media_url: str, filename: str | None = None
	) -> dict:
		payload = {
			"number": number,
			"type": "media",
			"message": message or "",
			"media_url": media_url,
		}
		if filename:
			payload["filename"] = filename
		return self.request("send", payload)

	def set_webhook(self, webhook_url: str, enable: bool = True) -> dict:
		return self.request(
			"set_webhook",
			{"webhook_url": webhook_url, "enable": bool(enable)},
			method="POST",
			base=self.instance_url,
		)

	def get_status(self, live: bool = True) -> dict:
		"""Connection state of the instance. `live` forces a real socket check."""
		return self.request(
			"instance_status",
			{"live": 1 if live else 0},
			method="GET",
			base=self.instance_url,
		)

	def get_instance_info(self) -> dict:
		"""Instance details, including the linked number and current webhook."""
		return self.request("instance_info", method="GET", base=self.instance_url)


def extract_message_id(response: dict) -> str | None:
	"""Pull the provider message key out of a send response.

	WaClient has shipped the payload under both `data` and `message`, so check both.
	"""
	for key in ("data", "message"):
		block = response.get(key)
		if isinstance(block, dict):
			message_id = (block.get("key") or {}).get("id")
			if message_id:
				return message_id
	return None
