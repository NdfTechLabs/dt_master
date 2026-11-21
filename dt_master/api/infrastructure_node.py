import datetime
import json
import uuid

import frappe
from frappe.utils import now_datetime

from dt_master.api.script_contract import framework_environment, parse_output, run_remote_script, secret
from dt_master.docker_readiness import evaluate_node_docker_readiness
from dt_master.operations.coordinator import start_operation

ALLOWED_TRANSITIONS = {
	"Declared": ["SSH Ready", "Error"],
	"SSH Ready": ["Deps Installed", "Error"],
	"Deps Installed": ["Project Installed", "Error"],
	"Project Installed": ["Initialized", "Error"],
	"Initialized": ["Healthy", "Error"],
	"Healthy": ["Running", "Error"],
	"Running": ["Stopped", "Error"],
	"Stopped": ["Running", "Error"],
	"Error": [
		"Error",
		"Stopped",
		"Running",
		"Healthy",
		"Initialized",
		"Project Installed",
		"Deps Installed",
		"SSH Ready",
		"Declared",
	],
}
ALLOWED_STATUS_TRANSITIONS = {
	"Provisioning": ["Active", "Maintenance", "Provisioning"],
	"Active": ["Maintenance", "Retired", "Active"],
	"Maintenance": ["Provisioning", "Active", "Retired", "Maintenance"],
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
		"from": ["Healthy", "Running", "Stopped", "SSH Ready"],
		"recover_from": [],
		"success": (None, None),  # no state/status change
		"failure": ("Error", "Maintenance"),
	},
}


def _validate_status_transition(current, next_):
	allowed = ALLOWED_STATUS_TRANSITIONS.get(current, [])
	if next_ not in allowed:
		frappe.throw(f"Invalid status transition: '{current}' → '{next_}'")


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

	host_alias = getattr(node, "ssh_host_alias", None) or None
	if not host_alias and (not node.ip_address or not node.ssh_user):
		frappe.throw("Incomplete SSH configuration")
	if not host_alias and not node.ssh_user:
		frappe.throw("SSH user is required for direct connections")

	return {
		"host": (node.ip_address or "").strip(),
		"user": node.ssh_user,
		"port": node.ssh_port or 22,
		"host_alias": host_alias,
	}


def _get_node_dependencies(node):
	deps = frappe.get_all(
		"Framework Version Dependency",
		filters={"framework_version": node.frappe_version, "enabled": 1},
		fields=["dependency", "install_order"],
		order_by="install_order asc",
	)

	result = []

	for d in deps:
		dep = frappe.get_doc("System Dependency", d.dependency)

		result.append(
			{
				"name": dep.name,
				"install_method": dep.install_method,
				"group": dep.dependency_group,
				"version": getattr(dep, "version", None),
				"repository": dep.repository,
			}
		)

	return result


def _execute_ssh_script(node, script_name):
	ssh = _get_ssh_config(node)
	env = framework_environment(node)
	env["SYSTEM_DEPENDENCIES"] = json.dumps(_get_node_dependencies(node))
	return run_remote_script(
		node,
		ssh,
		"node",
		script_name,
		env,
		"script_execution_log",
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
	finished_at=None,
):
	node.append(
		"node_action_logs",
		{
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
			"stderr": stderr,
		},
	)

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
		env.update(
			{
				"SITE_NAME": tenant.fqdn,
				"MYSQL_ROOT_USER": tenant.mysql_root_user_name,
				"MYSQL_ROOT_PASSWORD": secret(tenant, "mysql_root_user_password"),
			}
		)
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
			node,
			_get_ssh_config(node),
			"node",
			script,
			env,
			"script_execution_log",
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
		_log_node_action(
			node,
			f"Bench {action.title()}",
			"Bench",
			state_before,
			node.state,
			"Failed",
			code,
			stdout,
			stderr or str(exc),
			started,
			now_datetime(),
		)
		raise
	_log_node_action(
		node,
		f"Bench {action.title()}",
		"Bench",
		state_before,
		node.state,
		"Success",
		code,
		stdout,
		stderr,
		started,
		now_datetime(),
	)
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
		frappe.throw(f"Invalid node state '{node.state}' for install_dependencies")

	state_before = node.state
	status_before = node.status

	# -------------------------------------------------
	# Capture start time
	# -------------------------------------------------
	started_at = now_datetime()

	# -------------------------------------------------
	# Execute remote script
	# -------------------------------------------------
	exit_code, stdout, stderr = _execute_ssh_script(node, "install_deps.sh")

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
		frappe.throw("Dependency installation failed. Node moved to Error/Maintenance.")

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
		frappe.throw(f"Invalid node state '{node.state}' for install_bench")

	state_before = node.state
	status_before = node.status

	# -------------------------------------------------
	# Capture start time
	# -------------------------------------------------
	started_at = now_datetime()

	# -------------------------------------------------
	# Execute remote script
	# -------------------------------------------------
	exit_code, stdout, stderr = _execute_ssh_script(node, "install_bench.sh")

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
		frappe.throw("Bench installation failed. Node moved to Error/Maintenance.")

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
		frappe.throw(f"Invalid node state '{node.state}' for init_project")

	state_before = node.state
	status_before = node.status

	# -------------------------------------------------
	# Capture start time
	# -------------------------------------------------
	started_at = now_datetime()

	# -------------------------------------------------
	# Execute remote script
	# -------------------------------------------------
	exit_code, stdout, stderr = _execute_ssh_script(node, "init_project.sh")

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
		frappe.throw("Project initialization failed. Node moved to Error/Maintenance.")

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
		frappe.throw(f"Invalid node state '{node.state}' for prepare_production")

	state_before = node.state
	status_before = node.status

	# -------------------------------------------------
	# Capture start time
	# -------------------------------------------------
	started_at = now_datetime()

	# -------------------------------------------------
	# Execute remote script
	# -------------------------------------------------
	exit_code, stdout, stderr = _execute_ssh_script(node, "prepare_production.sh")

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
		frappe.throw("Production preparation failed. Node moved to Error/Maintenance.")

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
		frappe.throw(f"Invalid node state '{node.state}' for verify_production")

	state_before = node.state
	status_before = node.status

	# -------------------------------------------------
	# Capture start time
	# -------------------------------------------------
	started_at = now_datetime()

	# -------------------------------------------------
	# Execute verification script
	# -------------------------------------------------
	exit_code, stdout, stderr = _execute_ssh_script(node, "verify_production.sh")

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
		frappe.throw("Production verification failed. Node moved to Error/Maintenance.")

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
		frappe.throw(f"Invalid node state '{node.state}' for start_project")

	state_before = node.state
	status_before = node.status

	# -------------------------------------------------
	# Capture start time
	# -------------------------------------------------
	started_at = now_datetime()

	# -------------------------------------------------
	# Execute remote script
	# -------------------------------------------------
	exit_code, stdout, stderr = _execute_ssh_script(node, "start_production_services.sh")

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
		frappe.throw("Failed to start production services. Node moved to Error/Maintenance.")

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
		frappe.throw(f"Invalid node state '{node.state}' for stop_project")

	state_before = node.state
	status_before = node.status

	# -------------------------------------------------
	# Capture start time
	# -------------------------------------------------
	started_at = now_datetime()

	# -------------------------------------------------
	# Execute remote script
	# -------------------------------------------------
	exit_code, stdout, stderr = _execute_ssh_script(node, "stop_production_services.sh")

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
		frappe.throw("Failed to stop production services. Node moved to Error/Maintenance.")

	return "Production services stopped successfully"


DOCKER_INSPECTION_SCHEMA_VERSION = "1.0.0"


def _docker_ssh_config(node):
	"""Reuse the Infrastructure Node SSH identity and managed OpenSSH alias."""
	node.check_permission("write")
	if not node.access_verified:
		frappe.throw("Node access not verified")
	host_alias = getattr(node, "ssh_host_alias", None) or None
	if not host_alias and (not node.ip_address or not node.ssh_user):
		frappe.throw("Incomplete SSH configuration")
	return {
		"host": (node.ip_address or "").strip(),
		"user": node.ssh_user,
		"port": node.ssh_port or 22,
		"host_alias": host_alias,
	}


def _docker_failure_snapshot(status, message, duration_ms=0):
	observed = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
	return {
		"schema_version": DOCKER_INSPECTION_SCHEMA_VERSION,
		"observed_at": observed,
		"duration_ms": max(0, int(duration_ms)),
		"inspection_status": status,
		"host": {"cpu": {"logical_cores": None}, "memory": {"total_bytes": None, "available_bytes": None}},
		"docker_cli": {"installed": False, "path": None, "client_version": None},
		"engine": {
			"availability": "Unknown",
			"server_version": None,
			"api_version": None,
			"minimum_api_version": None,
		},
		"compose": {"installed": False, "version": None, "compatibility": "Unknown"},
		"storage": {
			"driver": None,
			"data_root": None,
			"filesystem": {
				"mount_point": None,
				"total_bytes": None,
				"available_bytes": None,
				"used_bytes": None,
			},
		},
		"permissions": {"execution_identity": None, "inspection_access": "Unknown"},
		"host_inventory": {
			"schema_version": "1.0.0", "observed_at": None,
			"host_os": {key: None for key in ("id", "id_like", "name", "version_id", "version_codename", "pretty_name", "architecture", "kernel_release")},
			"package_manager": "Unknown", "docker_packages": [],
			"services": {"manager": "Unknown", "docker": "Unknown", "containerd": "Unknown"},
			"workloads": {"available": False, "running_containers": None, "total_containers": None},
		},
		"diagnostics": [
			{
				"code": "INSPECTION_" + status.upper().replace(" ", "_"),
				"severity": "Error",
				"message": str(message)[:500],
			}
		],
	}


def _validate_docker_snapshot(snapshot):
	"""Validate the versioned payload and reject unexpected or malformed data."""
	if not isinstance(snapshot, dict) or snapshot.get("schema_version") != DOCKER_INSPECTION_SCHEMA_VERSION:
		raise ValueError("Unsupported Docker inspection snapshot schema")
	required = {
		"schema_version",
		"observed_at",
		"duration_ms",
		"inspection_status",
		"host",
		"docker_cli",
		"engine",
		"compose",
		"storage",
		"permissions",
		"diagnostics",
	}
	optional_legacy = {"readiness", "host_inventory"}
	if not required.issubset(snapshot) or set(snapshot) - required - optional_legacy:
		raise ValueError("Docker inspection snapshot has an invalid top-level contract")
	observed_at = snapshot["observed_at"]
	if not isinstance(observed_at, str):
		raise ValueError("Invalid Docker inspection timestamp")
	try:
		datetime.datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
	except ValueError as exc:
		raise ValueError("Invalid Docker inspection timestamp") from exc
	if snapshot["inspection_status"] not in {"Successful", "Partial", "Failed", "Timed Out"}:
		raise ValueError("Invalid Docker inspection status")
	# Accept the deprecated field only on legacy payloads. It is never
	# consumed as readiness evidence; DT Master evaluates readiness separately.
	if "readiness" in snapshot and snapshot["readiness"] not in {"Unknown", "Ready", "Degraded", "Not Ready"}:
		raise ValueError("Invalid legacy Docker readiness value")
	if not isinstance(snapshot["duration_ms"], int) or snapshot["duration_ms"] < 0:
		raise ValueError("Invalid Docker inspection duration")

	def exact_keys(value, keys, label):
		if not isinstance(value, dict) or set(value) != set(keys):
			raise ValueError(f"Invalid Docker inspection section: {label}")

	def nullable_string(value):
		return value is None or isinstance(value, str)

	exact_keys(snapshot["host"], ("cpu", "memory"), "host")
	exact_keys(snapshot["host"]["cpu"], ("logical_cores",), "host.cpu")
	exact_keys(snapshot["host"]["memory"], ("total_bytes", "available_bytes"), "host.memory")
	exact_keys(snapshot["docker_cli"], ("installed", "path", "client_version"), "docker_cli")
	exact_keys(
		snapshot["engine"], ("availability", "server_version", "api_version", "minimum_api_version"), "engine"
	)
	exact_keys(snapshot["compose"], ("installed", "version", "compatibility"), "compose")
	exact_keys(snapshot["storage"], ("driver", "data_root", "filesystem"), "storage")
	exact_keys(
		snapshot["storage"]["filesystem"],
		("mount_point", "total_bytes", "available_bytes", "used_bytes"),
		"storage.filesystem",
	)
	exact_keys(snapshot["permissions"], ("execution_identity", "inspection_access"), "permissions")

	if "host_inventory" in snapshot:
		inventory = snapshot["host_inventory"]
		exact_keys(inventory, ("schema_version", "observed_at", "host_os", "package_manager", "docker_packages", "services", "workloads"), "host_inventory")
		if inventory["schema_version"] != "1.0.0":
			raise ValueError("Unsupported Docker host inventory schema")
		if inventory["observed_at"] is not None and not isinstance(inventory["observed_at"], str):
			raise ValueError("Invalid host inventory timestamp")
		exact_keys(inventory["host_os"], ("id", "id_like", "name", "version_id", "version_codename", "pretty_name", "architecture", "kernel_release"), "host_inventory.host_os")
		for value in inventory["host_os"].values():
			if value is not None and not isinstance(value, str):
				raise ValueError("Invalid host OS inventory value")
		if inventory["package_manager"] not in {"dpkg", "rpm", "apk", "pacman", "Unknown"}:
			raise ValueError("Invalid host inventory package manager")
		if not isinstance(inventory["docker_packages"], list) or len(inventory["docker_packages"]) > 32:
			raise ValueError("Invalid Docker package inventory")
		allowed_packages = {"docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin", "docker.io", "docker-compose", "docker", "docker-ce-rootless-extras"}
		for package in inventory["docker_packages"]:
			exact_keys(package, ("name", "version", "status"), "host_inventory.docker_packages[]")
			if package["name"] not in allowed_packages or not isinstance(package["version"], str) or package["status"] not in {"installed", "unknown"}:
				raise ValueError("Invalid Docker package inventory row")
		exact_keys(inventory["services"], ("manager", "docker", "containerd"), "host_inventory.services")
		if inventory["services"]["manager"] not in {"systemd", "Unknown"} or any(inventory["services"][key] not in {"active", "inactive", "failed", "activating", "deactivating", "Unknown"} for key in ("docker", "containerd")):
			raise ValueError("Invalid Docker service inventory")
		exact_keys(inventory["workloads"], ("available", "running_containers", "total_containers"), "host_inventory.workloads")
		if not isinstance(inventory["workloads"]["available"], bool):
			raise ValueError("Invalid Docker workload availability")
		for key in ("running_containers", "total_containers"):
			value = inventory["workloads"][key]
			if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
				raise ValueError("Invalid Docker workload count")
		if inventory["workloads"]["available"] and (inventory["workloads"]["running_containers"] is None or inventory["workloads"]["total_containers"] is None):
			raise ValueError("Available Docker workload inventory requires both counts")


	cpu = snapshot["host"]["cpu"]["logical_cores"]
	if cpu is not None and (not isinstance(cpu, int) or cpu < 1):
		raise ValueError("Invalid logical CPU count")
	for value in (
		snapshot["host"]["memory"]["total_bytes"],
		snapshot["host"]["memory"]["available_bytes"],
		snapshot["storage"]["filesystem"]["total_bytes"],
		snapshot["storage"]["filesystem"]["available_bytes"],
		snapshot["storage"]["filesystem"]["used_bytes"],
	):
		if value is not None and (not isinstance(value, int) or value < 0):
			raise ValueError("Invalid byte measurement")
	for value in (
		snapshot["docker_cli"]["path"],
		snapshot["docker_cli"]["client_version"],
		snapshot["engine"]["server_version"],
		snapshot["engine"]["api_version"],
		snapshot["engine"]["minimum_api_version"],
		snapshot["compose"]["version"],
		snapshot["storage"]["driver"],
		snapshot["storage"]["data_root"],
		snapshot["storage"]["filesystem"]["mount_point"],
		snapshot["permissions"]["execution_identity"],
	):
		if not nullable_string(value):
			raise ValueError("Invalid nullable string in Docker inspection snapshot")
	if not isinstance(snapshot["docker_cli"]["installed"], bool) or not isinstance(
		snapshot["compose"]["installed"], bool
	):
		raise ValueError("Invalid Docker component installed flag")
	if snapshot["engine"]["availability"] not in {"Available", "Unavailable", "Not Installed", "Unknown"}:
		raise ValueError("Invalid Docker Engine availability")
	if snapshot["compose"]["compatibility"] not in {"Compatible", "Incompatible", "Unavailable", "Unknown"}:
		raise ValueError("Invalid Docker Compose compatibility")
	if snapshot["permissions"]["inspection_access"] not in {"Allowed", "Denied", "Partial", "Unknown"}:
		raise ValueError("Invalid Docker inspection access")
	if not isinstance(snapshot["diagnostics"], list):
		raise ValueError("Invalid Docker inspection diagnostics")
	for diagnostic in snapshot["diagnostics"]:
		exact_keys(diagnostic, ("code", "severity", "message"), "diagnostics[]")
		if not all(
			isinstance(diagnostic[key], str) and diagnostic[key] for key in ("code", "severity", "message")
		):
			raise ValueError("Invalid Docker diagnostic fields")
		if diagnostic["severity"] not in {"Info", "Warning", "Error"}:
			raise ValueError("Invalid Docker diagnostic severity")
	return snapshot


DOCKER_OBSERVATIONS = {
	"privileges": "host.node.docker.privileges.check",
	"inspect": "host.node.docker.inspect",
	"preflight": "host.node.docker.preflight",
	"updates": "host.node.docker.updates.check",
	"verify": "host.node.docker.verify",
}


@frappe.whitelist(methods=["POST"])
def run_docker_observation(node_name, observation):
	"""Queue one allowlisted, read-only Docker observation through the durable coordinator."""
	frappe.only_for("System Manager")
	operation_type = DOCKER_OBSERVATIONS.get(observation)
	if not operation_type:
		frappe.throw("Unsupported Docker observation.", frappe.ValidationError)
	node = frappe.get_doc("Infrastructure Node", node_name)
	node.check_permission("read")
	if not node.access_verified:
		frappe.throw("Node SSH access must be verified before Docker inspection.")
	return start_operation(
		operation_type=operation_type,
		target_doctype="Infrastructure Node",
		target_name=node.name,
		payload={},
		idempotency_key=f"docker-{observation}:{uuid.uuid4().hex}",
	)


@frappe.whitelist(methods=["POST"])
def inspect_docker_runtime(node_name):
	"""Queue read-only Docker inspection through the durable Docker adapter."""
	frappe.only_for("System Manager")
	node = frappe.get_doc("Infrastructure Node", node_name)
	node.check_permission("read")
	if not node.access_verified:
		frappe.throw("Node SSH access must be verified before Docker inspection.")
	return start_operation(
		operation_type="runtime.adapter.inspect",
		target_doctype="Infrastructure Node",
		target_name=node.name,
		payload={
			"adapter_id": "docker-compose-v1",
			"runtime_type": "docker",
			"method": "inspect",
			"payload": {},
		},
		idempotency_key=f"docker-inspect:{node.name}:{uuid.uuid4()}",
	)


@frappe.whitelist(methods=["POST"])
def check_system_status(node_name):
	"""Queue a read-only Host inspection through the durable operation manager."""
	node = frappe.get_doc("Infrastructure Node", node_name)
	node.check_permission("read")
	if not node.access_verified:
		frappe.throw("Node SSH access must be verified before system inspection.")
	return start_operation(
		operation_type="runtime.adapter.inspect",
		target_doctype="Infrastructure Node",
		target_name=node.name,
		payload={
			"adapter_id": "host-v1",
			"runtime_type": "host",
			"method": "inspect",
			"payload": {},
		},
		idempotency_key=f"host-inspect:{node.name}:{uuid.uuid4()}",
	)
