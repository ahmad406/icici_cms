import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from icici_cms.icici_cms.utils import (
	build_annexure_line,
	build_payment_line,
	fmt_amount,
	fmt_date,
	get_payment_code,
)


class ICICICMSBatch(Document):
	def validate(self):
		if not self.client_code:
			self.client_code = frappe.db.get_single_value("ICICI CMS Settings", "client_code")
		if not self.client_code:
			frappe.throw(_("Client Code is required. Set it in ICICI CMS Settings."))
		if not re.fullmatch(r"[A-Za-z0-9]+", self.client_code):
			frappe.throw(_("Client Code can only have letters and digits."))
		if not self.payments:
			frappe.throw(_("Add at least one Payment Entry."))

		self.refresh_payments()
		self.total_amount = sum(flt(d.paid_amount) for d in self.payments)

	def refresh_payments(self):
		seen = set()
		sent = self.get_sent_payments(exclude=self.name)
		for row in self.payments:
			if row.payment_entry in seen:
				frappe.throw(_("Row {0}: {1} is added twice.").format(row.idx, row.payment_entry))
			seen.add(row.payment_entry)

			pe = frappe.db.get_value(
				"Payment Entry",
				row.payment_entry,
				[
					"docstatus", "payment_type", "party_type", "company", "bank_account",
					"posting_date", "party", "party_name", "mode_of_payment", "paid_amount",
				],
				as_dict=True,
			)
			if not pe or pe.docstatus != 1:
				frappe.throw(_("Row {0}: {1} is not submitted.").format(row.idx, row.payment_entry))
			if pe.payment_type != "Pay" or pe.party_type != "Supplier":
				frappe.throw(_("Row {0}: {1} is not a supplier payment.").format(row.idx, row.payment_entry))
			if pe.company != self.company or pe.bank_account != self.bank_account:
				frappe.throw(
					_("Row {0}: {1} does not belong to this company bank account.").format(
						row.idx, row.payment_entry
					)
				)
			if row.payment_entry in sent:
				frappe.throw(
					_("Row {0}: {1} is already in batch {2}.").format(
						row.idx, row.payment_entry, sent[row.payment_entry]
					)
				)

			row.posting_date = pe.posting_date
			row.party = pe.party
			row.party_name = pe.party_name
			row.mode_of_payment = pe.mode_of_payment
			row.paid_amount = pe.paid_amount

	@staticmethod
	def get_sent_payments(exclude=None):
		rows = frappe.db.sql(
			"""select bp.payment_entry, b.name
			from `tabICICI CMS Batch Payment` bp
			join `tabICICI CMS Batch` b on b.name = bp.parent
			where b.docstatus = 1 and b.name != %s""",
			exclude or "",
		)
		return dict(rows)

	@frappe.whitelist()
	def get_payment_entries(self):
		if not (self.company and self.bank_account):
			frappe.throw(_("Select Company and Company Bank Account first."))

		filters = {
			"docstatus": 1,
			"payment_type": "Pay",
			"party_type": "Supplier",
			"company": self.company,
			"bank_account": self.bank_account,
		}
		if self.from_date and self.to_date:
			filters["posting_date"] = ["between", [self.from_date, self.to_date]]
		elif self.from_date:
			filters["posting_date"] = [">=", self.from_date]
		elif self.to_date:
			filters["posting_date"] = ["<=", self.to_date]

		sent = list(self.get_sent_payments(exclude=self.name))
		if sent:
			filters["name"] = ["not in", sent]

		entries = frappe.get_all(
			"Payment Entry",
			filters=filters,
			fields=["name", "posting_date", "party", "party_name", "mode_of_payment", "paid_amount"],
			order_by="posting_date, name",
		)
		self.set("payments", [])
		for e in entries:
			self.append(
				"payments",
				{
					"payment_entry": e.name,
					"posting_date": e.posting_date,
					"party": e.party,
					"party_name": e.party_name,
					"mode_of_payment": e.mode_of_payment,
					"paid_amount": e.paid_amount,
				},
			)
		self.total_amount = sum(flt(d.paid_amount) for d in self.payments)
		if not entries:
			frappe.msgprint(_("No pending Payment Entries found."))

	def before_submit(self):
		self.set_sno_and_file_name()
		self._file_content = self.build_file_content()

	def on_submit(self):
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": self.file_name,
				"attached_to_doctype": self.doctype,
				"attached_to_name": self.name,
				"content": self._file_content,
				"is_private": 1,
			}
		).insert(ignore_permissions=True)
		self.db_set("generated_file", file.file_url)

	def set_sno_and_file_name(self):
		last = frappe.db.sql(
			"""select max(cast(sno as unsigned)) from `tabICICI CMS Batch`
			where docstatus in (1, 2) and client_code = %s and file_date = %s and name != %s
			for update""",
			(self.client_code, self.file_date, self.name),
		)[0][0]
		self.sno = f"{int(last or 0) + 1:04d}"
		day = getdate(self.file_date).strftime("%d%m%Y")
		self.file_name = f"{self.client_code}_{self.client_code}UPLD_{day}_{self.sno}.txt"

	def build_file_content(self):
		from erpnext.accounts.utils import get_fiscal_year

		lines, errors = [], []
		bank_cache = {}

		def bank(name):
			if name and name not in bank_cache:
				bank_cache[name] = frappe.db.get_value(
					"Bank Account", name, ["bank_account_no", "branch_code", "bank"], as_dict=True
				)
			return bank_cache.get(name) or frappe._dict()

		for row in self.payments:
			pe = frappe.get_doc("Payment Entry", row.payment_entry)
			debit = bank(pe.bank_account)
			bene = bank(pe.party_bank_account)

			addr = frappe._dict()
			address_name = frappe.db.get_value("Supplier", pe.party, "supplier_primary_address")
			if address_name:
				addr = frappe.db.get_value(
					"Address", address_name, ["address_line1", "address_line2", "city", "pincode"], as_dict=True
				)

			mobile = pe.get("contact_mobile")
			if not mobile and pe.get("contact_person"):
				mobile = frappe.db.get_value("Contact", pe.contact_person, "mobile_no")

			mode = pe.mode_of_payment or ""
			is_cheque = any(w in mode.upper() for w in ("CHEQUE", "CHECK"))

			payment = {
				"indicator": get_payment_code(mode),
				"ref_no": pe.name,
				"party": pe.party,
				"party_name": pe.party_name,
				"amount": fmt_amount(pe.paid_amount),
				"payment_date": fmt_date(pe.posting_date),
				"cheque_no": pe.reference_no if is_cheque else "",
				"debit_account": debit.bank_account_no,
				"bene_account": bene.bank_account_no,
				"ifsc": bene.branch_code,
				"bank_name": bene.bank,
				"addr1": addr.address_line1,
				"addr2": addr.address_line2,
				"city": addr.city,
				"zip": addr.pincode,
				"narration": pe.remarks,
				"fiscal_year": get_fiscal_year(pe.posting_date, company=pe.company)[0],
				"company": pe.company,
				"email": pe.get("contact_email"),
				"mobile": mobile,
			}
			line, errs = build_payment_line(payment)
			if not get_payment_code(mode):
				errs.append(_("Mode of Payment {0} has no NEFT / RTGS / IMPS / IFT in its name").format(mode))

			annexure = []
			deductions = sum(flt(d.amount) for d in pe.get("deductions") or [])
			for i, ref in enumerate(pe.references):
				invoice_no, invoice_date, tds = ref.reference_name, None, 0
				if ref.reference_doctype == "Purchase Invoice":
					pi = frappe.db.get_value(
						"Purchase Invoice", ref.reference_name, ["bill_no", "bill_date"], as_dict=True
					)
					invoice_no = (pi and pi.bill_no) or ref.reference_name
					invoice_date = pi and pi.bill_date
					tds = sum(
						abs(flt(a))
						for a in frappe.get_all(
							"Purchase Taxes and Charges",
							filters={
								"parent": ref.reference_name,
								"parenttype": "Purchase Invoice",
								"is_tax_withholding_account": 1,
							},
							pluck="tax_amount",
						)
					)
				a_line, a_errs = build_annexure_line(
					{
						"invoice_no": invoice_no,
						"invoice_date": fmt_date(invoice_date),
						"gross": fmt_amount(ref.total_amount),
						"tds": fmt_amount(tds),
						"deductions": fmt_amount(deductions if i == 0 else 0),
						"net": fmt_amount(ref.allocated_amount),
					}
				)
				errs += a_errs
				annexure.append(a_line)

			if errs:
				errors.append(f"<b>{pe.name}</b>: " + "; ".join(errs))
			lines.append(line)
			lines.extend(annexure)

		if errors:
			frappe.throw("<br>".join(errors), title=_("Fix these before submitting"))
		return "\n".join(lines) + "\n"