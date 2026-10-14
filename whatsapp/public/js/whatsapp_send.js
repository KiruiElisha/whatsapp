// "Send on WhatsApp" on every submittable or printable document form.
// Hidden until WhatsApp Settings is enabled (see whatsapp.boot.boot_session).
(() => {
	const SKIP = new Set(["WhatsApp Settings", "WhatsApp Chat Message", "WhatsApp Account"]);

	function send(frm) {
		frappe
			.call({ method: "whatsapp.api.send.document_defaults", args: { doctype: frm.doctype, name: frm.docname } })
			.then((r) => {
				if (r.exc) return;
				const p = r.message;
				const d = new frappe.ui.Dialog({
					title: __("Send on WhatsApp"),
					fields: [
						{ fieldname: "to", fieldtype: "Data", label: __("To"), reqd: 1, default: p.to,
						  description: __("Mobile number; the default country code is added to local numbers.") },
						{ fieldname: "message", fieldtype: "Small Text", label: __("Message"), default: p.message },
						{ fieldname: "attach_pdf", fieldtype: "Check", label: __("Attach PDF"), default: p.can_attach ? 1 : 0,
						  read_only: !p.can_attach,
						  description: p.can_attach ? "" : __("Attaching files needs the Nergaa WhatsApp provider.") },
						{ fieldname: "print_format", fieldtype: "Link", options: "Print Format", label: __("Print Format"),
						  default: p.print_format === "Standard" ? "" : p.print_format,
						  depends_on: "attach_pdf", get_query: () => ({ filters: { doc_type: frm.doctype } }) },
					],
					primary_action_label: __("Send"),
					primary_action(v) {
						frappe
							.call({
								method: "whatsapp.api.send.send_document",
								args: { doctype: frm.doctype, name: frm.docname, ...v },
								freeze: true,
								freeze_message: __("Sending on WhatsApp..."),
							})
							.then((res) => {
								if (res.exc) return;
								d.hide();
								frappe.show_alert({ message: __("Sent to {0}", [res.message.to]), indicator: "green" });
								frm.reload_doc();
							});
					},
				});
				d.show();
			});
	}

	$(document).on("form-refresh", (e, frm) => {
		if (!frappe.boot.whatsapp_enabled || !frm || frm.is_new() || SKIP.has(frm.doctype)) return;
		if (frm.meta.issingle || frm.meta.istable) return;
		if (!frappe.model.can_print(frm.doctype, frm) && !frm.meta.is_submittable) return;
		frm.page.add_menu_item(__("Send on WhatsApp"), () => send(frm), true);
	});
})();
