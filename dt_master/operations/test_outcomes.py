"""Tests for explicit durable operation outcome handling."""

import unittest

from dt_master.operations.outcomes import (
	OperationDispositionError,
	enforce_durable_operation_outcome,
	resolve_operation_disposition,
)


class TestOperationOutcomes(unittest.TestCase):
	def test_successful_adapter_result_returns_for_worker_completion(self):
		result = {"outcome": "succeeded", "data": {"verified": True}}
		self.assertIs(
			enforce_durable_operation_outcome(
				result,
				failed_code="MUTATION_FAILED",
				failed_summary="Mutation failed.",
				recovery_code="MUTATION_UNCERTAIN",
				recovery_summary="Mutation requires reconciliation.",
			),
			result,
		)

	def test_failed_adapter_result_becomes_durable_failure(self):
		with self.assertRaises(OperationDispositionError) as raised:
			enforce_durable_operation_outcome(
				{"outcome": "failed", "data": {"failure": {"phase": "repository_preparation"}}},
				failed_code="MUTATION_FAILED",
				failed_summary="Mutation failed before execution.",
				recovery_code="MUTATION_UNCERTAIN",
				recovery_summary="Mutation requires reconciliation.",
			)
		self.assertEqual(raised.exception.disposition, "Failed")
		self.assertEqual(raised.exception.code, "MUTATION_FAILED")
		self.assertIsNone(raised.exception.recovery_reason)
		self.assertEqual(
			raised.exception.adapter_result["data"]["failure"]["phase"],
			"repository_preparation",
		)

	def test_partial_adapter_result_becomes_recovery_required(self):
		with self.assertRaises(OperationDispositionError) as raised:
			enforce_durable_operation_outcome(
				{"outcome": "partial", "data": {"failure": {"phase": "package_install"}}},
				failed_code="MUTATION_FAILED",
				failed_summary="Mutation failed.",
				recovery_code="MUTATION_UNCERTAIN",
				recovery_summary="Mutation requires reconciliation.",
			)
		self.assertEqual(raised.exception.disposition, "Recovery Required")
		self.assertEqual(raised.exception.code, "MUTATION_UNCERTAIN")
		self.assertEqual(raised.exception.recovery_reason, "Mutation requires reconciliation.")
		fields = resolve_operation_disposition(raised.exception, lease_acquired=True)
		self.assertEqual(fields["result_payload"]["data"]["failure"]["phase"], "package_install")

	def test_unknown_adapter_outcome_is_rejected(self):
		with self.assertRaises(ValueError):
			enforce_durable_operation_outcome(
				{"outcome": "unknown"},
				failed_code="MUTATION_FAILED",
				failed_summary="Mutation failed.",
				recovery_code="MUTATION_UNCERTAIN",
				recovery_summary="Mutation requires reconciliation.",
			)

	def test_explicit_failure_remains_failed_after_lease_acquisition(self):
		exc = OperationDispositionError("Failed", "PLAN_STALE", "Approved plan is stale.")
		fields = resolve_operation_disposition(exc, lease_acquired=True)
		self.assertEqual(fields["execution_state"], "Failed")
		self.assertFalse(fields["recovery_required"])

	def test_explicit_uncertainty_requires_recovery(self):
		exc = OperationDispositionError(
			"Recovery Required",
			"HELPER_UNCERTAIN",
			"Helper outcome is uncertain.",
			recovery_reason="Fresh host reconciliation is required.",
		)
		fields = resolve_operation_disposition(exc, lease_acquired=True)
		self.assertEqual(fields["execution_state"], "Recovery Required")
		self.assertTrue(fields["recovery_required"])

	def test_unclassified_exception_after_lease_requires_recovery(self):
		fields = resolve_operation_disposition(RuntimeError("internal"), lease_acquired=True)
		self.assertEqual(fields["execution_state"], "Recovery Required")

	def test_unclassified_exception_before_lease_is_failed(self):
		fields = resolve_operation_disposition(RuntimeError("internal"), lease_acquired=False)
		self.assertEqual(fields["execution_state"], "Failed")

	def test_disposition_contract_is_closed_and_bounded(self):
		with self.assertRaises(ValueError):
			OperationDispositionError("Succeeded", "OK", "Completed")
		with self.assertRaises(ValueError):
			OperationDispositionError("Recovery Required", "UNCERTAIN", "Needs review")
		with self.assertRaises(ValueError):
			OperationDispositionError("Failed", "FAILED", "Failed", recovery_reason="Not allowed")


if __name__ == "__main__":
	unittest.main()
