/* Copyright (c) 2026, Dhana Technologies and contributors
 * Global Desk Task Manager — initial snapshot and presentation layer.
 */
(() => {
	"use strict";

	if (window.__dtMasterTaskManager) return;
	if (!window.frappe || !frappe.boot || !frappe.session || frappe.session.user === "Guest") return;

	const API = "dt_master.api.operations.get_operations";
	const EVENTS_API = "dt_master.api.operations.get_operation_events";
	const EVENT_BATCH_SIZE = 100;
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
			this.lastSequences = new Map();
			this.replaying = new Set();
			this.replayQueued = new Set();
			this.handleOperationEvent = (event) => this.onOperationEvent(event);
			this.handleReconnect = () => this.reconcileAll();
		}

		mount() {
			if (this.root || !document.body) return;
			this.root = document.createElement("section");
			this.root.id = "dt-master-task-manager";
			this.root.className = this.collapsed ? "dt-task-manager is-collapsed" : "dt-task-manager";
			this.root.setAttribute("aria-label", __("Background operations"));
			document.body.appendChild(this.root);
			this.render();
			this.bindRealtime();
			this.refresh();
		}

		bindRealtime() {
			if (!frappe.realtime || this.realtimeBound) return;
			this.realtimeBound = true;
			// The server publishes only to authorized user rooms; the browser
			// never chooses or subscribes to operation rooms.
			frappe.realtime.on("operation_event", this.handleOperationEvent);
			frappe.realtime.on("connect", this.handleReconnect);
		}

		onOperationEvent(event) {
			if (
				!event ||
				!event.operation_id ||
				event.sequence === null ||
				event.sequence === undefined ||
				!Number.isInteger(Number(event.sequence)) ||
				Number(event.sequence) < 1
			) return;
			const operationId = event.operation_id;
			if (!this.operations.has(operationId)) {
				// A newly accepted operation may race the initial snapshot.
				this.refresh();
				return;
			}
			this.reconcileOperation(operationId);
		}

		async reconcileAll() {
			// refresh() also replays persisted history for every operation in the snapshot.
			await this.refresh();
		}

		async reconcileOperation(operationId) {
			if (this.replaying.has(operationId)) {
				this.replayQueued.add(operationId);
				return;
			}
			this.replaying.add(operationId);
			try {
				let cursor = this.lastSequences.get(operationId) || 0;
				let hasMore = true;
				while (hasMore) {
					const response = await frappe.call({
						method: EVENTS_API,
						args: { operation_id: operationId, after_sequence: cursor, limit: EVENT_BATCH_SIZE },
					});
					const page = response.message || {};
					const events = Array.isArray(page.events) ? page.events : [];
					for (const event of events) {
						const sequence = Number(event.sequence);
						if (!Number.isInteger(sequence) || sequence <= cursor) continue;
						if (sequence !== cursor + 1) {
							// Do not skip a durable event if the history page has a gap.
							throw new Error("Non-contiguous operation event history");
						}
						this.applyEvent(operationId, event);
						cursor = sequence;
						this.lastSequences.set(operationId, cursor);
					}
					hasMore = Boolean(page.has_more);
					if (!events.length) hasMore = false;
				}
			} catch (error) {
				// Snapshot remains usable; the next socket reconnect or refresh retries replay.
				console.warn("[dt_master] Unable to reconcile operation event history.");
			} finally {
				this.replaying.delete(operationId);
				if (this.replayQueued.delete(operationId)) {
					this.reconcileOperation(operationId);
				}
			}
		}

		applyEvent(operationId, event) {
			const operation = this.operations.get(operationId);
			if (!operation) return;
			const sequence = Number(event.sequence);
			if (sequence <= (this.lastSequences.get(operationId) || 0)) return;
			operation.execution_state = event.execution_state || operation.execution_state;
			operation.last_event_message = event.message || operation.last_event_message;
			operation.last_event_phase = event.phase || operation.last_event_phase;
			operation.last_event_at = event.occurred_at || operation.last_event_at;
			operation.progress = event.progress || operation.progress;
			operation.event_history = operation.event_history || [];
			operation.event_history.push({
				event_type: event.event_type,
				execution_state: event.execution_state,
				phase: event.phase,
				progress: event.progress,
				message: event.message,
				occurred_at: event.occurred_at,
			});
			operation.event_history = operation.event_history.slice(-10);
			operation.modified = event.occurred_at || operation.modified;
			if (event.event_type === "completed" || event.event_type === "failed" || event.event_type === "cancelled") {
				operation.completed_on = event.occurred_at || operation.completed_on;
			}
			this.lastSequences.set(operationId, sequence);
			this.render();
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
				const previous = this.operations;
				const next = new Map();
				snapshot.forEach((operation) => {
					if (operation && operation.operation_id) {
						const prior = previous.get(operation.operation_id);
						if (prior) {
							operation.event_history = prior.event_history || [];
							operation.last_event_message = prior.last_event_message;
							operation.last_event_phase = prior.last_event_phase;
							operation.last_event_at = prior.last_event_at;
							operation.progress = prior.progress;
						}
						next.set(operation.operation_id, operation);
					}
				});
				this.operations = next;
				for (const operationId of next.keys()) {
					if (!this.lastSequences.has(operationId)) this.lastSequences.set(operationId, 0);
				}
				for (const operationId of [...this.lastSequences.keys()]) {
					if (!next.has(operationId)) this.lastSequences.delete(operationId);
				}
				this.render();
				await Promise.all([...next.keys()].map((operationId) => this.reconcileOperation(operationId)));
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
			if (operation.last_event_message) {
				const message = document.createElement("p");
				message.className = "dt-task-manager__event-message";
				message.textContent = operation.last_event_message;
				card.appendChild(message);
			}
			if (operation.last_event_phase) {
				const phase = document.createElement("div");
				phase.className = "dt-task-manager__target";
				phase.textContent = operation.last_event_phase;
				card.appendChild(phase);
			}
			const progress = operation.progress;
			if (progress && Number.isFinite(Number(progress.completed)) && Number.isFinite(Number(progress.total)) &&
				Number(progress.total) > 0 && Number(progress.completed) >= 0 && Number(progress.completed) <= Number(progress.total)) {
				const progressWrap = document.createElement("div");
				progressWrap.className = "dt-task-manager__progress";
				const progressBar = document.createElement("progress");
				progressBar.max = Number(progress.total);
				progressBar.value = Number(progress.completed);
				progressBar.setAttribute("aria-label", __("Operation progress"));
				const progressLabel = document.createElement("span");
				progressLabel.textContent = `${progress.completed} / ${progress.total}${progress.unit ? ` ${progress.unit}` : ""}`;
				progressWrap.append(progressBar, progressLabel);
				card.appendChild(progressWrap);
			}
			const history = Array.isArray(operation.event_history) ? operation.event_history : [];
			if (history.length) {
				const details = document.createElement("details");
				details.className = "dt-task-manager__history";
				const summary = document.createElement("summary");
				summary.textContent = `${__("Event history")} (${history.length})`;
				const list = document.createElement("ol");
				history.slice().reverse().forEach((item) => {
					const entry = document.createElement("li");
					const eventLabel = document.createElement("strong");
					eventLabel.textContent = text(item.phase || item.event_type);
					entry.appendChild(eventLabel);
					if (item.message) {
						const eventMessage = document.createElement("p");
						eventMessage.textContent = item.message;
						entry.appendChild(eventMessage);
					}
					if (item.occurred_at) {
						const eventTime = document.createElement("time");
						eventTime.textContent = formatDate(item.occurred_at);
						entry.appendChild(eventTime);
					}
					list.appendChild(entry);
				});
				details.append(summary, list);
				card.appendChild(details);
			}
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
