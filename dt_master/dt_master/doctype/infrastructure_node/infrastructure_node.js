frappe.ui.form.on("Infrastructure Node", {
	refresh(frm) {
		if (frm.is_new()) return;

		const { status, state } = frm.doc;

		const actions = {
			install_dependencies: {
				label: "Install Dependencies",
				statuses: ["Provisioning", "Maintenance"],
				states: ["SSH Ready", "Error"],
				method: "dt_master.api.infrastructure_node.install_dependencies",
			},
			install_bench: {
				label: "Install Bench",
				statuses: ["Provisioning", "Maintenance"],
				states: ["Deps Installed", "Error"],
				method: "dt_master.api.infrastructure_node.install_bench",
			},
			initialize_project: {
				label: "Initialize Project",
				statuses: ["Provisioning", "Maintenance"],
				states: ["Project Installed", "Error"],
				method: "dt_master.api.infrastructure_node.init_project",
			},
			prepare_project: {
				label: "Prepare Project",
				statuses: ["Provisioning", "Maintenance"],
				states: ["Initialized", "Error"],
				method: "dt_master.api.infrastructure_node.prepare_production",
			},
			start_project: {
				label: "Start Project",
				statuses: ["Active", "Maintenance"],
				states: ["Healthy", "Stopped"],
				method: "dt_master.api.infrastructure_node.start_project",
			},
			stop_project: {
				label: "Stop Project",
				statuses: ["Active", "Maintenance"],
				states: ["Running"],
				method: "dt_master.api.infrastructure_node.stop_project",
			},
		};

		Object.values(actions).forEach((cfg) => {
			if (cfg.statuses.includes(status) && cfg.states.includes(state)) {
				frm.add_custom_button(
					__(cfg.label),
					() => run_node_action(frm, cfg.label, cfg.method),
					__("Node")
				);
			}
		});
		render_docker_readiness_summary(frm);

		if (
			frappe.user_roles.includes("System Manager") &&
			["Initialized", "Healthy", "Running", "Stopped", "Error"].includes(state)
		) {
			for (const [method, label] of Object.entries({
				backup_bench: "Backup Bench",
				update_bench: "Update Bench",
				migrate_bench: "Migrate Bench",
				remove_bench: "Remove Bench",
			})) {
				frm.add_custom_button(
					__(label),
					() => {
						frappe.confirm(
							__(
								method === "remove_bench"
									? "Remove this bench and its virtualenv? Databases will be kept."
									: "Run this bench operation? Updates and migrations take backups first."
							),
							() => run_bench_operation(frm, method)
						);
					},
					__("Bench")
				);
			}
			frm.add_custom_button(
				__("Restore Backup"),
				() => {
					frappe.prompt(
						[
							{
								fieldname: "tenant_name",
								fieldtype: "Link",
								options: "Tenant Site",
								label: "Tenant Site",
								reqd: 1,
								get_query: () => ({ filters: { node: frm.doc.name } }),
							},
							{
								fieldname: "restore_dir",
								fieldtype: "Data",
								label: "Absolute backup directory on node",
								reqd: 1,
							},
						],
						(values) =>
							frappe.confirm(
								__("Replace the site's database and files with this backup?"),
								() => run_bench_operation(frm, "restore_backup", values)
							),
						__("Restore Backup")
					);
				},
				__("Bench")
			);
		}
	},
});

function test_node_connection(frm) {
	frappe.call({
		method: "dt_master.api.infrastructure_node.test_node_connection",
		args: { node_name: frm.doc.name },
		freeze: true,
		freeze_message: __("Queueing SSH connection test..."),
		callback(response) {
			const operation = response.message || {};
			frappe.show_alert({
				message: __(
					"Node connection test queued. Operation: {0}. Follow progress and results in Task Manager.",
					[operation.operation_id || __("unknown")]
				),
				indicator: "blue",
			});
		},
		error() {
			frappe.msgprint({
				title: __("Node Connection Test Not Started"),
				message: __(
					"The SSH connection test could not be queued. Check the server error log."
				),
				indicator: "red",
			});
		},
	});
}

function run_docker_privilege_bootstrap(frm) {
	frappe.call({
		method: "dt_master.api.infrastructure_node.bootstrap_docker_privileges",
		args: { node_name: frm.doc.name },
		freeze: true,
		freeze_message: __("Queueing Docker privilege bootstrap..."),
		callback(response) {
			const operation = response.message || {};
			frappe.show_alert({
				message: __(
					"Docker privilege bootstrap queued. Operation: {0}. Follow progress and results in Task Manager.",
					[operation.operation_id || __("unknown")]
				),
				indicator: "blue",
			});
		},
		error() {
			frappe.msgprint({
				title: __("Docker Privilege Bootstrap Not Started"),
				message: __(
					"The bootstrap operation could not be queued. Check the server error log."
				),
				indicator: "red",
			});
		},
	});
}

function run_docker_engine_install(frm) {
	frappe.call({
		method: "dt_master.api.infrastructure_node.install_docker_engine",
		args: { node_name: frm.doc.name },
		freeze: true,
		freeze_message: __("Queueing Docker Engine installation..."),
		callback(response) {
			const operation = response.message || {};
			frappe.show_alert({
				message: __(
					"Docker Engine installation queued. Operation: {0}. Follow progress and verification in Task Manager.",
					[operation.operation_id || __("unknown")]
				),
				indicator: "blue",
			});
		},
		error() {
			frappe.msgprint({
				title: __("Docker Engine Installation Not Started"),
				message: __("The installation could not be queued. Check the server error log."),
				indicator: "red",
			});
		},
	});
}

function run_docker_observation(frm, observation, label) {
	frappe.call({
		method: "dt_master.api.infrastructure_node.run_docker_observation",
		args: { node_name: frm.doc.name, observation },
		freeze: true,
		freeze_message: __("Submitting {0}...", [__(label)]),
		callback(response) {
			const operation = response.message || {};
			frappe.show_alert({
				message: __(
					"{0} queued. Operation: {1}. Follow progress and results in Task Manager.",
					[__(label), operation.operation_id || __("unknown")]
				),
				indicator: "blue",
			});
		},
		error() {
			frappe.msgprint({
				title: __("{0} Not Started", [__(label)]),
				message: __(
					"The read-only Docker observation could not be queued. Check the server error log."
				),
				indicator: "red",
			});
		},
	});
}

function docker_setup_state(frm) {
	const management = parse_json_field(frm.doc.docker_host_management_snapshot) || {};
	const hostInspection = management.host_inspection || {};
	const privilegeBootstrap = management.privilege_bootstrap || {};
	const privilegeReadiness = management.privilege_readiness || {};
	const installPlan = management.docker_install_plan || {};
	const state = frm.doc.docker_host_state || "Not Evaluated";
	const sshConfigured = Boolean(
		frm.doc.ssh_host_alias || (frm.doc.ip_address && frm.doc.ssh_user)
	);
	const connected =
		management.connection_status === "Connected" &&
		Boolean(management.connection_operation_id || management.operation_id);
	const hostSupported =
		hostInspection.inspection_status === "Successful" &&
		hostInspection.os_support_status === "Supported" &&
		Boolean(hostInspection.operation_id);
	const helperInstalled =
		privilegeBootstrap.status === "Succeeded" &&
		Boolean(privilegeBootstrap.operation_id);
	const privilegesReady =
		privilegeReadiness.status === "Ready" &&
		Boolean(privilegeReadiness.operation_id);
	const inspectionSuccessful = frm.doc.docker_inspection_status === "Successful";
	const planStatus = installPlan.status || null;

	let action = null;
	let blockedReason = null;
	if (!connected || state === "Not Evaluated" || state === "Connection Required") {
		if (sshConfigured) {
			action = { key: "test_connection", label: "Test Node Connection" };
		} else {
			blockedReason = "Configure an SSH host alias, or an IP address and SSH user, before testing the connection.";
		}
	} else if (state === "Host Inspection Required") {
		action = { key: "inspect_host", label: "Inspect Host" };
	} else if (state === "Privilege Setup Required") {
		if (!hostSupported) {
			blockedReason = "Docker privilege setup is locked because Host inspection has not confirmed a supported operating system.";
		} else if (!helperInstalled) {
			action = { key: "bootstrap_privileges", label: "Install Docker Privilege Helper" };
		} else {
			action = { key: "check_privileges", label: "Check Docker Privileges" };
		}
	} else if (state === "Docker Inspection Required") {
		if (!privilegesReady) {
			blockedReason = "Docker runtime inspection is locked until trusted privilege verification succeeds.";
		} else if (!inspectionSuccessful) {
			action = { key: "inspect_docker", label: "Inspect Docker Runtime" };
		} else {
			action = { key: "preflight", label: "Run Docker Preflight" };
		}
	} else if (state === "Repository Preparation Required") {
		if (planStatus === "preparation_required") {
			action = { key: "prepare_repository", label: "Prepare Docker Repository" };
		} else {
			blockedReason = "The persisted install plan does not authorize repository preparation. Refresh the form and review the latest operation result.";
		}
	} else if (state === "Ready for Install") {
		if (planStatus === "ready") {
			action = { key: "install_engine", label: "Install Docker Engine" };
		} else {
			blockedReason = "A current install plan with status Ready is required before Engine installation.";
		}
	} else if (state === "Engine Ready") {
		action = { key: "verify_runtime", label: "Verify Docker Runtime" };
	} else if (state === "Operation In Progress") {
		blockedReason = "A Docker lifecycle operation is in progress. Wait for it to finish in Task Manager, then refresh this form.";
	} else if (state === "Reconciliation Required" || state === "Failed") {
		blockedReason = "The last lifecycle operation requires investigation or reconciliation. Review its result in Task Manager before retrying.";
	} else {
		blockedReason = "The next Docker lifecycle action is not currently available.";
	}

	return {
		management,
		state,
		connected,
		hostSupported,
		helperInstalled,
		privilegesReady,
		inspectionSuccessful,
		planStatus,
		installPlan,
		action,
		blockedReason,
	};
}

function docker_setup_progress_html(frm, setup) {
	const steps = [
		{ label: "SSH connection", done: setup.connected, current: setup.action?.key === "test_connection" },
		{ label: "Host inspection", done: setup.hostSupported, current: setup.action?.key === "inspect_host" },
		{ label: "Privilege helper", done: setup.helperInstalled, current: setup.action?.key === "bootstrap_privileges" },
		{ label: "Privilege verification", done: setup.privilegesReady, current: setup.action?.key === "check_privileges" },
		{ label: "Docker runtime inspection", done: setup.inspectionSuccessful, current: setup.action?.key === "inspect_docker" },
		{ label: "Install preflight", done: ["ready", "preparation_required"].includes(setup.planStatus), current: setup.action?.key === "preflight" },
		{ label: "Repository preparation (if required)", resolved: setup.planStatus === "ready" && ["Ready for Install", "Engine Ready"].includes(setup.state), current: setup.action?.key === "prepare_repository" },
		{ label: "Docker Engine installation", done: setup.state === "Engine Ready", current: setup.action?.key === "install_engine" },
	];
	const stepsHtml = steps.map((step, index) => {
		const status = step.done ? "Completed" : step.resolved ? "Resolved" : step.current ? "Next" : "Locked";
		const indicator = step.done || step.resolved ? "green" : step.current ? "blue" : "gray";
		return "<li class=\"mb-1\"><span class=\"indicator-pill " + indicator + "\">" +
			__(status) + "</span> <span>" + (index + 1) + ". " + __(step.label) + "</span></li>";
	}).join("");
	const planHtml = setup.installPlan.status
		? "<div class=\"small text-muted mt-2\">" + __("Latest install plan") + ": <strong>" +
			escape_html(setup.installPlan.status) + "</strong>" +
			(setup.installPlan.observed_at ? " · " + escape_html(setup.installPlan.observed_at) : "") +
			(setup.installPlan.plan_digest ? "<br>" + __("Plan digest") + ": " + escape_html(setup.installPlan.plan_digest) : "") +
			"</div>"
		: "<div class=\"small text-muted mt-2\">" + __("No validated Docker install plan is recorded yet.") + "</div>";
	const actionHtml = setup.action
		? "<button type=\"button\" class=\"btn btn-primary btn-sm mt-3\" data-docker-action=\"" +
			escape_html(setup.action.key) + "\">" + __(setup.action.label) + "</button>"
		: "<div class=\"alert alert-warning mt-3 mb-0\">" +
			escape_html(setup.blockedReason || __("No action is currently available.")) + "</div>";
	const roleHtml = frappe.user_roles.includes("System Manager")
		? actionHtml
		: "<div class=\"text-muted small mt-3\">" + __("Docker lifecycle actions are available to System Managers.") + "</div>";
	const secondaryActionsHtml = frappe.user_roles.includes("System Manager") && setup.state === "Engine Ready"
		? "<button type=\"button\" class=\"btn btn-default btn-sm mt-3 ml-2\" data-docker-action=\"updates\">" + __("Check Docker Updates") + "</button>"
		: "";
	return "<div class=\"border rounded p-3 mb-3\">" +
		"<div class=\"d-flex justify-content-between align-items-center mb-2\">" +
		"<strong>" + __("Guided Docker Setup") + "</strong>" +
		"<span class=\"indicator-pill " +
		(setup.state === "Engine Ready" ? "green" : setup.state === "Failed" || setup.state === "Reconciliation Required" ? "red" : "blue") +
		"\">" + escape_html(setup.state) + "</span></div>" +
		"<ol class=\"pl-3 mb-2\">" + stepsHtml + "</ol>" + planHtml + roleHtml + secondaryActionsHtml +
		"<button type=\"button\" class=\"btn btn-default btn-xs mt-2\" data-docker-action=\"refresh\">" +
		__("Refresh status") + "</button>" +
		"<div class=\"small text-muted mt-2\">" +
		__("Each step unlocks only after the preceding operation has produced the required persisted evidence. The server revalidates every action.") +
		"</div></div>";
}

function run_docker_setup_action(frm, action) {
	switch (action) {
		case "test_connection":
			return test_node_connection(frm);
		case "inspect_host":
			return run_node_action(frm, "Inspect Host", "dt_master.api.infrastructure_node.inspect_node");
		case "bootstrap_privileges":
			return run_docker_privilege_bootstrap(frm);
		case "check_privileges":
			return run_docker_observation(frm, "privileges", "Check Docker Privileges");
		case "inspect_docker":
			return run_docker_observation(frm, "inspect", "Inspect Docker Runtime");
		case "preflight":
			return run_docker_observation(frm, "preflight", "Run Docker Preflight");
		case "prepare_repository":
			return run_docker_repository_prepare(frm);
		case "install_engine":
			return run_docker_engine_install(frm);
		case "verify_runtime":
			return run_docker_observation(frm, "verify", "Verify Docker Runtime");
		case "updates":
			return run_docker_observation(frm, "updates", "Check Docker Updates");
		case "refresh":
			return frm.reload_doc();
		default:
			return;
	}
}

function run_docker_repository_prepare(frm) {
	frappe.confirm(
		__("Prepare the official Docker package repository and refresh trusted package metadata? This does not install Docker Engine."),
		() => {
			frappe.call({
				method: "dt_master.api.infrastructure_node.prepare_docker_repository",
				args: { node_name: frm.doc.name },
				freeze: true,
				freeze_message: __("Queueing Docker repository preparation..."),
				callback(response) {
					const operation = response.message || {};
					frappe.show_alert({
						message: __(
							"Docker repository preparation queued. Operation: {0}. Follow progress and results in Task Manager, then refresh this form.",
							[operation.operation_id || __("unknown")]
						),
						indicator: "blue",
					});
				},
				error() {
					frappe.msgprint({
						title: __("Docker Repository Preparation Not Started"),
						message: __("The preparation operation could not be queued. Check the server error log."),
						indicator: "red",
					});
				},
			});
		}
	);
}

function render_docker_readiness_summary(frm) {
	const field = frm.get_field("docker_readiness_summary");
	if (!field) return;

	const snapshot = parse_json_field(frm.doc.docker_inspection_snapshot);
	const evaluation = parse_json_field(frm.doc.docker_readiness_evaluation);
	const setup = docker_setup_state(frm);
	const setupHtml = docker_setup_progress_html(frm, setup);
	const readiness = frm.doc.docker_readiness || "Unknown";
	const inspection = frm.doc.docker_inspection_status || "Not Inspected";
	const indicator =
		{
			Ready: "green",
			Degraded: "orange",
			"Not Ready": "red",
			Unknown: "gray",
		}[readiness] || "gray";

	const rows = [
		[__("Inspection Status"), inspection],
		[__("Docker Readiness"), readiness],
		[__("Last Inspected"), frm.doc.docker_inspected_on || __("Never")],
	];

	if (snapshot) {
		rows.push(
			[__("Docker Engine"), snapshot.engine?.availability || __("Unknown")],
			[__("Engine Version"), snapshot.engine?.server_version || __("Unavailable")],
			[__("Compose"), snapshot.compose?.compatibility || __("Unknown")],
			[__("Compose Version"), snapshot.compose?.version || __("Unavailable")],
			[__("Logical CPU Cores"), format_observation(snapshot.host?.cpu?.logical_cores)],
			[__("Total Memory"), format_bytes(snapshot.host?.memory?.total_bytes)],
			[__("Available Memory"), format_bytes(snapshot.host?.memory?.available_bytes)],
			[__("Available Storage"), format_bytes(snapshot.storage?.filesystem?.available_bytes)]
		);
	}

	const diagnostics = Array.isArray(evaluation?.diagnostics) ? evaluation.diagnostics : [];
	const diagnostic_html = diagnostics.length
		? `<ul class="mb-0">${diagnostics
				.map((item) => {
					const severity = escape_html(item.severity || "Info");
					const code = escape_html(item.code || "DIAGNOSTIC");
					const message = escape_html(item.message || "");
					return `<li><strong>${severity} — ${code}:</strong> ${message}</li>`;
				})
				.join("")}</ul>`
		: `<span class="text-muted">${__("No readiness diagnostics.")}</span>`;

	const rows_html = rows
		.map(
			([label, value]) =>
				`<tr><th class="text-muted" style="width: 38%">${escape_html(
					label
				)}</th><td>${escape_html(value)}</td></tr>`
		)
		.join("");

	const evaluated_at = evaluation?.evaluated_at
		? `<div class="text-muted small mt-2">${__("Evaluated")}: ${escape_html(
				evaluation.evaluated_at
		  )} · ${__("Evaluator")}: ${escape_html(
				evaluation.evaluator_version || __("Unknown")
		  )}</div>`
		: "";

	field.$wrapper.html(
		`${setupHtml}
		<div class="border rounded p-3">
			<div class="mb-3">
				<span class="indicator-pill ${indicator}">${escape_html(readiness)}</span>
				<span class="text-muted ml-2">${__(
					"Docker readiness is evaluated independently of the Host-native Node state."
				)}</span>
			</div>
			<table class="table table-bordered table-sm mb-3"><tbody>${rows_html}</tbody></table>
			<div class="font-weight-bold mb-2">${__("Readiness Diagnostics")}</div>
			${diagnostic_html}
			${evaluated_at}
		</div>`
	);
	field.$wrapper.find("[data-docker-action]").on("click", function () {
		run_docker_setup_action(frm, $(this).attr("data-docker-action"));
	});
}

function parse_json_field(value) {
	if (!value) return null;
	if (typeof value === "object") return value;
	try {
		return JSON.parse(value);
	} catch (error) {
		return null;
	}
}

function format_bytes(value) {
	if (typeof value !== "number" || value < 0) return __("Unavailable");
	const gib = value / 1073741824;
	return `${gib.toFixed(2)} GiB`;
}

function format_observation(value) {
	return value === null || value === undefined ? __("Unavailable") : String(value);
}

function escape_html(value) {
	return frappe.utils.escape_html(String(value ?? ""));
}

function run_bench_operation(frm, method, args = {}) {
	const dialog = create_log_dialog(method);
	start_execution_log_stream(dialog, frm.doc.name);
	frappe.call({
		method: `dt_master.api.infrastructure_node.${method}`,
		args: { node_name: frm.doc.name, ...args },
		callback: () => frm.reload_doc(),
		error: () => {
			frappe.msgprint(
				__("The operation request failed. Check the Node Action Log for details.")
			);
			frm.reload_doc();
		},
	});
}

function create_log_dialog(title) {
	const dialog = new frappe.ui.Dialog({
		title: `${title.replaceAll("_", " ")}`,
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "log_container",
			},
		],
		size: "large",
	});

	dialog.show();
	dialog.log_el = dialog.fields_dict.log_container.$wrapper;
	dialog.log_el.css({
		background: "#111",
		color: "#0f0",
		"font-family": "monospace",
		padding: "10px",
		height: "400px",
		"overflow-y": "scroll",
	});
	dialog.log_el.append(
		`<div id="log-wait" style="color:#888">${__(
			"Waiting for remote execution output..."
		)}</div>`
	);
	return dialog;
}

function run_node_action(frm, label, method) {
	if (method === "dt_master.api.infrastructure_node.inspect_node") {
		frappe.call({
			method,
			args: { node_name: frm.doc.name },
			freeze: true,
			freeze_message: __("Queueing Node inspection..."),
			callback(response) {
				const operation = response.message || {};
				frappe.show_alert({
					message: __(
						"Node inspection queued. Operation: {0}. View progress and results in Task Manager.",
						[operation.operation_id || __("unknown")]
					),
					indicator: "blue",
				});
			},
			error() {
				frappe.msgprint({
					title: __("Node Inspection Not Started"),
					message: __("The inspection could not be queued. Check the server error log."),
					indicator: "red",
				});
			},
		});
		return;
	}

	const dialog = create_log_dialog(label);
	start_execution_log_stream(dialog, frm.doc.name);
	frappe.call({
		method,
		args: { node_name: frm.doc.name },
		callback() {
			frm.reload_doc();
		},
		error() {
			frappe.msgprint(
				__("The operation request failed. Check the Node Action Log for details.")
			);
			frm.reload_doc();
		},
	});
}

function start_execution_log_stream(dialog, node_name) {
	let first_line_received = false;
	frappe.realtime.off("script_execution_log");
	frappe.realtime.on("script_execution_log", (data) => {
		if (data.node !== node_name) return;

		if (data.line) {
			if (!first_line_received) {
				dialog.log_el.find("#log-wait").remove();
				first_line_received = true;
			}
			dialog.log_el.append(`<div>${frappe.utils.escape_html(data.line)}</div>`);
			dialog.log_el.scrollTop(dialog.log_el[0].scrollHeight);
		}

		if (data.status === "Completed") {
			dialog.log_el.append(`<div style="color:#0f0">PROCESS COMPLETED</div>`);
			frappe.realtime.off("script_execution_log");
		}

		if (data.status === "Failed" || data.status === "Timed Out") {
			dialog.log_el.append(`<div style="color:red">PROCESS FAILED OR TIMED OUT</div>`);
			frappe.realtime.off("script_execution_log");
		}
	});
}
