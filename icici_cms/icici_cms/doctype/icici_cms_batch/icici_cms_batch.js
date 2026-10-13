frappe.ui.form.on("ICICI CMS Batch", {
	setup(frm) {
		frm.set_query("bank_account", () => ({
			filters: { is_company_account: 1, company: frm.doc.company },
		}));
	},

	onload(frm) {
		if (frm.is_new() && !frm.doc.client_code) {
			frappe.db.get_single_value("ICICI CMS Settings", "client_code").then((v) => {
				if (v) frm.set_value("client_code", v);
			});
		}
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Get Payment Entries"), () => {
				frm.call({ doc: frm.doc, method: "get_payment_entries", freeze: true }).then(() =>
					frm.refresh()
				);
			});
		}
		if (frm.doc.docstatus === 1 && frm.doc.generated_file) {
			frm.add_custom_button(__("Download File"), () => window.open(frm.doc.generated_file));
		}
		if (frm.doc.docstatus === 1 && frm.doc.generated_excel) {
			frm.add_custom_button(__("Download Excel"), () => window.open(frm.doc.generated_excel));
		}
	},
});
