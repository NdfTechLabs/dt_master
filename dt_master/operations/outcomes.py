"""Explicit durable operation dispositions for deterministic and uncertain outcomes."""

from __future__ import annotations


class OperationDispositionError(RuntimeError):
	"""Controlled terminal disposition raised by a leased operation handler."""

	DISPOSITIONS = frozenset({"Failed", "Recovery Required"})

	def __init__(
		self,
		disposition: str,
		code: str,
		summary: str,
		*,
		recovery_reason: str | None = None,
		adapter_result: dict | None = None,
	):
		if disposition not in self.DISPOSITIONS:
			raise ValueError("Unsupported operation disposition")
		if not isinstance(code, str) or not code.isupper() or not code.replace("_", "").isalnum():
			raise ValueError("Operation disposition code must be a stable uppercase identifier")
		if not isinstance(summary, str) or not summary.strip() or len(summary) > 500:
			raise ValueError("Operation disposition summary must be a bounded non-empty string")
		if disposition == "Recovery Required" and (
			not isinstance(recovery_reason, str) or not recovery_reason.strip() or len(recovery_reason) > 500
		):
			raise ValueError("Recovery Required disposition needs a bounded recovery reason")
		if disposition == "Failed" and recovery_reason is not None:
			raise ValueError("Failed disposition must not carry a recovery reason")
		super().__init__(summary)
		self.disposition = disposition
		self.code = code
		self.summary = summary.strip()
		self.recovery_reason = recovery_reason.strip() if recovery_reason else None
		self.adapter_result = adapter_result


def enforce_durable_operation_outcome(
	result: dict,
	*,
	failed_code: str,
	failed_summary: str,
	recovery_code: str,
	recovery_summary: str,
) -> dict:
	"""Prevent partial adapter results from being committed as durable success."""
	if not isinstance(result, dict):
		raise ValueError("Runtime adapter result must be an object")
	outcome = result.get("outcome")
	if outcome == "succeeded":
		return result
	if outcome == "failed":
		raise OperationDispositionError("Failed", failed_code, failed_summary, adapter_result=result)
	if outcome == "partial":
		raise OperationDispositionError(
			"Recovery Required",
			recovery_code,
			recovery_summary,
			recovery_reason=recovery_summary,
			adapter_result=result,
		)
	raise ValueError("Runtime adapter returned an unsupported durable outcome")


def _adapter_failure_summary(adapter_result: dict | None, fallback: str) -> str:
	"""Build a concise, safe user-facing summary from structured adapter failure data."""
	if not isinstance(adapter_result, dict):
		return fallback
	data = adapter_result.get("data")
	failure = data.get("failure") if isinstance(data, dict) else None
	if not isinstance(failure, dict):
		return fallback

	reason = failure.get("reason")
	if not isinstance(reason, str) or not reason.strip():
		return fallback

	# Adapter failure reasons are already sanitized at the adapter boundary.
	# Sanitize again here because this is the durable operation's public summary.
	from dt_master.runtime_adapters.contracts import sanitize_diagnostic_message

	reason = sanitize_diagnostic_message(reason).strip()
	phase = failure.get("phase")
	if isinstance(phase, str) and phase.strip():
		summary = f"{phase.replace('_', ' ').capitalize()}: {reason}"
	else:
		summary = reason
	return summary[:500] or fallback


def resolve_operation_disposition(exc: Exception, *, lease_acquired: bool) -> dict:
	"""Return the bounded durable fields used by the worker's exception path."""
	if isinstance(exc, OperationDispositionError):
		recovery_required = exc.disposition == "Recovery Required"
		return {
			"execution_state": exc.disposition,
			"error_code": exc.code,
			"error_summary": _adapter_failure_summary(exc.adapter_result, exc.summary),
			"recovery_required": recovery_required,
			"recovery_reason": exc.recovery_reason,
			"result_payload": exc.adapter_result,
		}
	recovery_required = bool(lease_acquired)
	return {
		"execution_state": "Recovery Required" if recovery_required else "Failed",
		"error_code": "LEASE_OR_HANDLER_FAILED" if recovery_required else "HANDLER_FAILED",
		"error_summary": (
			"Execution stopped after a resource lease or handler failure; reconcile the target before retrying."
			if recovery_required
			else "The registered operation handler failed. See the restricted server error log."
		),
		"recovery_required": recovery_required,
		"recovery_reason": (
			"Resource lease ownership or execution outcome requires reconciliation."
			if recovery_required
			else None
		),
		"result_payload": None,
	}
