frappe.ui.form.on("WhatsApp Settings", {
	refresh(frm) {
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
										"This instance needs to be linked again by scanning the QR code in your WaClient dashboard."
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
						message: __("WaClient will now deliver messages to {0}", [
							frappe.utils.escape_html(d.webhook_url),
						]),
					});
					return;
				}

				frappe.msgprint({
					title: __("Webhook Not Active"),
					indicator: "red",
					message: __(
						"WaClient accepted the request but the webhook is not live. It reports URL {0} and enabled {1}.",
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
