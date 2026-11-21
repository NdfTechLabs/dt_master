"""Tests for explicit durable operation outcome handling."""

import unittest

from dt_master.operations.outcomes import (
	OperationDispositionError,
	enforce_durable_operation_outcome,
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
				{"outcome": "failed"},
				failed_code="MUTATION_FAILED",
				failed_summary="Mutation failed before execution.",
				recovery_code="MUTATION_UNCERTAIN",
				recovery_summary="Mutation requires reconciliation.",
			)
		self.assertEqual(raised.exception.disposition, "Failed")
		self.assertEqual(raised.exception.code, "MUTATION_FAILED")
		self.assertIsNone(raised.exception.recovery_reason)

	def test_partial_adapter_result_becomes_recovery_required(self):
		with self.assertRaises(OperationDispositionError) as raised:
			enforce_durable_operation_outcome(
				{"outcome": "partial"},
				failed_code="MUTATION_FAILED",
				failed_summary="Mutation failed.",
				recovery_code="MUTATION_UNCERTAIN",
				recovery_summary="Mutation requires reconciliation.",
			)
		self.assertEqual(raised.exception.disposition, "Recovery Required")
		self.assertEqual(raised.exception.code, "MUTATION_UNCERTAIN")
		self.assertEqual(raised.exception.recovery_reason, "Mutation requires reconciliation.")

	def test_unknown_adapter_outcome_is_rejected(self):
		with self.assertRaises(ValueError):
			enforce_durable_operation_outcome(
				{"outcome": "unknown"},
				failed_code="MUTATION_FAILED",
				failed_summary="Mutation failed.",
				recovery_code="MUTATION_UNCERTAIN",
				recovery_summary="Mutation requires reconciliation.",
			)

	def test_disposition_contract_is_closed_and_bounded(self):
		with self.assertRaises(ValueError):
			OperationDispositionError("Succeeded", "OK", "Completed")
		with self.assertRaises(ValueError):
			OperationDispositionError("Recovery Required", "UNCERTAIN", "Needs review")
		with self.assertRaises(ValueError):
			OperationDispositionError("Failed", "FAILED", "Failed", recovery_reason="Not allowed")


if __name__ == "__main__":
	unittest.main()
