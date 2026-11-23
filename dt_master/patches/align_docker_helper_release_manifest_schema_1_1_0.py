# Copyright (c) 2026, NDF Technology Labs Solutions LTD
# For license information, please see license.txt

import json

import frappe


ACCEPTED_PREDECESSORS = {
	"lifecycle_helper": [
		"b9deb8114d1efde9ef23f37dc3e327a913d5f0bcbfecc7ce9a2780e426391a9b",
		"1cf27d95c853ce1b46bbb47cc3982baa1f7dfddbe123909c7c5e564d1eaed8fe",
		"8ec811c5ef0ae8c6f3a7733bb17cdbd088d9b5db9ae206f942b0e9267a34bf2a",
		"afd072cd7d80a0182351ea6682779f703b95beef6c8695d8cee2a96dd59e9fa9",
		"06aa9fa0cf7218495b0dfde5667ae50ba00ed85ed45054606939d89fae2c47a2",
	],
	"install_policy": ["3fb6c49de806d380d5101679ae0c7364957d6686df5c09ff4a60c453297797f0"],
	"host_inventory": [
		"99863a6d5d78c95ba3111d6574c3ae5c3bd653de004b24c0859904f86ac641a6",
		"f558523497595bcd890450405789eda7d418807160c2fab8f4c11758f636502b",
	],
}


def execute():
	"""Upgrade editable Drafts only; immutable Published/Retired definitions remain untouched."""
	for release_name in frappe.get_all(
		"Docker Helper Release",
		filters={"status": "Draft"},
		pluck="name",
	):
		release = frappe.get_doc("Docker Helper Release", release_name)
		release.schema_version = "1.1.0"
		for component in release.components:
			component.accepted_predecessor_sha256 = json.dumps(
				ACCEPTED_PREDECESSORS.get(component.component_id, []),
				separators=(",", ":"),
			)
		release.save(ignore_permissions=True)
