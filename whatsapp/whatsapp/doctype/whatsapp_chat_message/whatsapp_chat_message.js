frappe.ui.form.on("WhatsApp Chat Message", {
	refresh(frm) {
		// Only outgoing replies can be wrong in a way the AI should learn from.
		if (frm.is_new() || frm.doc.direction !== "Outgoing") return;

		frm.add_custom_button(__("Record AI Correction"), () => {
			frappe.prompt(
				[
					{
						fieldname: "title",
						label: __("Title"),
						fieldtype: "Data",
						reqd: 1,
						description: __("Short name for the lesson."),
					},
					{
						fieldname: "correct_behaviour",
						label: __("What It Should Have Said Instead"),
						fieldtype: "Small Text",
						reqd: 1,
					},
				],
				(values) => {
					frappe.call({
						method: "whatsapp.whatsapp.doctype.whatsapp_ai_correction.whatsapp_ai_correction.create_from_message",
						args: {
							message: frm.doc.name,
							title: values.title,
							correct_behaviour: values.correct_behaviour,
						},
						freeze: true,
					}).then((r) => {
						if (r.exc) return;
						frappe.set_route("Form", "WhatsApp AI Correction", r.message);
						frappe.show_alert({
							message: __("Correction recorded. Refine when it applies, then save."),
							indicator: "green",
						});
					});
				},
				__("Teach the AI"),
				__("Record")
			);
		});
	},
});
