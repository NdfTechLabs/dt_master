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
			this.realtimeDiscovered = new Set();
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
				// The accepted/queued event can arrive before the list snapshot
				// includes the new operation. Fetch the authorized record directly
				// instead of relying on a possibly in-flight list refresh.
				this.loadOperation(operationId);
				return;
			}
			this.reconcileOperation(operationId);
		}

		async reconcileAll() {
			// refresh() also replays persisted history for every operation in the snapshot.
			await this.refresh();
		}

		async loadOperation(operationId) {
			try {
				const response = await frappe.call({
					method: "dt_master.api.operations.get_operation",
					args: { operation_id: operationId },
				});
				const operation = response.message;
				if (!operation || !operation.operation_id) return;
				this.operations.set(operationId, {
					...operation,
					creation: operation.creation || operation.started_on || null,
					modified: operation.completed_on || operation.started_on || null,
				});
				if (!this.lastSequences.has(operationId)) this.lastSequences.set(operationId, 0);
				this.render();
				await this.reconcileOperation(operationId);
			} catch (error) {
				// A later list refresh/reconnect can recover if the event raced
				// record visibility or the caller is no longer authorized.
				console.warn("[dt_master] Unable to load newly announced operation.");
				this.refresh();
			}
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
			operation.event_history = operation.event_history || [];
			operation.event_history.push({
				sequence,
				event_type: event.event_type,
				execution_state: event.execution_state,
				occurred_at: event.occurred_at,
				phase: event.phase,
				message: event.message,
				progress: event.progress || null,
			});
			operation.event_history = operation.event_history.slice(-10);
			operation.last_event_at = event.occurred_at || operation.last_event_at;
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
				const next = new Map();
				snapshot.forEach((operation) => {
					if (operation && operation.operation_id) {
						next.set(operation.operation_id, operation);
					}
				});
				// A snapshot can have started before a realtime event announced a
				// newly-created operation. Preserve those discoveries when the
				// older snapshot resolves instead of overwriting the newer state.
				for (const operationId of this.realtimeDiscovered) {
					if (!next.has(operationId) && this.operations.has(operationId)) {
						next.set(operationId, this.operations.get(operationId));
					} else if (next.has(operationId)) {
						this.realtimeDiscovered.delete(operationId);
					}
				}
				for (const [operationId, operation] of next) {
					const current = this.operations.get(operationId);
					if (current?.event_history?.length) {
						operation.event_history = current.event_history;
						operation.last_event_message = current.last_event_message;
						operation.last_event_phase = current.last_event_phase;
						operation.last_event_at = current.last_event_at;
					}
				}
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
			if (operation.event_history?.length) {
				const history = document.createElement("details");
				history.className = "dt-task-manager__history";
				const summary = document.createElement("summary");
				summary.textContent = __(
					"Event history ({0})",
					[operation.event_history.length]
				);
				history.appendChild(summary);
				const list = document.createElement("ol");
				list.className = "dt-task-manager__history-list";
				[...operation.event_history].reverse().forEach((event) => {
					const item = document.createElement("li");
					const heading = document.createElement("strong");
					heading.textContent = text(event.message || event.event_type);
					item.appendChild(heading);
					const metadata = document.createElement("div");
					metadata.className = "dt-task-manager__target";
					metadata.textContent = [
						event.phase,
						event.execution_state,
						formatDate(event.occurred_at),
					].filter(Boolean).join(" · ");
					item.appendChild(metadata);
					if (event.progress && Number.isFinite(Number(event.progress.completed)) &&
						Number.isFinite(Number(event.progress.total)) && Number(event.progress.total) > 0) {
						const progress = document.createElement("div");
						progress.className = "dt-task-manager__target";
						progress.textContent = __("Progress: {0} / {1}", [
							Number(event.progress.completed), Number(event.progress.total),
						]);
						item.appendChild(progress);
					}
					list.appendChild(item);
				});
				history.appendChild(list);
				card.appendChild(history);
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
