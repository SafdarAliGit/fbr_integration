# fbr_integration/api/fbr_api.py
import json
import frappe
import requests
from frappe.utils import now_datetime, flt, cint
from requests.exceptions import RequestException

class FBRAPI:
    def __init__(self, customer_name=None, invoice_doc=None):
        """
        Initialize with optional customer_name and invoice_doc
        Allows for more flexible usage
        """
        self.settings = frappe.get_single("FBR Integration Settings")
        
        # Request parameters with defaults
        self.base_url = (self.settings.get("base_url") or 
                        "https://gw.fbr.gov.pk/di_data/v1/di/").rstrip('/') + '/'
        self.auth_token = self.settings.get("auth_token")
        self.timeout = cint(self.settings.get("timeout")) or 30
        self.default_tax_rate = flt(self.settings.get("tax_rate"))
        
        # Initialize customer data if provided
        self.customer = None
        if customer_name:
            self._init_customer(customer_name)
            
        # Store invoice doc reference if provided
        self.invoice_doc = invoice_doc

    def _init_customer(self, customer_name):
        """Safely initialize customer data"""
        if frappe.db.exists("Customer", customer_name):
            self.customer = frappe.get_doc("Customer", customer_name)
        else:
            frappe.log_error(f"Customer {customer_name} not found", "FBRAPI Init Error")

    def prepare_headers(self):
        """Prepare standard headers with auth token"""
        if not self.auth_token:
            frappe.throw("FBR Authorization Token is not configured in Settings")
            
        return {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def get_customer_info(self):
        """Return standardized customer info dict"""
        if not self.customer:
            return {}
            
        return {
            "name": self.customer.name,
            "tax_id": (self.customer.tax_id or 
                      getattr(self.customer, "custom_cnic", "") or ""),
            "province": getattr(self.customer, "custom_province", "") or "",
            "address": (self.customer.primary_address or 
                       getattr(self.customer, "custom_address", "") or "")
        }

    def prepare_invoice_item(self, item):
        """
        Prepare standardized item data for FBR
        Can be overridden for custom implementations
        """
        tax_rate = flt(item.tax_rate or self.default_tax_rate)
        tax_amount = flt(item.amount) * (tax_rate / 100)
        
        return {
            "hsCode": item.get("hs_code") or "99260000",
            "productDescription": item.item_name or item.item_code,
            "rate": f"{tax_rate}%",
            "uoM": item.uom or item.stock_uom or "KG",
            "quantity": flt(item.qty),
            "totalValues": flt(item.amount) + tax_amount,
            "valueSalesExcludingST": flt(item.amount),
            "salesTaxApplicable": tax_amount,
            "salesTaxWithheldAtSource": flt(item.get("withholding_tax_amount", 0)),
            # Additional fields can be added here
        }

    def prepare_request_data(self, invoice_doc=None):
        """
        Prepare complete request payload
        Can accept invoice_doc parameter or use self.invoice_doc
        """
        invoice_doc = invoice_doc or self.invoice_doc
        if not invoice_doc:
            frappe.throw("No invoice document provided")
            
        customer_info = self.get_customer_info()
        
        return {
            "invoiceType": cint(self.settings.get("invoice_type", 1)),
            "invoiceDate": invoice_doc.posting_date.strftime("%Y-%m-%d"),
            "sellerBusinessName": self.settings.get("seller_business_name"),
            "sellerAddress": self.settings.get("seller_address"),
            "sellerProvince": self.settings.get("seller_province"),
            "buyerNTNCNIC": customer_info.get("tax_id", ""),
            "buyerBusinessName": customer_info.get("name", ""),
            "buyerProvince": customer_info.get("province", ""),
            "buyerAddress": customer_info.get("address", ""),
            "invoiceRefNo": invoice_doc.name,
            "items": [self.prepare_invoice_item(item) for item in invoice_doc.items]
        }

    def post_invoice(self, invoice_doc=None):
        """Main method to submit invoice to FBR"""
        self.validate_credentials()
        invoice_doc = invoice_doc or self.invoice_doc
        
        try:
            request_data = self.prepare_request_data(invoice_doc)
            endpoint = f"{self.base_url}postinvoicedata_sb"
            
            response = requests.post(
                endpoint,
                headers=self.prepare_headers(),
                json=request_data,
                timeout=self.timeout
            )
            
            response.raise_for_status()
            response_data = response.json()
            
            self.log_submission(
                invoice_no=invoice_doc.name,
                status="Success",
                request_data=request_data,
                response_data=response_data
            )
            
            return {
                "success": True,
                "data": response_data,
                "fbr_invoice_ref": response_data.get("invoice_ref"),
                "request_data": request_data  # For debugging
            }
            
        except Exception as e:
            error_message = self._get_error_message(e)
            self.log_submission(
                invoice_no=invoice_doc.name if invoice_doc else "Unknown",
                status="Failed",
                error_message=error_message,
                request_data=request_data if 'request_data' in locals() else None
            )
            return {
                "success": False,
                "error": error_message,
                "exception": str(e)
            }

    def _get_error_message(self, exception):
        """Extract error message from exception"""
        if isinstance(exception, RequestException) and hasattr(exception, "response"):
            try:
                error_data = exception.response.json()
                return (error_data.get("message") or 
                        error_data.get("error") or 
                        exception.response.text)
            except:
                return f"HTTP {exception.response.status_code}: {exception.response.text}"
        return str(exception)

    def log_submission(self, invoice_no, status, request_data=None, 
                      response_data=None, error_message=None):
        """Generic logging method"""
        log_data = {
            "doctype": "FBR Submission Log",
            "invoice": invoice_no,
            "status": status,
            "submission_time": now_datetime(),
            "request_data": json.dumps(request_data, indent=2) if request_data else None,
            "response_data": json.dumps(response_data, indent=2) if response_data else None,
            "error_message": error_message
        }
        
        try:
            frappe.get_doc(log_data).insert(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(
                title="FBR Logging Failed",
                message=f"Error logging submission: {str(e)}\nOriginal Data: {log_data}"
            )

    def validate_credentials(self):
        """Validate required settings"""
        if not self.auth_token:
            frappe.throw("FBR Authorization Token is not configured in Settings")
        if not self.settings.get("seller_business_name"):
            frappe.throw("Seller business name is not configured in Settings")