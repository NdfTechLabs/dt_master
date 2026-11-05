import datetime
import json

import frappe
from frappe.utils import now_datetime
from dt_master.api.script_contract import framework_environment, parse_output, run_remote_script


BASE_DIR="/home/frank/scripts"
SCRIPT_BASE_DIR = f"{BASE_DIR}/bin/node/"
SCRIPT_LIB_DIR = f"{BASE_DIR}/lib/"

ALLOWED_TRANSITIONS = {
    "Declared": ["SSH Ready","Error"],
    "SSH Ready": ["Deps Installed","Error"],
    "Deps Installed": ["Project Installed","Error"],
    "Project Installed": ["Initialized","Error"],
    "Initialized": ["Healthy","Error"],
    "Healthy": ["Running","Error"],
    "Running": ["Stopped","Error"],
    "Stopped": ["Running","Error"],
    "Error": ["Error","Stopped","Running","Healthy","Initialized","Project Installed","Deps Installed","SSH Ready","Declared"],
}
ALLOWED_STATUS_TRANSITIONS = {
    "Provisioning": ["Active", "Maintenance","Provisioning"],
    "Active": ["Maintenance", "Retired", "Active"],
    "Maintenance": ["Provisioning", "Active", "Retired","Maintenance"],
    "Retired": [],
}

NODE_TRANSITIONS = {
    "install_dependencies": {
        # Normal forward path
        "from": ["SSH Ready"],
        # Recovery path (retry after failure)
        "recover_from": ["Error"],
        # Outcomes
        "success": ("Deps Installed", "Provisioning"),
        "failure": ("Error", "Maintenance"),
    },
    "install_bench": {
        "from": ["Deps Installed"],
        "recover_from": ["Error"],
        "success": ("Project Installed", "Provisioning"),
        "failure": ("Error", "Maintenance"),
    },
    "init_project": {
        "from": ["Project Installed"],
        "recover_from": ["Error"],
        "success": ("Initialized", "Provisioning"),
        "failure": ("Error", "Maintenance"),
    },
    "prepare_production": {
        "from": ["Initialized"],
        "recover_from": ["Error"],
        "success": ("Healthy", "Active"),
        "failure": ("Error", "Maintenance"),
    },
    "start_project": {
        "from": ["Healthy", "Stopped"],
        "recover_from": ["Error"],
        "success": ("Running", "Active"),
        "failure": ("Error", "Maintenance"),
    },
    "stop_project": {
        "from": ["Running"],
        "recover_from": [],
        "success": ("Stopped", "Active"),
        "failure": ("Error", "Maintenance"),
    },
    "verify_production": {
        "from": ["Healthy", "Running", "Stopped",'SSH Ready'],
        "recover_from": [],
        "success": (None, None),          # no state/status change
        "failure": ("Error", "Maintenance"),
    },
}


def _validate_status_transition(current, next_):
    allowed = ALLOWED_STATUS_TRANSITIONS.get(current, [])
    if next_ not in allowed:
        frappe.throw(
            f"Invalid status transition: '{current}' → '{next_}'"
        )

def _validate_transition(from_state, to_state):
    if from_state == to_state:
        return
    if to_state not in ALLOWED_TRANSITIONS.get(from_state, []):
        frappe.throw(f"Invalid node state transition: {from_state} → {to_state}")


def _get_ssh_config(node):
    node.check_permission("write")
    if not node.project_base_dir or not node.project_logs_dir:
       frappe.throw("Project paths not configured on node")

    if not node.access_verified:
        frappe.throw("Node access not verified")

    if not node.ip_address or not node.ssh_user:
        frappe.throw("Incomplete SSH configuration")

    return {
        "host": node.ip_address.strip(),
        "user": node.ssh_user,
        "port": node.ssh_port or 22
    }

def _get_node_dependencies(node):
    deps = frappe.get_all(
        "Framework Version Dependency",
        filters={
            "framework_version": node.frappe_version,
            "enabled": 1
        },
        fields=["dependency", "install_order"],
        order_by="install_order asc"
    )

    result = []

    for d in deps:
        dep = frappe.get_doc("System Dependency", d.dependency)

        result.append({
            "name": dep.name,
            "install_method": dep.install_method,
            "group": dep.dependency_group,
            "version": getattr(dep, "version", None),
            "repository": dep.repository,
        })

    return result

def _execute_ssh_script(node, script_name):
    ssh = _get_ssh_config(node)
    env = framework_environment(node)
    env["SYSTEM_DEPENDENCIES"] = json.dumps(_get_node_dependencies(node))
    return run_remote_script(
        node, ssh, "node", script_name, env, "script_execution_log",
        {"execution": script_name, "node": node.name},
    )

def _log_node_action(
    node,
    action_name,
    action_type,
    state_before,
    state_after,
    result,
    exit_code,
    stdout,
    stderr,
    started_at=None,
    finished_at=None
):
    node.append("node_action_logs", {
        "action_name": action_name,
        "action_type": action_type,
        "triggered_by": frappe.session.user,
        "started_at": started_at or datetime.datetime.now(),
        "finished_at": finished_at or datetime.datetime.now(),
        "state_before": state_before,
        "state_after": state_after,
        "result": result,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr
    })

    node.save(ignore_permissions=True)
    frappe.db.commit()


BENCH_ACTIONS = {
    "backup": ("backup_bench.sh", "backups_taken"),
    "update": ("update_bench.sh", "bench_updated"),
    "migrate": ("migrate_bench.sh", "bench_migrated"),
    "restore": ("restore_backup.sh", "site_restored"),
    "remove": ("remove_bench.sh", "bench_removed"),
}


def _run_bench_action(node_name, action, tenant_name=None, restore_dir=None, stop_only=False):
    node = frappe.get_doc("Infrastructure Node", node_name)
    node.check_permission("write")
    frappe.only_for("System Manager")
    if node.state not in {"Initialized", "Healthy", "Running", "Stopped", "Error"}:
        frappe.throw(f"Bench action not allowed from node state '{node.state}'")
    script, expected_key = BENCH_ACTIONS[action]
    env = framework_environment(node)
    if tenant_name:
        tenant = frappe.get_doc("Tenant Site", tenant_name)
        tenant.check_permission("write")
        if tenant.node != node.name:
            frappe.throw("Tenant Site belongs to another node")
        env.update({"SITE_NAME": tenant.fqdn, "MYSQL_ROOT_USER": tenant.mysql_root_user_name,
                    "MYSQL_ROOT_PASSWORD": tenant.mysql_root_user_password})
    if action == "restore":
        if not tenant_name or not restore_dir or not str(restore_dir).startswith("/"):
            frappe.throw("Restore requires a Tenant Site and an absolute backup directory")
        env["RESTORE_DIR"] = restore_dir
    if action == "remove":
        env["REMOVE_STOP_ONLY"] = "true" if str(stop_only).lower() in {"1", "true"} else "false"
    state_before = node.state
    started = now_datetime()
    code, stdout, stderr = -1, "", ""
    try:
        code, stdout, stderr = run_remote_script(
            node, _get_ssh_config(node), "node", script, env, "script_execution_log",
            {"execution": script, "node": node.name},
        )
        parsed = parse_output(stdout)
        if code != 0:
            raise frappe.ValidationError(stderr or stdout or f"Bench action exited with {code}")
        if expected_key not in parsed:
            raise frappe.ValidationError(f"Missing script result: {expected_key}")
        if action == "backup":
            if not parsed[expected_key].isdigit():
                raise frappe.ValidationError("Invalid backup count from script")
        elif action != "remove" and parsed[expected_key] != "true":
            raise frappe.ValidationError(f"Script did not confirm {expected_key}")
        if action == "remove":
            if parsed["bench_removed"] == "true":
                node.state, node.status = "Decommissioned", "Retired"
            elif parsed.get("stop_only") == "true":
                node.state, node.status = "Stopped", "Maintenance"
            else:
                raise frappe.ValidationError("Bench removal did not confirm its outcome")
    except Exception as exc:
        node.state, node.status = "Error", "Maintenance"
        _log_node_action(node, f"Bench {action.title()}", "Bench", state_before, node.state,
                         "Failed", code, stdout, stderr or str(exc), started, now_datetime())
        raise
    _log_node_action(node, f"Bench {action.title()}", "Bench", state_before, node.state,
                     "Success", code, stdout, stderr, started, now_datetime())
    return parsed


@frappe.whitelist()
def backup_bench(node_name, tenant_name=None):
    return _run_bench_action(node_name, "backup", tenant_name=tenant_name)


@frappe.whitelist()
def update_bench(node_name):
    return _run_bench_action(node_name, "update")


@frappe.whitelist()
def migrate_bench(node_name, tenant_name=None):
    return _run_bench_action(node_name, "migrate", tenant_name=tenant_name)


@frappe.whitelist()
def restore_backup(node_name, tenant_name, restore_dir):
    return _run_bench_action(node_name, "restore", tenant_name=tenant_name, restore_dir=restore_dir)


@frappe.whitelist()
def remove_bench(node_name, stop_only=False):
    return _run_bench_action(node_name, "remove", stop_only=stop_only)

@frappe.whitelist()
def install_dependencies(node_name):
    node = frappe.get_doc("Infrastructure Node", node_name)

    transition = NODE_TRANSITIONS.get("install_dependencies")
    if not transition:
        frappe.throw("Transition not configured")

    # -------------------------------------------------
    # Allowed entry states (normal + recovery)
    # -------------------------------------------------
    allowed_states = []
    allowed_states.extend(transition.get("from", []))
    allowed_states.extend(transition.get("recover_from", []))

    if node.state not in allowed_states:
        frappe.throw(
            f"Invalid node state '{node.state}' for install_dependencies"
        )

    state_before = node.state
    status_before = node.status

    # -------------------------------------------------
    # Capture start time
    # -------------------------------------------------
    started_at = now_datetime()

    # -------------------------------------------------
    # Execute remote script
    # -------------------------------------------------
    exit_code, stdout, stderr = _execute_ssh_script(
        node, "install_deps.sh"
    )

    # -------------------------------------------------
    # Capture finish time
    # -------------------------------------------------
    finished_at = now_datetime()

    # -------------------------------------------------
    # Resolve transition outcome
    # -------------------------------------------------
    if exit_code == 0:
        next_state, next_status = transition["success"]
        result = "Success"
    else:
        next_state, next_status = transition["failure"]
        result = "Failed"

    # -------------------------------------------------
    # Validate FSM transitions
    # -------------------------------------------------
    _validate_transition(state_before, next_state)
    _validate_status_transition(status_before, next_status)

    # -------------------------------------------------
    # Apply transition
    # -------------------------------------------------
    node.state = next_state
    node.status = next_status
    node.save(ignore_permissions=True)

    # -------------------------------------------------
    # Audit log
    # -------------------------------------------------
    _log_node_action(
        node=node,
        action_name="Install Dependencies",
        action_type="Install",
        state_before=state_before,
        state_after=node.state,
        result=result,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        started_at=started_at,
        finished_at=finished_at,
    )

    # -------------------------------------------------
    # Hard fail on error
    # -------------------------------------------------
    if exit_code != 0:
        frappe.throw(
            "Dependency installation failed. Node moved to Error/Maintenance."
        )

    return "Dependencies installed successfully"

@frappe.whitelist()
def install_bench(node_name):
    node = frappe.get_doc("Infrastructure Node", node_name)

    transition = NODE_TRANSITIONS.get("install_bench")
    if not transition:
        frappe.throw("Transition not configured")

    # -------------------------------------------------
    # Allowed entry states (normal + recovery)
    # -------------------------------------------------
    allowed_states = []
    allowed_states.extend(transition.get("from", []))
    allowed_states.extend(transition.get("recover_from", []))

    if node.state not in allowed_states:
        frappe.throw(
            f"Invalid node state '{node.state}' for install_bench"
        )

    state_before = node.state
    status_before = node.status

    # -------------------------------------------------
    # Capture start time
    # -------------------------------------------------
    started_at = now_datetime()

    # -------------------------------------------------
    # Execute remote script
    # -------------------------------------------------
    exit_code, stdout, stderr = _execute_ssh_script(
        node, "install_bench.sh"
    )

    # -------------------------------------------------
    # Capture finish time
    # -------------------------------------------------
    finished_at = now_datetime()

    # -------------------------------------------------
    # Resolve transition outcome
    # -------------------------------------------------
    if exit_code == 0:
        next_state, next_status = transition["success"]
        result = "Success"
    else:
        next_state, next_status = transition["failure"]
        result = "Failed"

    # -------------------------------------------------
    # Validate FSM transitions
    # -------------------------------------------------
    _validate_transition(state_before, next_state)
    _validate_status_transition(status_before, next_status)

    # -------------------------------------------------
    # Apply transition
    # -------------------------------------------------
    node.state = next_state
    node.status = next_status
    node.save(ignore_permissions=True)

    # -------------------------------------------------
    # Audit log
    # -------------------------------------------------
    _log_node_action(
        node=node,
        action_name="Install Bench",
        action_type="Install",
        state_before=state_before,
        state_after=node.state,
        result=result,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        started_at=started_at,
        finished_at=finished_at,
    )

    # -------------------------------------------------
    # Hard fail on error
    # -------------------------------------------------
    if exit_code != 0:
        frappe.throw(
            "Bench installation failed. Node moved to Error/Maintenance."
        )

    return "Bench installed successfully"

@frappe.whitelist()
def init_project(node_name):
    node = frappe.get_doc("Infrastructure Node", node_name)

    transition = NODE_TRANSITIONS.get("init_project")
    if not transition:
        frappe.throw("Transition not configured")

    # -------------------------------------------------
    # Allowed entry states (normal + recovery)
    # -------------------------------------------------
    allowed_states = []
    allowed_states.extend(transition.get("from", []))
    allowed_states.extend(transition.get("recover_from", []))

    if node.state not in allowed_states:
        frappe.throw(
            f"Invalid node state '{node.state}' for init_project"
        )

    state_before = node.state
    status_before = node.status

    # -------------------------------------------------
    # Capture start time
    # -------------------------------------------------
    started_at = now_datetime()

    # -------------------------------------------------
    # Execute remote script
    # -------------------------------------------------
    exit_code, stdout, stderr = _execute_ssh_script(
        node, "init_project.sh"
    )

    # -------------------------------------------------
    # Capture finish time
    # -------------------------------------------------
    finished_at = now_datetime()

    # -------------------------------------------------
    # Resolve transition outcome
    # -------------------------------------------------
    if exit_code == 0:
        next_state, next_status = transition["success"]
        result = "Success"
    else:
        next_state, next_status = transition["failure"]
        result = "Failed"

    # -------------------------------------------------
    # Validate FSM transitions
    # -------------------------------------------------
    _validate_transition(state_before, next_state)
    _validate_status_transition(status_before, next_status)

    # -------------------------------------------------
    # Apply transition
    # -------------------------------------------------
    node.state = next_state
    node.status = next_status
    node.save(ignore_permissions=True)

    # -------------------------------------------------
    # Audit log (with timing)
    # -------------------------------------------------
    _log_node_action(
        node=node,
        action_name="Initialize Project",
        action_type="Control",
        state_before=state_before,
        state_after=node.state,
        result=result,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        started_at=started_at,
        finished_at=finished_at,
    )

    # -------------------------------------------------
    # Hard fail on error
    # -------------------------------------------------
    if exit_code != 0:
        frappe.throw(
            "Project initialization failed. Node moved to Error/Maintenance."
        )

    return "Project initialized successfully"

@frappe.whitelist()
def prepare_production(node_name):
    node = frappe.get_doc("Infrastructure Node", node_name)

    transition = NODE_TRANSITIONS.get("prepare_production")
    if not transition:
        frappe.throw("Transition not configured")

    # -------------------------------------------------
    # Allowed entry states (normal + recovery)
    # -------------------------------------------------
    allowed_states = []
    allowed_states.extend(transition.get("from", []))
    allowed_states.extend(transition.get("recover_from", []))

    if node.state not in allowed_states:
        frappe.throw(
            f"Invalid node state '{node.state}' for prepare_production"
        )

    state_before = node.state
    status_before = node.status

    # -------------------------------------------------
    # Capture start time
    # -------------------------------------------------
    started_at = now_datetime()

    # -------------------------------------------------
    # Execute remote script
    # -------------------------------------------------
    exit_code, stdout, stderr = _execute_ssh_script(
        node,
        "prepare_production.sh"
    )

    # -------------------------------------------------
    # Capture finish time
    # -------------------------------------------------
    finished_at = now_datetime()

    # -------------------------------------------------
    # Resolve transition outcome
    # -------------------------------------------------
    if exit_code == 0:
        next_state, next_status = transition["success"]
        result = "Success"
    else:
        next_state, next_status = transition["failure"]
        result = "Failed"

    # -------------------------------------------------
    # Validate FSM transitions
    # -------------------------------------------------
    _validate_transition(state_before, next_state)
    _validate_status_transition(status_before, next_status)

    # -------------------------------------------------
    # Apply transition
    # -------------------------------------------------
    node.state = next_state
    node.status = next_status
    node.save(ignore_permissions=True)

    # -------------------------------------------------
    # Audit log (with timing)
    # -------------------------------------------------
    _log_node_action(
        node=node,
        action_name="Prepare Production",
        action_type="Health",
        state_before=state_before,
        state_after=node.state,
        result=result,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        started_at=started_at,
        finished_at=finished_at,
    )

    # -------------------------------------------------
    # Hard fail on error
    # -------------------------------------------------
    if exit_code != 0:
        frappe.throw(
            "Production preparation failed. Node moved to Error/Maintenance."
        )

    return "Production environment prepared successfully"

@frappe.whitelist()
def verify_production(node_name):
    node = frappe.get_doc("Infrastructure Node", node_name)

    transition = NODE_TRANSITIONS.get("verify_production")
    if not transition:
        frappe.throw("Transition not configured")

    # -------------------------------------------------
    # Allowed entry states
    # -------------------------------------------------
    allowed_states = []
    allowed_states.extend(transition.get("from", []))
    allowed_states.extend(transition.get("recover_from", []))

    if node.state not in allowed_states:
        frappe.throw(
            f"Invalid node state '{node.state}' for verify_production"
        )

    state_before = node.state
    status_before = node.status

    # -------------------------------------------------
    # Capture start time
    # -------------------------------------------------
    started_at = now_datetime()

    # -------------------------------------------------
    # Execute verification script
    # -------------------------------------------------
    exit_code, stdout, stderr = _execute_ssh_script(
        node,
        "verify_production.sh"
    )

    # -------------------------------------------------
    # Capture finish time
    # -------------------------------------------------
    finished_at = now_datetime()

    # -------------------------------------------------
    # Resolve outcome
    # -------------------------------------------------
    if exit_code == 0:
        result = "Success"
        next_state, next_status = transition["success"]
    else:
        result = "Failed"
        next_state, next_status = transition["failure"]

    # -------------------------------------------------
    # Apply transition ONLY on failure
    # -------------------------------------------------
    if next_state:
        _validate_transition(state_before, next_state)
        _validate_status_transition(status_before, next_status)

        node.state = next_state
        node.status = next_status
        node.save(ignore_permissions=True)

    # -------------------------------------------------
    # Audit log (with timing)
    # -------------------------------------------------
    _log_node_action(
        node=node,
        action_name="Verify Production Environment",
        action_type="Health",
        state_before=state_before,
        state_after=node.state,
        result=result,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        started_at=started_at,
        finished_at=finished_at,
    )

    # -------------------------------------------------
    # Hard fail on verification error
    # -------------------------------------------------
    if exit_code != 0:
        frappe.throw(
            "Production verification failed. Node moved to Error/Maintenance."
        )

    # Verification returns raw report
    return stdout


@frappe.whitelist()
def start_project(node_name):
    node = frappe.get_doc("Infrastructure Node", node_name)

    transition = NODE_TRANSITIONS.get("start_project")
    if not transition:
        frappe.throw("Transition not configured")

    # -------------------------------------------------
    # Allowed entry states (normal + recovery)
    # -------------------------------------------------
    allowed_states = []
    allowed_states.extend(transition.get("from", []))
    allowed_states.extend(transition.get("recover_from", []))

    if node.state not in allowed_states:
        frappe.throw(
            f"Invalid node state '{node.state}' for start_project"
        )

    state_before = node.state
    status_before = node.status

    # -------------------------------------------------
    # Capture start time
    # -------------------------------------------------
    started_at = now_datetime()

    # -------------------------------------------------
    # Execute remote script
    # -------------------------------------------------
    exit_code, stdout, stderr = _execute_ssh_script(
        node,
        "start_production_services.sh"
    )

    # -------------------------------------------------
    # Capture finish time
    # -------------------------------------------------
    finished_at = now_datetime()

    # -------------------------------------------------
    # Resolve transition outcome
    # -------------------------------------------------
    if exit_code == 0:
        next_state, next_status = transition["success"]
        result = "Success"
    else:
        next_state, next_status = transition["failure"]
        result = "Failed"

    # -------------------------------------------------
    # Validate FSM transitions
    # -------------------------------------------------
    _validate_transition(state_before, next_state)
    _validate_status_transition(status_before, next_status)

    # -------------------------------------------------
    # Apply transition
    # -------------------------------------------------
    node.state = next_state
    node.status = next_status
    node.save(ignore_permissions=True)

    # -------------------------------------------------
    # Audit log (with timing)
    # -------------------------------------------------
    _log_node_action(
        node=node,
        action_name="Start Production Services",
        action_type="Control",
        state_before=state_before,
        state_after=node.state,
        result=result,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        started_at=started_at,
        finished_at=finished_at,
    )

    # -------------------------------------------------
    # Hard fail on error
    # -------------------------------------------------
    if exit_code != 0:
        frappe.throw(
            "Failed to start production services. Node moved to Error/Maintenance."
        )

    return "Production services started successfully"

@frappe.whitelist()
def stop_project(node_name):
    node = frappe.get_doc("Infrastructure Node", node_name)

    transition = NODE_TRANSITIONS.get("stop_project")
    if not transition:
        frappe.throw("Transition not configured")

    # -------------------------------------------------
    # Allowed entry states
    # -------------------------------------------------
    allowed_states = []
    allowed_states.extend(transition.get("from", []))
    allowed_states.extend(transition.get("recover_from", []))

    if node.state not in allowed_states:
        frappe.throw(
            f"Invalid node state '{node.state}' for stop_project"
        )

    state_before = node.state
    status_before = node.status

    # -------------------------------------------------
    # Capture start time
    # -------------------------------------------------
    started_at = now_datetime()

    # -------------------------------------------------
    # Execute remote script
    # -------------------------------------------------
    exit_code, stdout, stderr = _execute_ssh_script(
        node,
        "stop_production_services.sh"
    )

    # -------------------------------------------------
    # Capture finish time
    # -------------------------------------------------
    finished_at = now_datetime()

    # -------------------------------------------------
    # Resolve transition outcome
    # -------------------------------------------------
    if exit_code == 0:
        next_state, next_status = transition["success"]
        result = "Success"
    else:
        next_state, next_status = transition["failure"]
        result = "Failed"

    # -------------------------------------------------
    # Validate FSM transitions
    # -------------------------------------------------
    _validate_transition(state_before, next_state)
    _validate_status_transition(status_before, next_status)

    # -------------------------------------------------
    # Apply transition
    # -------------------------------------------------
    node.state = next_state
    node.status = next_status
    node.save(ignore_permissions=True)

    # -------------------------------------------------
    # Audit log (with timing)
    # -------------------------------------------------
    _log_node_action(
        node=node,
        action_name="Stop Production Services",
        action_type="Control",
        state_before=state_before,
        state_after=node.state,
        result=result,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        started_at=started_at,
        finished_at=finished_at,
    )

    # -------------------------------------------------
    # Hard fail on error
    # -------------------------------------------------
    if exit_code != 0:
        frappe.throw(
            "Failed to stop production services. Node moved to Error/Maintenance."
        )

    return "Production services stopped successfully"
