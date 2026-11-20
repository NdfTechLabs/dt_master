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
			this.loadingOperations = new Set();
			this.collapsed = localStorage.getItem(STORAGE_KEY) === "1";
			this.root = null;
			this.refreshing = false;
			this.refreshQueued = false;
			this.lastSequences = new Map();
			this.replaying = new Set();
			this.replayQueued = new Set();
			this.realtimeEventCount = 0;
			this.lastRealtimeEvent = null;
			this.handleOperationEvent = (event) => {
				this.realtimeEventCount += 1;
				this.lastRealtimeEvent = event || null;
				try {
					this.onOperationEvent(event);
				} catch (error) {
					console.error("[dt_master] Task Manager failed to process an operation event.", error);
				}
			};
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
			if (this.realtimeBound) return true;
			const realtime = frappe.realtime;
			if (!realtime || typeof realtime.on !== "function") return false;

			// Frappe's RealTimeClient.on() silently does nothing until init()
			// has created its Socket.IO socket. Hook init once so this manager
			// binds immediately after socket creation instead of marking itself
			// bound to a subscription that was never registered.
			if (!realtime.socket) {
				if (!realtime.__dtMasterTaskManagerInitHooked && typeof realtime.init === "function") {
					const taskManager = this;
					const originalInit = realtime.init;
					realtime.init = function (...args) {
						const result = originalInit.apply(this, args);
						if (this.socket) taskManager.bindRealtime();
						return result;
					};
					realtime.__dtMasterTaskManagerInitHooked = true;
				}
				return false;
			}

			// The server publishes only to authorized user rooms; the browser
			// never chooses or subscribes to operation rooms.
			realtime.on("operation_event", this.handleOperationEvent);
			realtime.on("connect", this.handleReconnect);
			this.realtimeBound = true;
			return true;
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
			const operationId = String(event.operation_id);
			if (!this.operations.has(operationId)) {
				// Insert synchronously before any asynchronous request so the first
				// authorized event immediately creates a visible task card.
				this.operations.set(operationId, {
					operation_id: operationId,
					correlation_id: event.correlation_id,
					operation_type: __("Background operation"),
					target_doctype: null,
					target_name: null,
					execution_state: "Pending Dispatch",
					creation: event.occurred_at || null,
					modified: event.occurred_at || null,
					event_history: [],
				});
				this.realtimeDiscovered.add(operationId);
				this.lastSequences.set(operationId, 0);
				this.render();
				this.loadOperation(operationId);
			}
			const sequence = Number(event.sequence);
			const cursor = this.lastSequences.get(operationId) || 0;
			if (sequence <= cursor) return;
			if (sequence !== cursor + 1) {
				if (this.replaying.has(operationId)) this.replayQueued.add(operationId);
				else this.reconcileOperation(operationId);
				return;
			}
			this.applyEvent(operationId, event);
		}

		async reconcileAll() {
			// refresh() also replays persisted history for every operation in the snapshot.
			await this.refresh();
		}

		async loadOperation(operationId) {
			if (this.loadingOperations.has(operationId)) return;
			this.loadingOperations.add(operationId);
			try {
				const response = await frappe.call({
					method: "dt_master.api.operations.get_operation",
					args: { operation_id: operationId },
				});
				const operation = response.message;
				if (!operation || !operation.operation_id) return;
				const current = this.operations.get(operationId) || {};
				const hasLiveEvents = (this.lastSequences.get(operationId) || 0) > 0;
				this.operations.set(operationId, {
					...operation,
					...current,
					operation_id: operationId,
					creation: current.creation || operation.creation || operation.started_on || null,
					modified: current.modified || operation.modified || operation.completed_on || operation.started_on || null,
					execution_state: hasLiveEvents ? current.execution_state : operation.execution_state,
					result: operation.result ?? current.result ?? null,
					event_history: current.event_history || [],
				});
				this.realtimeDiscovered.add(operationId);
				if (!this.lastSequences.has(operationId)) this.lastSequences.set(operationId, 0);
				this.render();
			} catch (error) {
				// A later list refresh/reconnect can recover if the event raced
				// record visibility or the caller is no longer authorized.
				console.warn("[dt_master] Unable to load newly announced operation.");
				this.refresh();
			} finally {
				this.loadingOperations.delete(operationId);
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
			if (event.event_type === "completed") this.loadOperation(operationId);
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
				for (const [operationId, operation] of next) {
					const current = this.operations.get(operationId);
					if (!current) continue;
					const hasLiveEvents = (this.lastSequences.get(operationId) || 0) > 0;
					next.set(operationId, {
						...operation,
						...current,
						operation_id: operationId,
						execution_state: hasLiveEvents ? current.execution_state : operation.execution_state,
						event_history: current.event_history || [],
					});
				}
				for (const operationId of this.realtimeDiscovered) {
					if (!next.has(operationId) && this.operations.has(operationId)) {
						next.set(operationId, this.operations.get(operationId));
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
			const byNewest = (a, b) => String(b.modified || b.creation || "")
				.localeCompare(String(a.modified || a.creation || ""));
			const groups = [
				{ label: __("Queued"), states: ["Pending Dispatch", "Queued", "Retry Waiting"] },
				{ label: __("Recovery Required"), states: ["Recovery Required"] },
				{ label: __("Running"), states: ["Running", "Cancellation Requested"] },
				{ label: __("Failed"), states: ["Failed"] },
				{ label: __("Succeeded"), states: ["Succeeded"] },
				{ label: __("Cancelled"), states: ["Cancelled"] },
			];
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
			let visibleCount = 0;
			for (const group of groups) {
				const items = operations
					.filter((item) => group.states.includes(item.execution_state))
					.sort(byNewest);
				if (!items.length) continue;
				visibleCount += items.length;
				body.appendChild(this.section(group.label, items));
			}
			const knownStates = new Set(groups.flatMap((group) => group.states));
			const other = operations.filter((item) => !knownStates.has(item.execution_state)).sort(byNewest);
			if (other.length) {
				visibleCount += other.length;
				body.appendChild(this.section(__("Other"), other));
			}
			if (!visibleCount) {
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
			if (operation.result && typeof operation.result === "object") {
				const resultDetails = document.createElement("details");
				resultDetails.className = "dt-task-manager__history";
				const resultSummary = document.createElement("summary");
				resultSummary.textContent = __("Execution result");
				const resultBody = document.createElement("pre");
				resultBody.className = "dt-task-manager__result";
				resultBody.textContent = JSON.stringify(operation.result, null, 2);
				resultBody.style.whiteSpace = "pre-wrap";
				resultBody.style.overflowWrap = "anywhere";
				resultDetails.append(resultSummary, resultBody);
				card.appendChild(resultDetails);
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
