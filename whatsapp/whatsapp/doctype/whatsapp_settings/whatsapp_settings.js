// Linking a phone to Nergaa WhatsApp: show the QR (or pairing code) the gateway
// hands back, and poll until the phone connects.
frappe.provide("whatsapp");
whatsapp.link_phone = function (frm, preset) {
	const accounts = (frm.doc.accounts || []).map((r) => r.account_name).filter(Boolean);
	const d = new frappe.ui.Dialog({
		title: __("Link a WhatsApp Phone"),
		fields: [
			{
				fieldname: "account",
				fieldtype: "Data",
				label: __("Account Name"),
				reqd: 1,
				default: preset || accounts[0] || "Main",
				description: __("A label for this number, e.g. Sales or Support. A new name adds a number."),
			},
			{
				fieldname: "method",
				fieldtype: "Select",
				label: __("How to link"),
				options: [
					{ value: "qr", label: __("Scan a QR code") },
					{ value: "code", label: __("Type a pairing code on the phone") },
				],
				default: "qr",
			},
			{
				fieldname: "phone",
				fieldtype: "Data",
				label: __("Phone Number"),
				depends_on: "eval:doc.method=='code'",
				description: __("International format, e.g. 254712345678"),
			},
			{ fieldname: "out", fieldtype: "HTML" },
		],
		primary_action_label: __("Start"),
		primary_action(values) {
			if (values.method === "code" && !values.phone) {
				frappe.msgprint(__("Enter the phone number to get a pairing code."));
				return;
			}
			show(`<p class="text-muted">${__("Contacting Nergaa WhatsApp...")}</p>`);
			frappe
				.call({
					method: "whatsapp.api.link.start",
					args: { account: values.account, phone: values.method === "code" ? values.phone : null },
				})
				.then((r) => {
					if (!r.exc) {
						render(r.message);
						poll(values.account);
					}
				});
		},
		on_hide() {
			clearInterval(d.timer);
			frm.reload_doc();
		},
	});

	const esc = (v) => frappe.utils.escape_html(String(v || ""));
	const show = (html) => d.fields_dict.out.$wrapper.html(html);

	function render(s) {
		if (s.connected) {
			clearInterval(d.timer);
			show(`<div class="text-center" style="padding: 20px">
				<h4 class="text-success">${__("Linked")}</h4>
				<p>${__("{0} is connected as {1}.", [esc(s.account), esc(s.phone || s.name || "")])}</p>
				<p class="text-muted small">${__("Messages to this number now reach ERPNext.")}</p></div>`);
			d.set_primary_action(__("Done"), () => d.hide());
			return;
		}
		if (s.pairing_code) {
			show(`<div class="text-center" style="padding: 12px">
				<p>${__("On the phone: WhatsApp → Linked devices → Link a device → Link with phone number instead, then type:")}</p>
				<div style="font-size: 2em; letter-spacing: .25em; font-family: monospace; margin: 12px 0">${esc(s.pairing_code)}</div>
				<p class="text-muted small">${__("Waiting for the phone...")}</p></div>`);
			return;
		}
		// only a real image data URL goes into the page (never markup from the gateway)
		if (s.qr && /^data:image\/(png|jpeg|gif|svg\+xml);base64,[A-Za-z0-9+/=]+$/.test(String(s.qr))) {
			show(`<div class="text-center">
				<p>${__("On the phone: WhatsApp → Linked devices → Link a device, and scan:")}</p>
				<img src="${s.qr}" style="width: 280px; max-width: 100%; image-rendering: pixelated" alt="QR">
				<p class="text-muted small">${__("The code refreshes by itself. Waiting for the phone...")}</p></div>`);
			return;
		}
		show(`<p class="text-muted">${__("State: {0}. Waiting for a code...", [esc(s.state)])}</p>
			${s.error ? `<p class="text-danger small">${esc(s.error)}</p>` : ""}`);
	}

	function poll(account) {
		clearInterval(d.timer);
		let rounds = 0;
		d.timer = setInterval(() => {
			// About four minutes; WhatsApp stops rotating codes well before then.
			if (++rounds > 80) {
				clearInterval(d.timer);
				show(`<p class="text-warning">${__("Timed out. Press Start to try again.")}</p>`);
				return;
			}
			frappe
				.call({ method: "whatsapp.api.link.status", args: { account }, type: "GET" })
				.then((r) => !r.exc && render(r.message));
		}, 3000);
	}

	d.show();
};

frappe.ui.form.on("WhatsApp Settings", {
	refresh(frm) {
		if (frm.doc.provider !== "WaClient") {
			frm.add_custom_button(__("Link Phone"), () => whatsapp.link_phone(frm));
			frm.add_custom_button(
				__("Unlink Phone"),
				() => {
					const names = (frm.doc.accounts || []).map((r) => r.account_name);
					frappe.prompt(
						{ fieldname: "account", fieldtype: "Select", label: __("Account"), options: names, reqd: 1 },
						(v) =>
							frappe.confirm(__("Log {0} out of WhatsApp on the gateway?", [v.account]), () =>
								frappe
									.call({ method: "whatsapp.api.link.unlink", args: { account: v.account }, freeze: true })
									.then((r) => !r.exc && frm.reload_doc())
							),
						__("Unlink Phone")
					);
				},
				__("Phones")
			);
			if (!(frm.doc.accounts || []).some((r) => r.instance_id)) {
				frm.dashboard.set_headline_alert(
					__("No phone linked yet. Use Link Phone to connect a WhatsApp number by QR code or pairing code."),
					"blue"
				);
			}
		}

		frm.add_custom_button(__("Test Connection"), () => {
			frm.call({
				doc: frm.doc,
				method: "test_connection",
				freeze: true,
				freeze_message: __("Contacting WhatsApp API..."),
			}).then((r) => {
				if (r.exc) return;
				const d = r.message;
				const esc = (v) =>
					v === null || v === undefined || v === ""
						? "&mdash;"
						: frappe.utils.escape_html(String(v));
				const row = (k, v) => `<tr><td><b>${k}</b></td><td>${v}</td></tr>`;

				frappe.msgprint({
					title: d.connected ? __("Connected") : __("Not Connected"),
					indicator: d.connected ? "green" : "red",
					message: `
						<table class="table table-bordered">
							${row(__("Account"), esc(d.account))}
							${row(__("Connection State"), esc(d.connection_state))}
							${row(__("Linked Number"), esc(d.phone))}
							${row(__("Webhook URL"), esc(d.webhook_url))}
							${row(__("Webhook Enabled"), d.webhook_enabled ? __("Yes") : __("No"))}
						</table>
						${
							d.relogin_required
								? `<p class="text-danger">${__(
										"This number needs to be linked again. Use Link Phone (Nergaa WhatsApp) or scan the QR code in your WaClient dashboard."
								  )}</p>`
								: ""
						}`,
				});
			});
		});

		frm.add_custom_button(__("Register Webhook"), () => {
			frm.call({
				doc: frm.doc,
				method: "register_webhook",
				freeze: true,
				freeze_message: __("Registering webhook..."),
			}).then((r) => {
				if (r.exc) return;
				const d = r.message;
				frm.reload_doc();

				if (d.verified) {
					frappe.msgprint({
						title: __("Webhook Registered"),
						indicator: "green",
						message: __("WhatsApp will now deliver messages to {0}", [
							frappe.utils.escape_html(d.webhook_url),
						]),
					});
					return;
				}

				frappe.msgprint({
					title: __("Webhook Not Active"),
					indicator: "red",
					message: __(
						"The provider accepted the request but the webhook is not live. It reports URL {0} and enabled {1}.",
						[
							frappe.utils.escape_html(d.registered_url || "—"),
							d.registered_enabled ? __("Yes") : __("No"),
						]
					),
				});
			});
		});

		if (frm.doc.ai_enabled) {
			frm.add_custom_button(__("Test AI Reply"), () => {
				frappe.prompt(
					{
						fieldname: "message",
						label: __("Message to test"),
						fieldtype: "Small Text",
						reqd: 1,
						default: __("Hi, how much is delivery to Nairobi?"),
					},
					(values) => {
						frm.call({
							doc: frm.doc,
							method: "test_ai",
							args: { message: values.message },
							freeze: true,
							freeze_message: __("Asking the AI..."),
						}).then((r) => {
							if (r.exc) return;
							const d = r.message;
							frappe.msgprint({
								title: __("AI Response"),
								indicator: d.is_prospect ? "green" : "blue",
								message: `
									<p><b>${__("Reply")}</b></p>
									<blockquote>${frappe.utils.escape_html(d.reply || "")}</blockquote>
									<table class="table table-bordered">
										<tr><td><b>${__("Lead Status")}</b></td><td>${frappe.utils.escape_html(d.lead_status)}</td></tr>
										<tr><td><b>${__("Lead Score")}</b></td><td>${d.lead_score}</td></tr>
										<tr><td><b>${__("Prospect")}</b></td><td>${d.is_prospect ? __("Yes") : __("No")}</td></tr>
										<tr><td><b>${__("Intent")}</b></td><td>${frappe.utils.escape_html(d.intent || "")}</td></tr>
										<tr><td><b>${__("Summary")}</b></td><td>${frappe.utils.escape_html(d.summary || "")}</td></tr>
										<tr><td><b>${__("Needs Human")}</b></td><td>${d.needs_human ? __("Yes") : __("No")}</td></tr>
									</table>`,
							});
						});
					},
					__("Test AI Reply"),
					__("Run")
				);
			});
		}

		frm.add_custom_button(__("Auto Reply Rules"), () => {
			frappe.set_route("List", "WhatsApp Auto Reply");
		});

		frm.add_custom_button(__("Message Log"), () => {
			frappe.set_route("List", "WhatsApp Chat Message");
		});

		if (!frm.doc.enabled) {
			frm.dashboard.set_headline_alert(
				__("WhatsApp integration is disabled. No messages will be sent or answered."),
				"orange"
			);
		}
	},
});
