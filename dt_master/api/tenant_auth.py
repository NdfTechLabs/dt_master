"""Caller → Tenant Site binding for the APIs consumed by dt_tenant.

A request-supplied FQDN or invoice name is never proof of entitlement. The
Tenant Site is resolved from the authenticated user: each Tenant Site names
one Master-side service user in ``master_api_user``. System Managers are
platform operators and may address any tenant explicitly.
"""

import frappe

OPERATOR_ROLE = "System Manager"


def normalize_fqdn(fqdn):
    return (fqdn or "").strip().lower().split(":")[0]


def is_operator(user=None):
    return OPERATOR_ROLE in frappe.get_roles(user or frappe.session.user)


def get_bound_tenant_name(user=None):
    """Tenant Site whose service identity is the given (default: session) user."""
    user = user or frappe.session.user
    if not user or user == "Guest":
        return None
    names = frappe.get_all("Tenant Site", filters={"master_api_user": user}, pluck="name", limit=2)
    if len(names) > 1:
        frappe.throw("Service user is bound to more than one Tenant Site", frappe.PermissionError)
    return names[0] if names else None


def resolve_tenant_name(fqdn=None, required=True):
    """Return the Tenant Site name the caller may act on.

    * Bound service user: always its own Tenant Site; a different ``fqdn`` is refused.
    * System Manager: the Tenant Site addressed by ``fqdn``.
    * Anyone else: refused.
    """
    fqdn = normalize_fqdn(fqdn)
    bound = get_bound_tenant_name()

    if bound:
        bound_fqdn = normalize_fqdn(frappe.db.get_value("Tenant Site", bound, "fqdn"))
        if fqdn and fqdn != bound_fqdn:
            frappe.throw("Not permitted for this tenant", frappe.PermissionError)
        return bound

    if not is_operator():
        frappe.throw("Not permitted for this tenant", frappe.PermissionError)

    if not fqdn:
        if required:
            frappe.throw("Tenant FQDN is required")
        return None

    name = frappe.db.get_value("Tenant Site", {"fqdn": fqdn}, "name")
    if not name and required:
        frappe.throw("Tenant not found")
    return name


def resolve_tenant(fqdn=None):
    return frappe.get_doc("Tenant Site", resolve_tenant_name(fqdn))


def authorize_invoice(invoice_name, fqdn=None):
    """Return the Sales Invoice if it belongs to the caller's tenant.

    Operators that do not name a tenant keep access to any invoice.
    """
    tenant_name = resolve_tenant_name(fqdn, required=False)
    invoice = frappe.get_doc("Sales Invoice", invoice_name)

    if tenant_name:
        tenant = frappe.db.get_value("Tenant Site", tenant_name, ["subscription", "customer"], as_dict=True)
        owned = (
            (tenant.subscription and invoice.get("subscription") == tenant.subscription)
            or (tenant.customer and invoice.customer == tenant.customer)
        )
        if not owned:
            frappe.throw("Invoice does not belong to this tenant", frappe.PermissionError)

    return invoice
