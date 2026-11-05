import frappe
from frappe.utils import now_datetime
from dt_master.api.script_contract import mail_environment, run_remote_script


BASE_DIR = "/home/frappe/scripts"
SCRIPTS_BASE_DIR = f"{BASE_DIR}/bin/mail"
SCRIPT_LIB_DIR = f"{BASE_DIR}/lib"


MAIL_AUTOMATION_TRANSITIONS = {
    "idle": ["idle", "queued", "installing_stack"],
    
    "queued": [
        "installing_stack",
        "configuring_postfix",
        "configuring_dovecot",
        "configuring_rspamd",
        "configuring_opendkim",
        "configuring_dns",
        "verifying",
        "destroying",
        "failed"
    ],

    "installing_stack": ["configuring_postfix", "failed", "queued"],
    
    "configuring_postfix": ["configuring_dovecot", "failed", "queued"],
    
    "configuring_dovecot": ["configuring_rspamd", "failed", "queued"],
    
    "configuring_rspamd": ["configuring_opendkim", "failed", "queued"],
    
    "configuring_opendkim": ["configuring_dns", "failed", "queued"],
    
    "configuring_dns": ["verifying", "failed", "queued"],
    
    "verifying": ["ready", "failed", "queued"],
    
    "ready": ["verifying", "destroying", "queued"],
    
    "destroying": ["destroyed", "failed"],
    
    "destroyed": [],
    
    "failed": [
        "queued",
        "installing_stack",
        "configuring_postfix",
        "configuring_dovecot",
        "configuring_rspamd",
        "configuring_opendkim",
        "configuring_dns",
        "verifying"
    ],
}

MAIL_STATUS_TRANSITIONS = {
    "Requested": ["Installing", "Archived"],
    "Installing": ["Active", "Failed", "Archived"],
    "Active": ["Installing", "Archived"],
    "Failed": ["Installing", "Archived"],
    "Archived": [],
}

MAIL_SERVER_TRANSITIONS = {

    "install_stack": {
        "from": ["idle", "failed"],
        "to": "installing_stack",
        "success": ("configuring_postfix", "Installing"),
        "failure": ("failed", "Failed"),
    },

    "configure_postfix": {
        "from": ["installing_stack", "configuring_postfix", "failed"],
        "to": "configuring_postfix",
        "success": ("configuring_dovecot", "Installing"),
        "failure": ("failed", "Failed"),
    },

    "configure_dovecot": {
        "from": ["configuring_postfix", "configuring_dovecot", "failed"],
        "to": "configuring_dovecot",
        "success": ("configuring_rspamd", "Installing"),
        "failure": ("failed", "Failed"),
    },

    "configure_rspamd": {
        "from": ["configuring_dovecot", "configuring_rspamd", "failed"],
        "to": "configuring_rspamd",
        "success": ("configuring_opendkim", "Installing"),
        "failure": ("failed", "Failed"),
    },

    "configure_opendkim": {
        "from": ["configuring_rspamd", "configuring_opendkim", "failed"],
        "to": "configuring_opendkim",
        "success": ("configuring_dns", "Installing"),
        "failure": ("failed", "Failed"),
    },

    "configure_dns": {
        "from": ["configuring_opendkim", "configuring_dns", "failed"],
        "to": "configuring_dns",
        "success": ("verifying", "Installing"),
        "failure": ("failed", "Failed"),
    },

    "verify_mail": {
        "from": ["verifying", "configuring_dns", "failed", "ready"],
        "to": "verifying",
        "success": ("ready", "Active"),
        "failure": ("failed", "Failed"),
    },

    "destroy_mail": {
        "from": ["ready", "failed"],
        "to": "destroying",
        "success": ("destroyed", "Archived"),
        "failure": ("failed", "Archived"),
    },
}

MAIL_SERVER_TRANSITIONS.update({

    "add_user": {
        "from": ["ready"],
        "to": "ready",
        "success": ("ready", "Active"),
        "failure": ("ready", "Active"),
    },

    "remove_user": {
        "from": ["ready"],
        "to": "ready",
        "success": ("ready", "Active"),
        "failure": ("ready", "Active"),
    },

})

MAIL_SERVER_TRANSITIONS.update({

    "list_users": {
        "from": ["ready"],
        "to": "ready",
        "success": ("ready", "Active"),
        "failure": ("ready", "Active"),
    },

})

MAIL_SERVER_TRANSITIONS.update({

    "update_user_password": {
        "from": ["ready"],
        "to": "ready",
        "success": ("ready", "Active"),
        "failure": ("ready", "Active"),
    },

})

MAIL_ACTION_SCRIPT_MAP = {
    "install_stack": ("installing_stack", "install_mail_stack.sh"),
    "configure_postfix": ("configuring_postfix", "configure_postfix.sh"),
    "configure_dovecot": ("configuring_dovecot", "configure_dovecot.sh"),
    "configure_rspamd": ("configuring_rspamd", "configure_rspamd.sh"),
    "configure_opendkim": ("configuring_opendkim", "configure_opendkim.sh"),
    "configure_dns": ("configuring_dns", "configure_dns_records.sh"),
    "verify_mail": ("verifying", "verify_mail_server.sh"),
}

MAIL_ACTION_SCRIPT_MAP.update({
    "add_user": ("ready", "add_user.sh"),
    "remove_user": ("ready", "remove_user.sh"),
})
MAIL_ACTION_SCRIPT_MAP.update({
    "list_users": ("ready", "list_users.sh"),
})

MAIL_ACTION_SCRIPT_MAP.update({
    "update_user_password": ("ready", "update_user_password.sh"),
})




# -----------------------------------------------------------
# SSH CONFIG
# -----------------------------------------------------------

def _get_ssh_config(node):

    if not node.project_base_dir or not node.project_logs_dir:
        frappe.throw("Project paths not configured on node")

    if not node.access_verified:
        frappe.throw("Node access not verified")

    if not node.ip_address or not node.ssh_user:
        frappe.throw("Incomplete SSH configuration")

    return {
        "host": node.ip_address.strip(),
        "user": node.ssh_user,
        "port": node.ssh_port or 22,
    }


# -----------------------------------------------------------
# SCRIPT EXECUTION
# -----------------------------------------------------------

def _execute_mail_script(node, mail_server, script_name, mail_user=None):
    ssh = _get_ssh_config(node)
    env = mail_environment(node, mail_server, mail_user)
    if script_name in {"configure_postfix.sh", "configure_dovecot.sh", "add_user.sh", "remove_user.sh", "list_users.sh", "update_user_password.sh"}:
        if not env.get("MYSQL_ROOT_USER") or not env.get("MYSQL_ROOT_PASSWORD"):
            frappe.throw("Link a Tenant Site with database credentials before running this mail action")
    if script_name in {"add_user.sh", "remove_user.sh", "update_user_password.sh"} and mail_user is None:
        frappe.throw("A mailbox user is required for this action")
    return run_remote_script(
        node, ssh, "mail", script_name, env, "mail_executor_log", {"server": mail_server.name},
    )


# -----------------------------------------------------------
# ERROR EXTRACTION
# -----------------------------------------------------------

def _extract_error(stdout, stderr, exit_code):

    stderr = (stderr or "").strip()
    stdout = (stdout or "").strip()

    if stderr:
        return stderr.splitlines()[-1]

    if stdout:
        return stdout.splitlines()[-1]

    return f"Command failed with exit code {exit_code}"

def _log_mail_action(
    mail_server,
    action_name,
    result,
    exit_code,
    stdout,
    stderr,
    started_at,
    finished_at,
):

    mail_server.append("automation_logs", {
    "action_name": action_name,
    "triggered_by": frappe.session.user,
    "started_at": started_at,
    "finished_at": finished_at,
    "duration_seconds": (finished_at - started_at).total_seconds(),
    "result": result,
    "exit_code": exit_code,
    "stdout": stdout,
    "stderr": stderr,
})

    mail_server.save(ignore_permissions=True)
    frappe.db.commit()


def generate_system_username(email):
    local, domain = email.split("@")
    return f"{local}_{domain.replace('.', '_')}"

# -----------------------------------------------------------
# MAIN ACTION RUNNER
# -----------------------------------------------------------
def _run_mail_action(mail_server_name, action, mail_user=None):

    mapping = MAIL_ACTION_SCRIPT_MAP.get(action)

    if not mapping:
        frappe.throw(f"Unknown mail action: {action}")

    expected_state, script = mapping

    mail_server = frappe.get_doc("Mail Server", mail_server_name)
    node = frappe.get_doc("Infrastructure Node", mail_server.node)
    mail_server.check_permission("write")
    node.check_permission("write")

    transition = MAIL_SERVER_TRANSITIONS.get(action)

    if not transition:
        frappe.throw(f"No transition defined for action {action}")

    current_state = mail_server.automation_state

    if current_state not in transition["from"]:
        frappe.throw(
            f"Action '{action}' not allowed from state '{current_state}'"
        )

    mail_server = frappe.get_doc("Mail Server", mail_server_name)
    node = frappe.get_doc("Infrastructure Node", mail_server.node)

    started = now_datetime()

    stdout = ""
    stderr = ""
    exit_code = -1

    try:

        if transition["to"]:
            mail_server.automation_state = transition["to"]

        mail_server.save(ignore_permissions=True)
        frappe.db.commit()

        exit_code, stdout, stderr = _execute_mail_script(
                                                        node,
                                                        mail_server,
                                                        script,
                                                        mail_user
                                                    )

        finished = now_datetime()

        if exit_code != 0:
            raise frappe.ValidationError(_extract_error(stdout, stderr, exit_code))

        # success
        success = transition.get("success")

        if success:
            next_state, next_status = success
            mail_server.automation_state = next_state
            mail_server.status = next_status

        # -----------------------------------------------------------
        # Post-success actions
        # -----------------------------------------------------------

        if action == "remove_user" and mail_user:

            mail_server.set("mail_users", [row for row in mail_server.mail_users if row.name != mail_user.name])

        if mail_user and action in {"add_user", "update_user_password"}:
            for row in mail_server.mail_users:
                if row.name == mail_user.name:
                    row.status = "Active"
                    row.active = 1
                    row.last_password_change = now_datetime()

        mail_server.save(ignore_permissions=True)
        frappe.db.commit()

        _log_mail_action(
            mail_server,
            action.replace("_", " ").title(),
            "Success",
            exit_code,
            stdout,
            stderr,
            started,
            finished,
        )

        return stdout

    except Exception:

        finished = now_datetime()

        traceback_msg = frappe.get_traceback()

        # merge shell stderr with python traceback
        if stderr:
            stderr = f"{stderr}\n\nPYTHON TRACEBACK:\n{traceback_msg}"
        else:
            stderr = traceback_msg

        failure = transition.get("failure")
        if failure:
            next_state, next_status = failure
            mail_server.automation_state = next_state
            mail_server.status = next_status
        else:
            mail_server.automation_state = "failed"
            mail_server.status = "Failed"

        mail_server.save(ignore_permissions=True)
        frappe.db.commit()

        _log_mail_action(
            mail_server,
            action.replace("_", " ").title(),
            "Failed",
            exit_code,
            stdout,
            stderr,
            started,
            finished,
        )

        frappe.log_error(
            title="Mail Server Automation Failed",
            message=stderr
        )

        raise


# -----------------------------------------------------------
# WHITELISTED API
# -----------------------------------------------------------

@frappe.whitelist()
def install_mail_stack(mail_server_name):
    return _run_mail_action(mail_server_name, "install_stack")


@frappe.whitelist()
def configure_postfix(mail_server_name):
    return _run_mail_action(mail_server_name, "configure_postfix")


@frappe.whitelist()
def configure_dovecot(mail_server_name):
    return _run_mail_action(mail_server_name, "configure_dovecot")


@frappe.whitelist()
def configure_rspamd(mail_server_name):
    return _run_mail_action(mail_server_name, "configure_rspamd")


@frappe.whitelist()
def configure_opendkim(mail_server_name):
    return _run_mail_action(mail_server_name, "configure_opendkim")


@frappe.whitelist()
def configure_dns(mail_server_name):
    return _run_mail_action(mail_server_name, "configure_dns")


@frappe.whitelist()
def verify_mail_server(mail_server_name):
    return _run_mail_action(mail_server_name, "verify_mail")

@frappe.whitelist()
def add_mail_user(mail_user_name):

    mail_user = frappe.get_doc("Master Mail Users", mail_user_name)
    mail_server = frappe.get_doc("Mail Server", mail_user.parent)

    return _run_mail_action(mail_server.name, "add_user", mail_user)


@frappe.whitelist()
def remove_mail_user(mail_user_name):

    mail_user = frappe.get_doc("Master Mail Users", mail_user_name)
    mail_server = frappe.get_doc("Mail Server", mail_user.parent)

    return _run_mail_action(mail_server.name, "remove_user", mail_user)


@frappe.whitelist()
def update_mail_user_password(mail_user_name):

    mail_user = frappe.get_doc("Master Mail Users", mail_user_name)
    mail_server = frappe.get_doc("Mail Server", mail_user.parent)

    return _run_mail_action(mail_server.name, "update_user_password", mail_user)

@frappe.whitelist()
def list_mail_users(mail_server_name):
    return _run_mail_action(mail_server_name, "list_users")
