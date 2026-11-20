"""Tests for the read-only Docker package update metadata probe."""
import unittest
from unittest.mock import patch
from dt_master.scripts.bin.docker import check_docker_updates

class TestDockerUpdateMetadata(unittest.TestCase):
    def test_reports_update_available(self):
        update = {"name": "docker-ce", "installed_version": "29.0", "candidate_version": "29.1",
                  "candidate_origin": "https://download.docker.com/linux/ubuntu", "status": "Update Available"}
        current = [{"name": n, "installed_version": "1", "candidate_version": "1",
                    "candidate_origin": None, "status": "Up To Date"} for n in check_docker_updates.PACKAGES[1:]]
        with patch.object(check_docker_updates.shutil, "which", return_value="/usr/bin/apt-cache"), \
             patch.object(check_docker_updates, "_metadata_age", return_value={"status": "Fresh", "age_seconds": 60, "newest_index_at": "2026-11-20T10:00:00Z"}), \
             patch.object(check_docker_updates, "_policy", side_effect=[update] + current):
            result = check_docker_updates.collect()
        self.assertEqual(result["overall_status"], "Updates Available")

    def test_stale_metadata_never_claims_up_to_date(self):
        row = {"name": "docker-ce", "installed_version": "1", "candidate_version": "1",
               "candidate_origin": None, "status": "Up To Date"}
        with patch.object(check_docker_updates.shutil, "which", return_value="/usr/bin/apt-cache"), \
             patch.object(check_docker_updates, "_metadata_age", return_value={"status": "Stale", "age_seconds": 900000, "newest_index_at": None}), \
             patch.object(check_docker_updates, "_policy", return_value=row):
            result = check_docker_updates.collect()
        self.assertEqual(result["overall_status"], "Unknown")

    def test_unsupported_package_manager_is_nonfatal(self):
        with patch.object(check_docker_updates.shutil, "which", return_value=None), \
             patch.object(check_docker_updates, "_metadata_age", return_value={"status": "Unavailable", "age_seconds": None, "newest_index_at": None}):
            result = check_docker_updates.collect()
        self.assertEqual(result["overall_status"], "Unsupported")
        self.assertEqual(result["packages"], [])

    def test_policy_parser_handles_missing_candidate(self):
        with patch.object(check_docker_updates, "_run", return_value=(0, "docker-ce:\n  Installed: 1.0\n  Candidate: (none)\n  Version table:\n")):
            result = check_docker_updates._policy("docker-ce")
        self.assertEqual(result["status"], "No Candidate")

if __name__ == "__main__":
    unittest.main()
