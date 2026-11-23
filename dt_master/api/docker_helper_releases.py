"""Operator-facing setup actions for the Docker Helper Release catalog."""

import re
from pathlib import Path

import frappe

from dt_master.docker_helper_releases import (
	DockerHelperReleaseError,
	create_draft_release_from_template,
	create_initial_draft_release,
)


def _require_node_release_setup_permission(node_name):
	if "System Manager" not in frappe.get_roles():
		frappe.throw("Only System Managers can create Docker Helper Releases.", frappe.PermissionError)
	node = frappe.get_doc("Infrastructure Node", node_name)
	if not node.has_permission("write"):
		frappe.throw("You do not have permission to modify this Infrastructure Node.", frappe.PermissionError)
	if node.docker_helper_release:
		frappe.throw("Clear the node's current Docker Helper Release assignment before setting up a replacement.")
	return node


@frappe.whitelist()
def get_unassigned_node_release_setup(node_name):
	"""Inspect the catalog and return the next safe setup action for an unassigned node."""
	_require_node_release_setup_permission(node_name)
	published = frappe.get_all(
		"Docker Helper Release",
		filters={"status": "Published"},
		fields=["name", "release_id", "version"],
		order_by="modified desc",
	)
	if published:
		return {"action": "assign_published", "published_releases": published}

	drafts = frappe.get_all(
		"Docker Helper Release",
		filters={"status": "Draft"},
		fields=["name", "release_id", "version"],
		order_by="modified desc",
	)
	if drafts:
		return {"action": "review_draft", "draft": drafts[0], "drafts": drafts}

	retired = frappe.get_all(
		"Docker Helper Release",
		filters={"status": "Retired"},
		fields=["name", "release_id", "version"],
		order_by="modified desc",
		limit=1,
	)
	return {
		"action": "create_replacement" if retired else "create_initial",
		"template": retired[0] if retired else None,
	}


@frappe.whitelist()
def create_draft_for_unassigned_node(node_name, version):
	"""Create a replacement or initial Draft without changing any existing release."""
	node = _require_node_release_setup_permission(node_name)
	if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version):
		frappe.throw("Release version must use semantic versioning.")

	published = frappe.get_all(
		"Docker Helper Release",
		filters={"status": "Published"},
		fields=["name"],
		limit=1,
	)
	if published:
		return {"action": "assign_published", "message": "A Published release already exists."}

	drafts = frappe.get_all(
		"Docker Helper Release",
		filters={"status": "Draft"},
		fields=["name", "release_id", "version"],
		order_by="modified desc",
	)
	if drafts:
		return {"action": "review_draft", "draft": drafts[0]}

	retired = frappe.get_all(
		"Docker Helper Release",
		filters={"status": "Retired"},
		fields=["name"],
		order_by="modified desc",
		limit=1,
	)
	release_id = f"docker-helper-{version}"
	source_root = Path(__file__).resolve().parents[1]
	try:
		if retired:
			template = frappe.get_doc("Docker Helper Release", retired[0].name)
			draft, created = create_draft_release_from_template(
				template,
				source_root,
				version=version,
				release_id=release_id,
			)
		else:
			draft, created = create_initial_draft_release(
				source_root,
				version=version,
				release_id=release_id,
			)
	except DockerHelperReleaseError as exc:
		frappe.throw(str(exc))

	return {
		"action": "draft_created" if created else "review_draft",
		"draft": {"name": draft.name, "release_id": draft.release_id, "version": draft.version},
		"created": created,
	}
