/* Copyright (c) 2026, Dhana Technologies and contributors
 * Global Desk Task Manager — initial snapshot and presentation layer.
 */
(() => {
	"use strict";

	if (window.__dtMasterTaskManager) return;
	if (!window.frappe || !frappe.boot || !frappe.session || frappe.session.user === "Guest") return;

	const API = "dt_master.api.operations.get_operations";
	const STORAGE_KEY = "dt_master_task_manager_collapsed";
	const ACTIVE_STATES = new Set([
		"Pending Dispatch",
		"Queued",
		"Running",
		"Retry Waiting",
		"Cancellation Requested",
		"Recovery Required",
	]);
	const TERMINAL_STATES = new Set(["Succeeded", "Failed", "Cancelled"]);
	const stateClass = (state) => {
		if (state === "Succeeded") return "success";
		if (state === "Failed") return "danger";
		if (state === "Cancelled") return "muted";
		if (state === "Recovery Required") return "warning";
		return "active";
	};
	const text = (value, fallback = "—") => value === null || value === undefined || value === "" ? fallback : String(value);
	const formatDate = (value) => {
		if (!value) return "";
		const date = frappe.datetime && frappe.datetime.str_to_user
			? frappe.datetime.str_to_user(value)
			: value;
		return text(date, "");
	};

	class TaskManager {
		constructor() {
			this.operations = new Map();
			this.collapsed = localStorage.getItem(STORAGE_KEY) === "1";
			this.root = null;
			this.refreshing = false;
			this.refreshQueued = false;
		}

		mount() {
			if (this.root || !document.body) return;
			this.root = document.createElement("section");
			this.root.id = "dt-master-task-manager";
			this.root.className = this.collapsed ? "dt-task-manager is-collapsed" : "dt-task-manager";
			this.root.setAttribute("aria-label", __("Background operations"));
			document.body.appendChild(this.root);
			this.render();
			this.refresh();
		}

		setCollapsed(collapsed) {
			this.collapsed = Boolean(collapsed);
			localStorage.setItem(STORAGE_KEY, this.collapsed ? "1" : "0");
			this.root?.classList.toggle("is-collapsed", this.collapsed);
			this.render();
		}

		toggle() {
			this.setCollapsed(!this.collapsed);
		}

		async refresh() {
			if (this.refreshing) {
				this.refreshQueued = true;
				return;
			}
			this.refreshing = true;
			try {
				const response = await frappe.call({
					method: API,
					args: { active_limit: 50, recent_limit: 20 },
				});
				const data = response.message || {};
				const snapshot = [...(data.active || []), ...(data.recent || [])];
				const next = new Map();
				snapshot.forEach((operation) => {
					if (operation && operation.operation_id) {
						next.set(operation.operation_id, operation);
					}
				});
				this.operations = next;
				this.render();
			} catch (error) {
				// Keep the panel usable; do not expose server diagnostics in Desk.
				console.warn("[dt_master] Unable to refresh Task Manager snapshot.");
			} finally {
				this.refreshing = false;
				if (this.refreshQueued) {
					this.refreshQueued = false;
					this.refresh();
				}
			}
		}

		render() {
			if (!this.root) return;
			const operations = [...this.operations.values()];
			const active = operations.filter((item) => ACTIVE_STATES.has(item.execution_state));
			const recent = operations
				.filter((item) => TERMINAL_STATES.has(item.execution_state))
				.sort((a, b) => String(b.modified || "").localeCompare(String(a.modified || "")));
			this.root.replaceChildren();

			const header = document.createElement("div");
			header.className = "dt-task-manager__header";
			const title = document.createElement("div");
			title.className = "dt-task-manager__title";
			const titleText = document.createElement("strong");
			titleText.textContent = __("Background tasks");
			const count = document.createElement("span");
			count.className = "dt-task-manager__count";
			count.textContent = String(active.length);
			title.append(titleText, count);
			const controls = document.createElement("div");
			controls.className = "dt-task-manager__controls";
			const refresh = document.createElement("button");
			refresh.type = "button";
			refresh.className = "dt-task-manager__icon";
			refresh.textContent = "↻";
			refresh.title = __("Refresh tasks");
			refresh.setAttribute("aria-label", __("Refresh tasks"));
			refresh.addEventListener("click", () => this.refresh());
			const collapse = document.createElement("button");
			collapse.type = "button";
			collapse.className = "dt-task-manager__icon";
			collapse.textContent = this.collapsed ? "▴" : "▾";
			collapse.title = this.collapsed ? __("Expand") : __("Minimize");
			collapse.setAttribute("aria-label", collapse.title);
			collapse.addEventListener("click", () => this.toggle());
			controls.append(refresh, collapse);
			header.append(title, controls);
			this.root.appendChild(header);

			if (this.collapsed) return;

			const body = document.createElement("div");
			body.className = "dt-task-manager__body";
			if (active.length) {
				body.appendChild(this.section(__("In progress"), active));
			}
			if (recent.length) {
				body.appendChild(this.section(__("Recent"), recent.slice(0, 10)));
			}
			if (!active.length && !recent.length) {
				const empty = document.createElement("p");
				empty.className = "dt-task-manager__empty";
				empty.textContent = __("No background tasks yet.");
				body.appendChild(empty);
			}
			this.root.appendChild(body);
		}

		section(label, operations) {
			const section = document.createElement("section");
			section.className = "dt-task-manager__section";
			const heading = document.createElement("h4");
			heading.textContent = label;
			section.appendChild(heading);
			operations.forEach((operation) => section.appendChild(this.operationCard(operation)));
			return section;
		}

		operationCard(operation) {
			const card = document.createElement("article");
			card.className = "dt-task-manager__operation";
			const line = document.createElement("div");
			line.className = "dt-task-manager__operation-line";
			const label = document.createElement("strong");
			label.textContent = text(operation.operation_type, __("Operation"));
			const badge = document.createElement("span");
			badge.className = "dt-task-manager__state dt-task-manager__state--" + stateClass(operation.execution_state);
			badge.textContent = text(operation.execution_state);
			line.append(label, badge);

			const target = document.createElement("div");
			target.className = "dt-task-manager__target";
			target.textContent = [operation.target_doctype, operation.target_name].filter(Boolean).join(" · ") || __("Platform task");
			card.append(line, target);

			if (operation.error_summary) {
				const error = document.createElement("p");
				error.className = "dt-task-manager__error";
				error.textContent = operation.error_summary;
				card.appendChild(error);
			}
			const timestamp = operation.completed_on || operation.modified || operation.creation;
			if (timestamp) {
				const time = document.createElement("time");
				time.className = "dt-task-manager__time";
				time.textContent = formatDate(timestamp);
				card.appendChild(time);
			}
			return card;
		}
	}

	const start = () => {
		if (window.__dtMasterTaskManager) return;
		const manager = new TaskManager();
		window.__dtMasterTaskManager = manager;
		manager.mount();
	};

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", start, { once: true });
	} else {
		start();
	}
})();
