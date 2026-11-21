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
			check_system_status: {
				label: "Inspect Node",
				statuses: ["Provisioning", "Active", "Maintenance"],
				states: ["SSH Ready", "Healthy", "Running", "Stopped", "Error"],
				method: "dt_master.api.infrastructure_node.inspect_node",
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
		const sshConfigured = Boolean(
			frm.doc.ssh_host_alias || (frm.doc.ip_address && frm.doc.ssh_user)
		);
		if (frappe.user_roles.includes("System Manager") && sshConfigured) {
			frm.add_custom_button(
				__("Test Node Connection"),
				() => test_node_connection(frm),
				__("Node")
			);
		}
		render_docker_readiness_summary(frm);

		if (frappe.user_roles.includes("System Manager")) {
			frm.add_custom_button(
				__("Install Docker Privilege Helper"),
				() =>
					frappe.confirm(
						__(
							"Install the reviewed Docker helper and its narrowly scoped sudoers rule using this node’s configured SSH identity? Existing non-interactive administrative authority is required."
						),
						() => run_docker_privilege_bootstrap(frm)
					),
				__("Docker")
			);
			if (frm.doc.access_verified) {
				frm.add_custom_button(
					__("Install Docker Engine"),
					() =>
						frappe.confirm(
							__(
								"Install Docker Engine on this node? DT Master will check the existing Docker readiness requirements, use the trusted installer, and verify the result. Existing running containers may be affected."
							),
							() => run_docker_engine_install(frm)
						),
					__("Docker")
				);
			}
			const dockerObservations = [
				{
					key: "privileges",
					label: "Check Docker Privileges",
					description:
						"Check the trusted helper, ownership, permissions, sudoers policy and root self-test. This is read-only.",
				},
				{
					key: "inspect",
					label: "Inspect Docker Runtime",
					description:
						"Inspect Docker Engine, Compose and host resources. This is read-only.",
				},
				{
					key: "preflight",
					label: "Run Docker Preflight",
					description:
						"Evaluate whether this node is ready for Docker-backed workloads. This is read-only.",
				},
				{
					key: "updates",
					label: "Check Docker Updates",
					description:
						"Check installed Docker package versions and available updates. This does not install updates.",
				},
				{
					key: "verify",
					label: "Verify Docker Runtime",
					description:
						"Verify Docker CLI, Engine and Compose availability. This is read-only.",
				},
			];
			for (const observation of dockerObservations) {
				frm.add_custom_button(
					__(observation.label),
					() => {
						frappe.confirm(__(observation.description), () =>
							run_docker_observation(frm, observation.key, observation.label)
						);
					},
					__("Docker")
				);
			}
		}

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
				message: __(
					"The installation could not be queued. Check the server error log."
				),
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

function render_docker_readiness_summary(frm) {
	const field = frm.get_field("docker_readiness_summary");
	if (!field) return;

	const snapshot = parse_json_field(frm.doc.docker_inspection_snapshot);
	const evaluation = parse_json_field(frm.doc.docker_readiness_evaluation);
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
		`<div class="border rounded p-3">
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
