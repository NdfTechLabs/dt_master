import requests

import frappe
from frappe.utils import now_datetime
from dt_master.api.script_contract import app_environment, parse_output, run_remote_script, tag_ref

BASE_DIR = "/home/frappe/scripts"
SCRIPTS_BASE_DIR = f"{BASE_DIR}/bin/app/"
SCRIPT_LIB_DIR = f"{BASE_DIR}/lib/"

# ---------------------------------------------------------------------
# APP ACTION DEFINITIONS
# ---------------------------------------------------------------------
APP_ACTIONS = {
    "install": {
        "from_status": ["Pending", "Failed", "Uninstalled"],
        "script": "install_app.sh",
        "success_status": "Installed",
        "failure_status": "Failed",
    },
    "upgrade": {
        "from_status": ["Installed"],
        "script": "upgrade_app.sh",
        "success_status": "Installed",
        "failure_status": "Failed",
    },
    "downgrade": {
        "from_status": ["Installed"],
        "script": "downgrade_app.sh",
        "success_status": "Installed",
        "failure_status": "Failed",
    },
    "reinstall": {
        "from_status": ["Failed"],
        "script": "install_app.sh",
        "success_status": "Installed",
        "failure_status": "Failed",
    },
    "uninstall": {
        "from_status": ["Installed", "Disabled"],
        "script": "uninstall_app.sh",
        "success_status": "Uninstalled",
        "failure_status": "Failed",
    },
    "remove": {
        "from_status": ["Uninstalled"],
        "script": "remove_app.sh",
        "success_status": "Uninstalled",
        "failure_status": "Failed",
    },
    "check_version": {
        "from_status": ["Pending", "Failed", "Installed", "Uninstalled", "Disabled"],
        "script": "check_version.sh",
        "readonly": True,
    },
    "check_app": {
        "from_status": ["Pending", "Failed", "Installed", "Uninstalled", "Disabled"],
        "script": "check_app.sh",
        "readonly": True,
    },
}

# ---------------------------------------------------------------------
# ACTION LABELS
# ---------------------------------------------------------------------
ACTION_LABELS = {
    "install": "Install",
    "upgrade": "Upgrade",
    "downgrade": "Downgrade",
    "reinstall": "Reinstall",
    "uninstall": "Uninstall",
    "remove": "Uninstall",
    "check_version": "Check App",
    "check_app": "Check App",
}

# ---------------------------------------------------------------------
# SSH CONFIGURATION
# ---------------------------------------------------------------------
def _get_ssh_config(node):
    if not node.access_verified:
        frappe.throw("Node access not verified")

    return {
        "host": node.ip_address.strip(),
        "user": node.ssh_user,
        "port": node.ssh_port or 22,
    }

# ---------------------------------------------------------------------
# CONTEXT LOADING
# ---------------------------------------------------------------------
def _load_action_context(tenant_name, row_name):
    tenant = frappe.get_doc("Tenant Site", tenant_name)
    tenant.check_permission("write")
    app_row = next(r for r in tenant.installed_apps if r.name == row_name)
    node = frappe.get_doc("Infrastructure Node", tenant.node)
    node.check_permission("write")
    return tenant, app_row, node

# ---------------------------------------------------------------------
# ACTION VALIDATION
# ---------------------------------------------------------------------
def _validate_action(app_row, action):
    action_def = APP_ACTIONS.get(action)
    if not action_def:
        frappe.throw(f"Unknown app action: {action}")

    if app_row.status not in action_def["from_status"]:
        frappe.throw(
            f"Action '{action}' not allowed from status '{app_row.status}'"
        )

    if action not in ACTION_LABELS:
        frappe.throw(f"No UI label defined for action '{action}'")

    return action_def

# ---------------------------------------------------------------------
# ACTION STATE MANAGEMENT
# ---------------------------------------------------------------------
def _mark_action_running(tenant, app_row, action):
    app_row.last_action = ACTION_LABELS[action]
    app_row.last_action_status = "Running"
    app_row.last_error = None
    tenant.save(ignore_permissions=True)
    frappe.db.commit()

# ---------------------------------------------------------------------
# SCRIPT EXECUTION
# ---------------------------------------------------------------------
def _execute_ssh_script(node, tenant, app_row, script_name):
    ssh = _get_ssh_config(node)
    return run_remote_script(
        node, ssh, "app", script_name, app_environment(node, tenant, app_row),
        "tenant_app_log", {"tenant": tenant.name, "row": app_row.name},
    )

# ---------------------------------------------------------------------
# EXECUTION WRAPPER
# ---------------------------------------------------------------------
def _execute_action(node, tenant, app_row, script):
    exit_code, stdout, stderr = _execute_ssh_script(
        node, tenant, app_row, script
    )

    error = None
    if exit_code != 0:
        error = _extract_error(stdout, stderr, exit_code)

    return {
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "error": error,
    }

# ---------------------------------------------------------------------
# OUTPUT PARSING
# ---------------------------------------------------------------------
def _parse_kv_output(stdout: str) -> dict:
    return parse_output(stdout)

# ---------------------------------------------------------------------
# ERROR EXTRACTION
# ---------------------------------------------------------------------
def _extract_error(stdout, stderr, exit_code):
    if stderr:
        return stderr.strip().splitlines()[-1]
    if stdout:
        return stdout.strip().splitlines()[-1]
    return f"Exited with code {exit_code}"

# ---------------------------------------------------------------------
# EXTENSION RETRIEVAL/CREATION
# ---------------------------------------------------------------------
def get_or_create_extension(parsed):
    app_name = parsed["app_name"]

    name = frappe.db.get_value(
        "Extension",
        {"app_name": app_name},
        "name"
    )

    if name:
        return frappe.get_doc("Extension", name)

    extension = frappe.get_doc({
        "doctype": "Extension",
        "extension_name": app_name,
        "app_name": app_name,
        "repository_url": parsed.get("repository_url"),
        "install_mode": parsed.get("install_mode"),
        "default_branch": parsed.get("git_branch"),
        "status": "Approved",
    })
    extension.insert(ignore_permissions=True)

    return extension

# ---------------------------------------------------------------------
# MAJOR VERSION EXTRACTION
# ---------------------------------------------------------------------
def extract_major_versions(parsed: dict) -> set[int]:
    majors = set()

    for key in (
        "installed_version",
        "next_major_version",
        "latest_major_version",
    ):
        val = parsed.get(key)
        if val:
            try:
                majors.add(int(val.removeprefix("v").split(".", 1)[0]))
            except ValueError:
                pass

    return majors

# ---------------------------------------------------------------------
# EXTENSION VERSION RECONCILIATION
# ---------------------------------------------------------------------
def reconcile_extension_major_versions(extension, parsed):
    majors = extract_major_versions(parsed)
    created_or_found = []

    ref_type = parsed.get("ref_type")
    source_ref = parsed.get("source_ref") or parsed.get("git_ref")

    install_strategy = (
        "git_branch" if ref_type == "branch"
        else "git_tag" if ref_type == "tag"
        else "git_commit"
    )

    for major in sorted(majors):
        resolved_ref = f"version-{major}"
        if ref_type == "tag":
            candidates = [parsed.get(key, "") for key in (
                "installed_version", "next_major_version", "latest_major_version", "latest_minor_version"
            )]
            resolved_ref = next((tag_ref(value) for value in candidates
                                 if value.removeprefix("v").split(".", 1)[0] == str(major)), "")
            if not resolved_ref:
                continue
        elif ref_type != "branch":
            # A commit ref cannot be inferred from a semantic version number.
            continue
        name = f"{extension.name}-v{major}.*"

        if frappe.db.exists("Extension Version", name):
            ev = frappe.get_doc("Extension Version", name)
            created_or_found.append(ev)
            continue

        ev = frappe.get_doc({
            "doctype": "Extension Version",
            "name": name,
            "extension": extension.name,
            "version": f"v{major}.*",
            "frappe_min": f"{major}.0.0",
            "frappe_max": f"{major}.999.999",
            "release_notes": f"Frappe Version {major}",
            "deprecated": 0,
            "source_ref": resolved_ref,
            "ref_type": ref_type,
            "install_strategy": install_strategy,
            "status": "Approved",
        })

        ev.insert(ignore_permissions=True)
        created_or_found.append(ev)

    return created_or_found

# ---------------------------------------------------------------------
# RESULT HANDLERS
# ---------------------------------------------------------------------
def _handle_readonly_result(tenant, app_row, result, action):
    app_row.last_action_status = (
        "Success" if result["exit_code"] == 0 else "Failed"
    )
    app_row.last_error = result["error"]

    if result["exit_code"] != 0:
        tenant.save(ignore_permissions=True)
        frappe.db.commit()
        raise frappe.ValidationError(result["error"])

    parsed = _parse_kv_output(result["stdout"])
    if parsed.get("app_name") != app_row.app_name or parsed.get("installed") not in {"true", "false"}:
        raise frappe.ValidationError("Invalid app inspection result from shell script")
    if action == "check_version":
        extension = get_or_create_extension(parsed)
        extension_versions = reconcile_extension_major_versions(extension, parsed)
        app_row.target_version = parsed.get("latest_minor_version") or app_row.target_version
    app_row.status = (
        "Installed" if parsed.get("installed") == "true" else "Uninstalled"
    )
    app_row.installed_version = (
        parsed.get("installed_version") or app_row.installed_version
    )
    app_row.git_branch = parsed.get("git_branch")
    app_row.git_commit = parsed.get("git_commit")
    app_row.git_dirty = parsed.get("dirty") == "true"
    app_row.git_head_state = parsed.get("git_head_state", parsed.get("head_state"))

    tenant.save(ignore_permissions=True)
    frappe.db.commit()

    return parsed

def _handle_mutating_result(tenant, app_row, action_def, result):
    previous_status = app_row.status
    parsed = _parse_kv_output(result["stdout"])
    if result["exit_code"] == 0:
        script = action_def["script"]
        expected = "uninstalled" if script == "uninstall_app.sh" else "removed" if script == "remove_app.sh" else "installed"
        if parsed.get("app_name") != app_row.app_name or parsed.get(expected) not in {"true", "false"}:
            raise frappe.ValidationError(f"Invalid {expected} result from shell script")
        if expected != "uninstalled" and parsed[expected] != "true":
            raise frappe.ValidationError(f"Shell script did not confirm {expected}")
    if result["exit_code"] == 0:
        app_row.status = action_def["success_status"]
        if parsed.get("installed") == "true":
            app_row.installed_at = now_datetime()
        app_row.last_action_status = "Success"
        app_row.last_error = None
    else:
        app_row.status = action_def["failure_status"]
        app_row.last_action_status = "Failed"
        app_row.last_error = result["error"]
    if result["exit_code"] == 0:
        if "installed" in parsed:
            app_row.status = "Installed" if parsed["installed"] == "true" else "Uninstalled"
        elif parsed.get("uninstalled") == "false":
            # A successful no-op is not evidence that the site was uninstalled.
            app_row.status = previous_status
        app_row.installed_version = parsed.get("installed_version") or app_row.installed_version
        for key in ("git_branch", "git_commit", "git_head_state"):
            if key in parsed:
                setattr(app_row, key, parsed[key])
        if "dirty" in parsed:
            app_row.git_dirty = parsed["dirty"] == "true"
    app_row.last_updated = now_datetime()
    tenant.save(ignore_permissions=True)
    frappe.db.commit()

    if result["exit_code"] != 0:
        raise frappe.ValidationError(result["error"])

    return result["stdout"]

# ---------------------------------------------------------------------
# MAIN ORCHESTRATOR
# ---------------------------------------------------------------------

def _run_app_action(tenant_name, row_name, action, source=None):

    tenant, app_row, node = _load_action_context(tenant_name, row_name)
    action_def = _validate_action(app_row, action)

    try:
        _mark_action_running(tenant, app_row, action)
        frappe.logger().info(
            f"[TRACE] action={action}, source={source}, tenant={tenant.name}, row={app_row.name}"
        )
        if source == "tenant":
            publish_install_event(tenant, app_row)

        result = _execute_action(
            node, tenant, app_row, action_def["script"]
        )

        if action_def.get("readonly"):
            output = _handle_readonly_result(tenant, app_row, result, action)
        else:
            output = _handle_mutating_result(tenant, app_row, action_def, result)

        if source == "tenant":
            publish_install_event(tenant, app_row)

        return output

    except Exception:

        # capture traceback
        error = frappe.get_traceback()

        if not action_def.get("readonly"):
            app_row.status = "Failed"
        app_row.last_action_status = "Failed"
        app_row.last_error = error
        app_row.last_updated = now_datetime()

        tenant.save(ignore_permissions=True)
        frappe.db.commit()

        frappe.log_error(
            title="Extension Executor Failed",
            message=error
        )

        if source == "tenant":
            publish_install_event(tenant, app_row)

        raise

def tenant_base_url(tenant):
    protocol = (tenant.protocol or "https").lower().replace("://", "")
    fqdn = tenant.fqdn
    port = tenant.port

    if protocol == "https":
        # Public access via Nginx
        return f"https://{fqdn}"

    if protocol == "http":
        # configure_tenant_proxy.sh exposes HTTP on port 80. tenant.port is
        # the private Gunicorn upstream, not the public API port.
        return f"http://{fqdn}"

    raise ValueError(f"Unsupported protocol: {protocol}")

def _tenant_api_call(tenant, path, data=None):

    url = tenant_base_url(tenant) + path

    headers = {
        "Authorization": f"token {tenant.tenant_api_key}:{tenant.tenant_api_secret}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(url, json=data or {}, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException:
        frappe.log_error(
            frappe.get_traceback(),
            f"Tenant API call failed: {url}"
        )
        return None

def publish_install_event(tenant, app_row):

    data = {
        "extension": app_row.extension,
        "status": app_row.status,
        "action_status": app_row.last_action_status,
        "error": app_row.last_error,
        "site": tenant.fqdn
    }

    frappe.logger().info(
        f"[PUBLISH] tenant={tenant.name}, data={data}"
    )

    try:
        _tenant_api_call(
            tenant,
            "/api/method/dt_tenant.api.marketplace.publish_install_update",
            data
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Tenant realtime publish failed"
        )

# ---------------------------------------------------------------------
# FRAPPE WHITELISTED FUNCTIONS
# ---------------------------------------------------------------------
@frappe.whitelist()
def install_app(tenant_name, row_name, source=None):
    return _run_app_action(tenant_name, row_name, "install", source=source)

@frappe.whitelist()
def upgrade_app(tenant_name, row_name, source=None):
    return _run_app_action(tenant_name, row_name, "upgrade", source=source)

@frappe.whitelist()
def downgrade_app(tenant_name, row_name, source=None):
    return _run_app_action(tenant_name, row_name, "downgrade", source=source)

@frappe.whitelist()
def reinstall_app(tenant_name, row_name, source=None):
    return _run_app_action(tenant_name, row_name, "reinstall", source=source)

@frappe.whitelist()
def uninstall_app(tenant_name, row_name, source=None):
    return _run_app_action(tenant_name, row_name, "uninstall", source=source)

@frappe.whitelist()
def remove_app(tenant_name, row_name, source=None):
    return _run_app_action(tenant_name, row_name, "remove", source=source)

@frappe.whitelist()
def check_version(tenant_name, row_name, source=None):
    return _run_app_action(tenant_name, row_name, "check_version", source=source)

@frappe.whitelist()
def check_app(tenant_name, row_name, source=None):
    return _run_app_action(tenant_name, row_name, "check_app", source=source)
