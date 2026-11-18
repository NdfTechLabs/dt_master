frappe.ui.form.on("Infrastructure Node", {
	refresh(frm) {
		if (frm.is_new()) return;

		const { status, state } = frm.doc;

		const actions = {
			install_dependencies: {
				statuses: ["Provisioning", "Maintenance"],
				states: ["SSH Ready", "Error"],
				method: "dt_master.api.infrastructure_node.install_dependencies",
			},

			install_bench: {
				statuses: ["Provisioning", "Maintenance"],
				states: ["Deps Installed", "Error"],
				method: "dt_master.api.infrastructure_node.install_bench",
			},

			initialize_project: {
				statuses: ["Provisioning", "Maintenance"],
				states: ["Project Installed", "Error"],
				method: "dt_master.api.infrastructure_node.init_project",
			},

			prepare_project: {
				statuses: ["Provisioning", "Maintenance"],
				states: ["Initialized", "Error"],
				method: "dt_master.api.infrastructure_node.prepare_production",
			},

			start_project: {
				statuses: ["Active", "Maintenance"],
				states: ["Healthy", "Stopped"],
				method: "dt_master.api.infrastructure_node.start_project",
			},

			stop_project: {
				statuses: ["Active", "Maintenance"],
				states: ["Running"],
				method: "dt_master.api.infrastructure_node.stop_project",
			},

			check_system_status: {
				statuses: ["Provisioning", "Active", "Maintenance"],
				states: ["SSH Ready", "Healthy", "Running", "Stopped"],
				method: "dt_master.api.infrastructure_node.verify_production",
			},
		};

		Object.keys(actions).forEach((label) => hide_button(frm, label));

		Object.entries(actions).forEach(([label, cfg]) => {
			const status_ok = cfg.statuses.includes(status);
			const state_ok = cfg.states.includes(state);

			if (status_ok && state_ok) {
				show_and_bind(frm, label, cfg.method);
			}
		});

		render_docker_readiness_summary(frm);

		if (frappe.user_roles.includes("System Manager")) {
			frm.add_custom_button(
				__("Inspect Docker Runtime"),
				() => {
					frappe.confirm(
						__("Run a read-only Docker runtime inspection on this node? This does not change the Host-native Node state or Docker workloads."),
						() => run_docker_inspection(frm)
					);
				},
				__("Docker")
			);
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

function run_docker_inspection(frm) {
	const dialog = create_log_dialog("Inspect Docker Runtime");
	start_execution_log_stream(dialog, frm.doc.name);

	frappe.call({
		method: "dt_master.api.infrastructure_node.inspect_docker_runtime",
		args: { node_name: frm.doc.name },
		freeze: true,
		freeze_message: __("Inspecting Docker runtime..."),
		callback: (response) => {
			const result = response.message || {};
			const readiness = result.readiness || __("Unknown");
			const inspection = result.inspection_status || __("Unknown");
			frappe.show_alert({
				message: __("Docker inspection: {0}; readiness: {1}", [inspection, readiness]),
				indicator: readiness === "Ready" ? "green" : readiness === "Degraded" ? "orange" : "red",
			});
			frm.reload_doc();
		},
		error: () => {
			frappe.msgprint({
				title: __("Docker Inspection Failed"),
				message: __("The inspection request failed. Check the Node Action Log and server logs for details."),
				indicator: "red",
			});
			frm.reload_doc();
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
	const indicator = {
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
				`<tr><th class="text-muted" style="width: 38%">${escape_html(label)}</th><td>${escape_html(value)}</td></tr>`
		)
		.join("");

	const evaluated_at = evaluation?.evaluated_at
		? `<div class="text-muted small mt-2">${__("Evaluated")}: ${escape_html(evaluation.evaluated_at)} · ${__("Evaluator")}: ${escape_html(evaluation.evaluator_version || __("Unknown"))}</div>`
		: "";

	field.$wrapper.html(
		`<div class="border rounded p-3">
			<div class="mb-3">
				<span class="indicator-pill ${indicator}">${escape_html(readiness)}</span>
				<span class="text-muted ml-2">${__("Docker readiness is evaluated independently of the Host-native Node state.")}</span>
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
			frappe.msgprint(__("The operation request failed. Check the Node Action Log for details."));
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
	dialog.log_el.append(`<div id="log-wait" style="color:#888">${__("Waiting for remote execution output...")}</div>`);
	return dialog;
}

function show_and_bind(frm, label, method) {
	const btn = frm.get_field(label);
	if (!btn) return;

	btn.$wrapper.show();
	btn.$wrapper.off("click").on("click", () => {
		const dialog = create_log_dialog(label);
		start_execution_log_stream(dialog, frm.doc.name);
		frappe.call({
			method,
			args: { node_name: frm.doc.name },
			callback() {
				frm.reload_doc();
			},
			error() {
				frappe.msgprint(__("The operation request failed. Check the Node Action Log for details."));
				frm.reload_doc();
			},
		});
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

function hide_button(frm, label) {
	const btn = frm.get_field(label);
	if (btn) btn.$wrapper.hide();
}
