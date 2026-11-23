frappe.ui.form.on("Infrastructure Node", {
	refresh(frm) {
		frm.set_query("docker_helper_release", () => ({
			filters: { status: "Published" },
		}));
		if (frm.doc.docker_helper_release) {
			frappe.db
				.get_value("Docker Helper Release", frm.doc.docker_helper_release, "status")
				.then(({ message }) => {
					if (message && message.status === "Retired") {
						frm.dashboard.set_headline_alert(
							__(
								"This node is assigned to a retired Docker Helper Release. Select the active Published release and save the node before starting reconciliation."
							),
							"orange"
						);
					}
				});
		}
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

function watch_docker_operation(frm, operationId, label) {
	if (!operationId) return;
	frm.__docker_operation_watchers = frm.__docker_operation_watchers || new Set();
	if (frm.__docker_operation_watchers.has(operationId)) return;
	frm.__docker_operation_watchers.add(operationId);

	const completedStates = new Set(["Succeeded", "Failed", "Cancelled", "Recovery Required"]);
	const pollInterval = 5000;
	const maxAttempts = 360;

	(async () => {
		try {
			for (let attempt = 0; attempt < maxAttempts; attempt++) {
				await new Promise((resolve) => setTimeout(resolve, pollInterval));
				let operation;
				try {
					const response = await frappe.call({
						method: "dt_master.api.operations.get_operation",
						args: { operation_id: operationId },
					});
					operation = response.message;
				} catch (error) {
					continue;
				}
				if (!operation || !completedStates.has(operation.execution_state)) continue;

				if (frm.is_dirty && frm.is_dirty()) {
					frappe.show_alert({
						message: __(
							"{0} has finished ({1}). This form has unsaved changes, so save or reload it to view the updated Docker state.",
							[__(label), operation.execution_state]
						),
						indicator: operation.execution_state === "Succeeded" ? "green" : "orange",
					});
				} else {
					await frm.reload_doc();
					frappe.show_alert({
						message: __(
							"{0} finished: {1}. Infrastructure Node status has been refreshed.",
							[__(label), operation.execution_state]
						),
						indicator: operation.execution_state === "Succeeded" ? "green" : "orange",
					});
				}
				return;
			}
			frappe.show_alert({
				message: __(
					"{0} is still running or could not be confirmed. Task Manager remains the source of progress; refresh this form when it finishes.",
					[__(label)]
				),
				indicator: "orange",
			});
		} finally {
			frm.__docker_operation_watchers.delete(operationId);
		}
	})();
}

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
					"Node connection test queued. Operation: {0}. Task Manager tracks progress; this form refreshes when it finishes.",
					[operation.operation_id || __("unknown")]
				),
				indicator: "blue",
			});
			watch_docker_operation(frm, operation.operation_id, "Node connection test");
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

function run_docker_helper_release_assessment(frm) {
	frappe.call({
		method: "dt_master.api.infrastructure_node.assess_docker_helper_release",
		args: { node_name: frm.doc.name },
		freeze: true,
		freeze_message: __("Starting Docker Helper Release assessment..."),
		callback(response) {
			const operation = response.message || {};
			frappe.show_alert({
				message: __(
					"Docker Helper Release assessment queued. Operation: {0}. Task Manager tracks progress.",
					[operation.operation_id || __("unknown")]
				),
				indicator: "blue",
			});
			watch_docker_operation(
				frm,
				operation.operation_id,
				"Docker Helper Release assessment"
			);
		},
		error() {
			frappe.msgprint({
				title: __("Release Assessment Not Started"),
				message: __("The assessment could not be queued. Check the server error log."),
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
					"Docker privilege bootstrap queued. Operation: {0}. Task Manager tracks progress; this form refreshes when it finishes.",
					[operation.operation_id || __("unknown")]
				),
				indicator: "blue",
			});
			watch_docker_operation(frm, operation.operation_id, "Docker privilege bootstrap");
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
					"Docker Engine installation queued. Operation: {0}. Task Manager tracks progress; this form refreshes after verification finishes.",
					[operation.operation_id || __("unknown")]
				),
				indicator: "blue",
			});
			watch_docker_operation(frm, operation.operation_id, "Docker Engine installation");
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
					"{0} queued. Operation: {1}. Task Manager tracks progress; this form refreshes when it finishes.",
					[__(label), operation.operation_id || __("unknown")]
				),
				indicator: "blue",
			});
			watch_docker_operation(frm, operation.operation_id, label);
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
	const releaseStatus = frm.doc.docker_helper_release_assessment_status || "Not Assessed";
	const releaseAssigned = Boolean(frm.doc.docker_helper_release);
	const helperInstalled =
		(releaseAssigned && releaseStatus === "Current") ||
		(privilegeBootstrap.status === "Succeeded" && Boolean(privilegeBootstrap.operation_id));
	const privilegesReady =
		privilegeReadiness.status === "Ready" && Boolean(privilegeReadiness.operation_id);
	const inspectionSuccessful = frm.doc.docker_inspection_status === "Successful";
	const planStatus = installPlan.status || null;

	let action = null;
	let blockedReason = null;
	if (releaseAssigned && releaseStatus === "Release Retired") {
		blockedReason =
			"The assigned Docker Helper Release is retired. Select the active Published release in the Docker Helper Release field, save the node, then start a new release assessment. No deployment or installation will run until that explicit reassignment.";
	} else if (!connected || state === "Not Evaluated" || state === "Connection Required") {
		if (sshConfigured) {
			action = { key: "test_connection", label: "Test Node Connection" };
		} else {
			blockedReason =
				"Configure an SSH host alias, or an IP address and SSH user, before testing the connection.";
		}
	} else if (state === "Host Inspection Required") {
		action = { key: "inspect_host", label: "Inspect Host" };
	} else if (
		releaseAssigned &&
		releaseStatus !== "Current" &&
		state !== "Operation In Progress" &&
		!["Reconciliation Required", "Failed"].includes(state)
	) {
		if (!hostSupported) {
			blockedReason =
				"Docker Helper Release assessment is locked until Host inspection confirms a supported operating system.";
		} else {
			action = {
				key: "assess_helper_release",
				label:
					releaseStatus === "Scripts Deployment Required"
						? "Restart Release Assessment"
						: "Assess Docker Helper Release",
			};
		}
	} else if (state === "Privilege Setup Required") {
		if (!hostSupported) {
			blockedReason =
				"Docker privilege setup is locked because Host inspection has not confirmed a supported operating system.";
		} else if (!helperInstalled) {
			action = { key: "bootstrap_privileges", label: "Install Docker Privilege Helper" };
		} else {
			action = { key: "check_privileges", label: "Check Docker Privileges" };
		}
	} else if (state === "Docker Inspection Required") {
		if (["blocked", "preparation_required"].includes(planStatus)) {
			blockedReason =
				"The latest preflight plan cannot advance because a required host resource or installation prerequisite is not satisfied. Review the plan and readiness diagnostics.";
		} else if (!privilegesReady) {
			blockedReason =
				"Docker runtime inspection is locked until trusted privilege verification succeeds.";
		} else if (!inspectionSuccessful) {
			action = { key: "inspect_docker", label: "Inspect Docker Runtime" };
		} else {
			action = { key: "preflight", label: "Run Docker Preflight" };
		}
	} else if (state === "Repository Preparation Required") {
		// The planner may label repository-addressable blockers as "blocked"
		// while the backend lifecycle classifier authorizes this preparation step.
		if (["preparation_required", "blocked"].includes(planStatus)) {
			action = { key: "prepare_repository", label: "Prepare Docker Repository" };
		} else {
			blockedReason =
				"The persisted install plan does not authorize repository preparation. Refresh the form and review the latest operation result.";
		}
	} else if (state === "Ready for Install") {
		if (planStatus === "ready") {
			action = { key: "install_engine", label: "Install Docker Engine" };
		} else {
			blockedReason =
				"A current install plan with status Ready is required before Engine installation.";
		}
	} else if (state === "Engine Ready") {
		blockedReason =
			"Docker Engine installation completed and its postcondition was verified. Optional observations are available below.";
	} else if (state === "Operation In Progress") {
		blockedReason =
			"A Docker lifecycle operation is in progress. Wait for it to finish in Task Manager, then refresh this form.";
	} else if (state === "Reconciliation Required" || state === "Failed") {
		blockedReason =
			"The last lifecycle operation requires investigation or reconciliation. Review its result in Task Manager before retrying.";
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

function docker_install_plan_details_html(plan) {
	if (!plan || !plan.status) {
		return (
			'<div class="small text-muted mt-2">' +
			__("No validated Docker install plan is recorded yet.") +
			"</div>"
		);
	}

	const reasonDescriptions = {
		HOST_IDENTITY_UNKNOWN: __("Host operating-system identity could not be established."),
		HOST_UNSUPPORTED: __("This operating system, release, or architecture is not supported."),
		PACKAGE_MANAGER_UNSUPPORTED: __("The host does not use a supported package manager."),
		PACKAGE_MANAGER_BUSY: __("The package manager is currently locked by another process."),
		DPKG_STATE_BROKEN: __("The package database reports an incomplete or inconsistent state."),
		APT_METADATA_NOT_FRESH: __("APT package metadata is stale, aging, or unavailable."),
		DOCKER_REPOSITORY_UNTRUSTED: __(
			"The configured Docker package source is not the official trusted repository."
		),
		DOCKER_KEYRING_PATH_UNTRUSTED: __(
			"The Docker signing key is not stored at the approved keyring path."
		),
		DOCKER_SIGNING_KEY_UNVERIFIED: __(
			"The Docker signing key has not yet been verified against platform policy."
		),
		DOCKER_SIGNING_KEY_MISMATCH: __(
			"The observed Docker signing key does not match the pinned trust policy."
		),
		ROOT_STORAGE_INSUFFICIENT: __(
			"The root filesystem does not have the required free space."
		),
		APT_CACHE_STORAGE_INSUFFICIENT: __(
			"The APT cache filesystem does not have the required free space."
		),
		WORKLOAD_STATE_UNAVAILABLE: __("Running workload state could not be inspected."),
		ACTIVE_WORKLOADS_IMPACT_APPROVAL_REQUIRED: __(
			"Running containers may be interrupted when Docker starts; operator impact approval is required."
		),
		CONFLICTING_PACKAGE_FAMILY: __(
			"Conflicting Docker or container runtime packages are installed."
		),
		PARTIAL_DOCKER_CE_INSTALLATION: __(
			"Only part of the required Docker Engine package set is installed."
		),
		ENGINE_PACKAGE_HELD: __(
			"One or more required Docker Engine packages are held by the package manager."
		),
		EXISTING_DOCKER_ENGINE_UNHEALTHY: __(
			"An existing Docker Engine installation is not healthy."
		),
		EXISTING_PACKAGE_ORIGIN_UNVERIFIED: __(
			"The origin of one or more installed Docker packages could not be trusted."
		),
	};

	const describeReason = (code) => {
		if (reasonDescriptions[code]) return reasonDescriptions[code];
		if (code.startsWith("PACKAGE_CANDIDATE_UNAVAILABLE_OR_UNTRUSTED:")) {
			return __("No trusted official-repository package candidate is available for {0}.", [
				code.split(":").slice(1).join(":"),
			]);
		}
		if (code.startsWith("PACKAGE_INVENTORY_MISSING:")) {
			return __("Package inventory is missing the required entry for {0}.", [
				code.split(":").slice(1).join(":"),
			]);
		}
		return __("The planner reported an additional condition requiring review.");
	};

	const reasonCodes = Array.isArray(plan.reason_codes) ? plan.reason_codes : [];
	const diagnosticsHtml = reasonCodes.length
		? '<ul class="mb-0">' +
		  reasonCodes
				.map((code) => {
					const warning =
						plan.status === "preparation_required" ||
						code === "ACTIVE_WORKLOADS_IMPACT_APPROVAL_REQUIRED";
					return (
						'<li><span class="indicator-pill ' +
						(warning ? "orange" : "red") +
						'">' +
						(warning ? __("Review") : __("Blocking")) +
						"</span> <strong>" +
						escape_html(code) +
						"</strong>: " +
						escape_html(describeReason(code)) +
						"</li>"
					);
				})
				.join("") +
		  "</ul>"
		: '<span class="text-muted">' +
		  __("No planner blockers or review conditions.") +
		  "</span>";

	const prerequisites = Array.isArray(plan.prerequisites) ? plan.prerequisites : [];
	const prerequisiteLabels = {
		configure_official_docker_repository: __("Configure official Docker repository"),
		refresh_apt_metadata: __("Refresh APT package metadata"),
		verify_and_pin_docker_signing_key: __("Verify and pin Docker signing key"),
	};
	const prerequisitesHtml = prerequisites.length
		? '<ul class="mb-0">' +
		  prerequisites
				.map((item) => "<li>" + escape_html(prerequisiteLabels[item] || item) + "</li>")
				.join("") +
		  "</ul>"
		: '<span class="text-muted">' + __("No additional planner prerequisites.") + "</span>";

	const packages = Array.isArray(plan.packages) ? plan.packages : [];
	const packageRows = packages
		.map(
			(row) =>
				"<tr><td>" +
				escape_html(row.name || "") +
				"</td><td>" +
				escape_html(row.action || "") +
				"</td><td>" +
				escape_html(row.installed_version || __("Not installed")) +
				"</td><td>" +
				escape_html(row.candidate_version || __("Unavailable")) +
				"</td></tr>"
		)
		.join("");
	const packageHtml = packages.length
		? '<div class="table-responsive"><table class="table table-bordered table-sm mb-0"><thead><tr><th>' +
		  __("Package") +
		  "</th><th>" +
		  __("Action") +
		  "</th><th>" +
		  __("Installed version") +
		  "</th><th>" +
		  __("Candidate version") +
		  "</th></tr></thead><tbody>" +
		  packageRows +
		  "</tbody></table></div>"
		: '<span class="text-muted">' + __("No package candidates were produced.") + "</span>";

	const repository = plan.repository || {};
	const apt = plan.apt_metadata || {};
	const packageManager = plan.package_manager || {};
	const storage = plan.storage || {};
	const workload = plan.workload_impact || {};
	const metadataRows = [
		[__("Repository"), repository.source_id],
		[__("Keyring path"), repository.keyring_path],
		[__("Signing key evidence"), repository.fingerprint_evidence],
		[
			__("Signing key trusted"),
			repository.signing_key_trusted === true ? __("Yes") : __("No"),
		],
		[__("APT metadata"), apt.status],
		[__("Metadata age (seconds)"), apt.age_seconds],
		[__("Package manager locked"), packageManager.locked === true ? __("Yes") : __("No")],
		[__("dpkg audit clean"), packageManager.dpkg_audit_clean === true ? __("Yes") : __("No")],
		[__("Root free space"), storage.root_available_bytes],
		[__("APT cache free space"), storage.apt_cache_available_bytes],
		[__("Running containers"), workload.running_containers],
		[__("Total containers"), workload.total_containers],
	];
	const metadataHtml =
		'<table class="table table-bordered table-sm mb-0"><tbody>' +
		metadataRows
			.map(
				([label, value]) =>
					'<tr><th class="text-muted" style="width: 42%">' +
					escape_html(label) +
					"</th><td>" +
					escape_html(
						value === null || value === undefined ? __("Unknown") : String(value)
					) +
					"</td></tr>"
			)
			.join("") +
		"</tbody></table>";

	return (
		'<div class="border rounded p-3 mt-3">' +
		'<div class="d-flex justify-content-between align-items-center mb-2"><strong>' +
		__("Docker Engine Install Plan") +
		'</strong><span class="indicator-pill ' +
		(plan.status === "ready" || plan.status === "no_op"
			? "green"
			: plan.status === "preparation_required"
			? "orange"
			: "red") +
		'">' +
		escape_html(plan.status) +
		"</span></div>" +
		'<div class="small text-muted mb-3">' +
		__("Policy version") +
		": " +
		escape_html(plan.policy_version || __("Unknown")) +
		" · " +
		__("Expires after") +
		": " +
		escape_html(String(plan.expires_after_seconds || 0)) +
		" " +
		__("seconds") +
		"<br>" +
		__("Plan digest") +
		": " +
		escape_html(plan.plan_digest || "") +
		"</div>" +
		'<div class="font-weight-bold mb-2">' +
		__("Planner diagnostics") +
		"</div>" +
		diagnosticsHtml +
		'<div class="font-weight-bold mt-3 mb-2">' +
		__("Prerequisites") +
		"</div>" +
		prerequisitesHtml +
		'<div class="font-weight-bold mt-3 mb-2">' +
		__("Package candidates") +
		"</div>" +
		packageHtml +
		'<details class="mt-3"><summary class="font-weight-bold">' +
		__("Repository, package manager and host evidence") +
		"</summary>" +
		'<div class="mt-2">' +
		metadataHtml +
		"</div></details>" +
		'<div class="small text-muted mt-2">' +
		escape_html(plan.daemon_impact || "") +
		"</div></div>"
	);
}

function docker_setup_progress_html(frm, setup) {
	const steps = [
		{
			label: "SSH connection",
			done: setup.connected,
			current: setup.action?.key === "test_connection",
		},
		{
			label: "Host inspection",
			done: setup.hostSupported,
			current: setup.action?.key === "inspect_host",
		},
		{
			label: "Privilege helper",
			done: setup.helperInstalled,
			current: setup.action?.key === "bootstrap_privileges",
		},
		{
			label: "Privilege verification",
			done: setup.privilegesReady,
			current: setup.action?.key === "check_privileges",
		},
		{
			label: "Docker runtime inspection",
			done: setup.inspectionSuccessful,
			current: setup.action?.key === "inspect_docker",
		},
		{
			label: "Install preflight",
			done: ["ready", "preparation_required", "blocked", "no_op"].includes(setup.planStatus),
			current: setup.action?.key === "preflight",
		},
		{
			label: "Repository preparation (if required)",
			resolved:
				setup.planStatus === "ready" &&
				["Ready for Install", "Engine Ready"].includes(setup.state),
			current: setup.action?.key === "prepare_repository",
		},
		{
			label: "Docker Engine installation",
			done: setup.state === "Engine Ready",
			current: setup.action?.key === "install_engine",
		},
	];
	const stepsHtml = steps
		.map((step, index) => {
			const status = step.done
				? "Completed"
				: step.resolved
				? "Resolved"
				: step.current
				? "Next"
				: "Locked";
			const indicator =
				step.done || step.resolved ? "green" : step.current ? "blue" : "gray";
			return (
				'<li class="mb-1"><span class="indicator-pill ' +
				indicator +
				'">' +
				__(status) +
				"</span> <span>" +
				(index + 1) +
				". " +
				__(step.label) +
				"</span></li>"
			);
		})
		.join("");
	const planHtml = docker_install_plan_details_html(setup.installPlan);
	const actionHtml = setup.action
		? '<button type="button" class="btn btn-primary btn-sm mt-3" data-docker-action="' +
		  escape_html(setup.action.key) +
		  '">' +
		  __(setup.action.label) +
		  "</button>"
		: '<div class="alert ' +
		  (setup.state === "Engine Ready" ? "alert-success" : "alert-warning") +
		  ' mt-3 mb-0">' +
		  escape_html(setup.blockedReason || __("No action is currently available.")) +
		  "</div>";
	const roleHtml = frappe.user_roles.includes("System Manager")
		? actionHtml
		: '<div class="text-muted small mt-3">' +
		  __("Docker lifecycle actions are available to System Managers.") +
		  "</div>";
	const recoveryActionHtml = frappe.user_roles.includes("System Manager")
		? '<button type="button" class="btn btn-default btn-sm mt-3 ml-2" data-docker-action="restart_from">' +
		  __("Restart from…") +
		  "</button>"
		: "";
	const secondaryActionsHtml =
		frappe.user_roles.includes("System Manager") && setup.state === "Engine Ready"
			? '<button type="button" class="btn btn-default btn-sm mt-3 ml-2" data-docker-action="verify_runtime">' +
			  __("Verify Docker Runtime") +
			  "</button>" +
			  '<button type="button" class="btn btn-default btn-sm mt-3 ml-2" data-docker-action="updates">' +
			  __("Check Docker Updates") +
			  "</button>"
			: "";
	return (
		'<div class="border rounded p-3 mb-3">' +
		'<div class="d-flex justify-content-between align-items-center mb-2">' +
		"<strong>" +
		__("Guided Docker Setup") +
		"</strong>" +
		'<span class="indicator-pill ' +
		(setup.state === "Engine Ready"
			? "green"
			: setup.state === "Failed" || setup.state === "Reconciliation Required"
			? "red"
			: "blue") +
		'">' +
		escape_html(setup.state) +
		"</span></div>" +
		'<ol class="pl-3 mb-2">' +
		stepsHtml +
		"</ol>" +
		planHtml +
		roleHtml +
		recoveryActionHtml +
		secondaryActionsHtml +
		'<button type="button" class="btn btn-default btn-xs mt-2" data-docker-action="refresh">' +
		__("Refresh status") +
		"</button>" +
		'<div class="small text-muted mt-2">' +
		__(
			"Each step unlocks only after the preceding operation has produced the required persisted evidence. The server revalidates every action."
		) +
		"</div></div>"
	);
}

function run_docker_setup_action(frm, action) {
	switch (action) {
		case "test_connection":
			return test_node_connection(frm);
		case "inspect_host":
			return run_node_action(
				frm,
				"Inspect Host",
				"dt_master.api.infrastructure_node.inspect_node"
			);
		case "assess_helper_release":
			return run_docker_helper_release_assessment(frm);
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
		case "restart_from":
			return show_docker_setup_restart_dialog(frm);
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
		__(
			"Prepare the official Docker package repository and refresh trusted package metadata? This does not install Docker Engine."
		),
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
							"Docker repository preparation queued. Operation: {0}. Task Manager tracks progress; this form refreshes when it finishes.",
							[operation.operation_id || __("unknown")]
						),
						indicator: "blue",
					});
					watch_docker_operation(
						frm,
						operation.operation_id,
						"Docker repository preparation"
					);
				},
				error() {
					frappe.msgprint({
						title: __("Docker Repository Preparation Not Started"),
						message: __(
							"The preparation operation could not be queued. Check the server error log."
						),
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
						"Node inspection queued. Operation: {0}. Task Manager tracks progress; this form refreshes when it finishes.",
						[operation.operation_id || __("unknown")]
					),
					indicator: "blue",
				});
				watch_docker_operation(frm, operation.operation_id, "Host inspection");
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

function show_docker_setup_restart_dialog(frm) {
	const checkpoints = [
		{
			value: "connection",
			label: __("1. SSH Connection"),
			description: __("Repeat connection verification and rebuild all downstream evidence."),
		},
		{
			value: "host_inspection",
			label: __("2. Host Inspection"),
			description: __(
				"Preserve SSH verification; repeat Host inspection and all later stages."
			),
		},
		{
			value: "privilege_helper",
			label: __("3. Docker Privilege Helper"),
			description: __(
				"Preserve Host inspection; reinstall helper and rebuild downstream evidence."
			),
		},
		{
			value: "privilege_verification",
			label: __("4. Privilege Verification"),
			description: __(
				"Preserve helper installation; repeat privilege verification and later stages."
			),
		},
		{
			value: "docker_inspection",
			label: __("5. Docker Runtime Inspection"),
			description: __(
				"Preserve privilege readiness; repeat runtime inspection and later stages."
			),
		},
		{
			value: "preflight",
			label: __("6. Install Preflight"),
			description: __(
				"Preserve runtime inspection; discard the plan and calculate a fresh one."
			),
		},
		{
			value: "repository_preparation",
			label: __("7. Repository Preparation"),
			description: __(
				"Retain the current preparation plan and repeat repository preparation."
			),
		},
		{
			value: "engine_installation",
			label: __("8. Docker Engine Installation"),
			description: __(
				"Retain prerequisites; rerun fresh checks before Engine installation."
			),
		},
	];
	const dialog = new frappe.ui.Dialog({
		title: __("Restart Guided Docker Setup"),
		fields: [
			{
				fieldname: "checkpoint",
				fieldtype: "Select",
				label: __("Restart from stage"),
				options: checkpoints.map((item) => item.label).join("\n"),
				reqd: 1,
				default: checkpoints[2].label,
				onchange() {
					const selected = checkpoints.find(
						(item) => item.label === dialog.get_value("checkpoint")
					);
					dialog.fields_dict.restart_description.$wrapper.html(
						'<div class="text-muted small">' +
							escape_html(selected?.description || "") +
							"</div>"
					);
				},
			},
			{
				fieldname: "restart_description",
				fieldtype: "HTML",
				options:
					'<div class="text-muted small">' +
					escape_html(checkpoints[2].description) +
					"</div>",
			},
		],
		primary_action_label: __("Restart from Stage"),
		primary_action(values) {
			const selected = checkpoints.find((item) => item.label === values.checkpoint);
			if (!selected) return;
			dialog.hide();
			frappe.confirm(
				__(
					"Restart Docker setup from {0}? Evidence from this stage onward will be invalidated. Earlier evidence will be preserved where applicable. No Bench or Tenant data will be removed.",
					[selected.label]
				),
				() => {
					frappe.call({
						method: "dt_master.api.infrastructure_node.restart_docker_setup_from",
						args: { node_name: frm.doc.name, checkpoint: selected.value },
						freeze: true,
						freeze_message: __("Queueing Docker setup restart..."),
						callback(response) {
							const operation = response.message || {};
							frappe.show_alert({
								message: __(
									"Docker setup restart queued. Operation: {0}. Task Manager tracks progress.",
									[operation.operation_id || __("unknown")]
								),
								indicator: "blue",
							});
							watch_docker_operation(
								frm,
								operation.operation_id,
								"Docker setup restart"
							);
						},
						error() {
							frappe.msgprint({
								title: __("Restart Not Started"),
								message: __(
									"The restart could not be queued. Check the server error log."
								),
								indicator: "red",
							});
						},
					});
				}
			);
		},
	});
	dialog.show();
}
