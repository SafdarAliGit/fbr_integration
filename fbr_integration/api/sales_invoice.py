from fbr_integration.api.fbr_api import FBRAPI
import frappe

def custom_on_submit(doc, method):
    
    fbr = FBRAPI(customer_name=doc.customer, invoice_doc=doc)
    result = fbr.post_invoice()
    
    if not result.get("success"):
        frappe.throw(f"FBR Submission Failed: {result.get('error')}")
    else:
        frappe.msgprint("Invoice successfully submitted to FBR")


