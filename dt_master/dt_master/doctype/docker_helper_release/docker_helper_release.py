# Copyright (c) 2026, NDF Technology Labs Solutions LTD
# For license information, please see license.txt

from pathlib import Path

from frappe.model.document import Document
from frappe.utils import now_datetime

from dt_master.docker_helper_releases import (
	DockerHelperReleaseError,
	refresh_component_digests,
	validate_release_metadata,
)

RELEASE_DEFINITION_FIELDS = ("release_id", "schema_version", "version", "channel", "compatibility", "lifecycle", "components", "aggregate_digest")


def _definition(doc):
    components = []
    for row in doc.get("components") or []:
        components.append(
            {
                field: row.get(field)
                for field in (
                    "component_id",
                    "version",
                    "source_path",
                    "install_path",
                    "required",
                    "file_owner",
                    "file_group",
                    "file_mode",
                    "sha256",
                    "responsibility",
                )
            }
        )
    return {
        "release_id": doc.get("release_id"),
        "schema_version": doc.get("schema_version"),
        "version": doc.get("version"),
        "channel": doc.get("channel"),
        "compatibility": doc.get("compatibility"),
        "lifecycle": doc.get("lifecycle"),
        "components": sorted(components, key=lambda item: item["component_id"] or ""),
        "aggregate_digest": doc.get("aggregate_digest"),
    }


class DockerHelperRelease(Document):
    def validate(self):
        previous = self.get_doc_before_save()
        if previous and previous.status in {"Published", "Retired"}:
            self._validate_published_immutability(previous)
            return

        self._validate_metadata()
        self._refresh_digests()
        if self.status == "Published":
            self.published_on = now_datetime()

    def _validate_metadata(self):
        import re

        if not self.release_id or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,99}", self.release_id):
            self._throw("Release ID must contain 3–100 lowercase letters, numbers, dots, underscores or hyphens.")
        if not self.version or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", self.version):
            self._throw("Release version must use semantic versioning.")
        if self.schema_version != "1.0.0":
            self._throw("Unsupported Docker Helper Release manifest schema version.")
        try:
            validate_release_metadata(self.compatibility, self.lifecycle)
        except DockerHelperReleaseError as exc:
            self._throw(str(exc))
        if self.channel not in {"stable", "beta", "edge"}:
            self._throw("Invalid Docker Helper Release channel.")
        if self.status not in {"Draft", "Published"}:
            self._throw("Invalid Docker Helper Release status.")

    def _refresh_digests(self):
        try:
            source_root = Path(__file__).resolve().parents[3]
            self.aggregate_digest = refresh_component_digests(self.components or [], source_root)
        except DockerHelperReleaseError as exc:
            self._throw(str(exc))

    def _validate_published_immutability(self, previous):
        if previous.status == "Retired" and self.status != "Retired":
            self._throw("A retired Docker Helper Release cannot be reactivated.")
        if self.status not in {"Published", "Retired"}:
            self._throw("A published Docker Helper Release cannot return to Draft.")
        if _definition(previous) != _definition(self):
            self._throw("Published Docker Helper Releases are immutable. Create a new release instead.")
        if previous.status == "Published" and self.status == "Retired":
            self.retired_on = now_datetime()

    @staticmethod
    def _throw(message):
        import frappe

        frappe.throw(frappe._(message), title=frappe._("Invalid Docker Helper Release"))
