# Copyright (c) 2026, NDF Technology Labs Solutions LTD
# For license information, please see license.txt

import json

import frappe


def execute():
	"""Seed the existing reviewed 0.3.2 helper release into the authoritative catalog."""
	release_id = "dt-docker-helper-0.3.2"
	if frappe.db.exists("Docker Helper Release", release_id):
		return

	release = frappe.new_doc("Docker Helper Release")
	release.release_id = release_id
	release.schema_version = "1.0.0"
	release.version = "0.3.2"
	release.channel = "stable"
	release.status = "Published"
	release.compatibility = json.dumps({"helper_protocol":{"minimum":"1.0.0","maximum":"1.0.0"},"privilege_readiness_contract":{"minimum":"1.0.0","maximum":"1.0.0"},"docker_install_plan":{"minimum":"1.1.0","maximum":"1.1.0"},"docker_install_policy":{"minimum":"1.1.1","maximum":"1.1.1"},"inventory_schema":{"minimum":"1.0.0","maximum":"1.0.0"},"upgrade_from":["0.3.0","0.3.1","0.3.2"]}, separators=(",", ":"))
	release.lifecycle = json.dumps({"bootstrap":"Install the release when no recognized helper release is present.","upgrade":"Replace a recognized older release with this complete release without repeating host preparation.","reconcile":"Repair only recognized, individually verifiable release artifacts; unknown or untrusted artifacts require operator intervention.","activation_order":["install_policy","host_inventory","lifecycle_helper"],"verification":"A release is current only when every required component digest, owner, group, mode, version and compatibility requirement passes."}, separators=(",", ":"))

	for component_id, filename, version, responsibility in (
		(
			"lifecycle_helper",
			"docker_lifecycle_helper.py",
			"0.3.2",
			"Root-owned privileged operation boundary; validates closed requests, verifies installed release dependencies and executes only explicitly supported helper operations.",
		),
		(
			"install_policy",
			"docker_install_policy.py",
			"1.1.1",
			"Pure deterministic Docker Engine installation planner and canonical host-state fingerprint implementation.",
		),
		(
			"host_inventory",
			"docker_install_inventory.py",
			"1.0.0",
			"Read-only host, package, repository, signing-key, service and workload inventory consumed by the planner and privileged helper.",
		),
	):
		release.append(
			"components",
			{
				"component_id": component_id,
				"version": version,
				"source_path": f"scripts/bin/docker/{filename}",
				"install_path": f"/usr/local/lib/dt-master/docker/{filename}",
				"required": 1,
				"file_owner": "root",
				"file_group": "root",
				"file_mode": "0755",
				"responsibility": responsibility,
			},
		)
	release.insert(ignore_permissions=True)
