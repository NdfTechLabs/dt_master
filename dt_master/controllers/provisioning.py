import requests

import frappe

from dt_master.api.script_contract import secret

def tenant_base_url(tenant):
    protocol = (tenant.protocol or "https").lower().replace("://", "")
    port = tenant.port

    if port:
        return f"{protocol}://{tenant.fqdn}:{port}"
    return f"{protocol}://{tenant.fqdn}"

def _ensure_site_reachable(tenant):
    url = tenant_base_url(tenant)

    try:
        r = requests.get(url, timeout=5)
        if r.status_code >= 400:
            raise Exception(f"Tenant site unreachable: {r.status_code}")
    except Exception as e:
        raise Exception(f"Tenant site not reachable: {str(e)}")

def _ensure_tenant_app_installed(tenant):
    resp = _tenant_api_call(
        tenant,
        "/api/method/dt_tenant.api.system.is_app_installed",
        {"app_name": "dt_tenant"}
    )

    if not resp.get("message", {}).get("installed"):
        raise Exception("dt_tenant app not installed on tenant site")

def _configure_tenant_settings(tenant):
    payload = {
        "master_url": frappe.utils.get_url(),
        "enable_capability_sync": 1,
    }

    _tenant_api_call(
        tenant,
        "/api/method/dt_tenant.api.settings.configure",
        payload
    )

def _initial_capability_sync(tenant):
    _tenant_api_call(
        tenant,
        "/api/method/dt_tenant.controllers.capability_sync.resync_capabilities"
    )

def _ensure_admin_user(tenant):
    resp = _tenant_api_call(
        tenant,
        "/api/method/dt_tenant.api.system.has_active_system_manager"
    )

    if not resp.get("message"):
        raise Exception("No active System Manager found on tenant site")


def _tenant_api_call(tenant, path, data=None):
    url = tenant_base_url(tenant) + path

    headers = {
        "Authorization": f"token {tenant.tenant_api_key}:{secret(tenant, 'tenant_api_secret')}",
        "Content-Type": "application/json",
    }

    r = requests.post(url, json=data or {}, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()


@frappe.whitelist()
def provision_tenant(site_name):
    frappe.only_for("System Manager")

    tenant = frappe.get_doc("Tenant Site", site_name)
    tenant.check_permission("write")

    if tenant.status == "Archived":
        frappe.throw("Archived sites cannot be provisioned")

    if not tenant.tenant_api_key or not secret(tenant, "tenant_api_secret"):
        frappe.throw("Tenant API credentials are not configured on the Tenant Site")

    previous_status = tenant.status
    tenant.status = "Provisioning"
    tenant.save()

    steps = [
        _ensure_site_reachable,
        _ensure_tenant_app_installed,
        _configure_tenant_settings,
        _initial_capability_sync,
        _ensure_admin_user,
    ]

    try:
        for step in steps:
            step(tenant)
    except Exception as e:
        # "Failed" is not a Tenant Site status; put the record back where it was
        # and report the real error.
        tenant.status = previous_status
        tenant.save()
        frappe.db.commit()
        frappe.throw(f"Tenant provisioning failed: {e}")

    tenant.status = "Active"
    tenant.save()
    return {"status": "active"}
