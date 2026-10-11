"""N120 metadata binding over the unchanged generation-fixed lifecycle view.

Root supplies exact existing archive/definition bytes and retained GCS pins.
Only a new create-only case/manifest.json binds the existing complete13 CAS.
This module does not upload, admit a provider, promote, evict or start a Model.
"""
from __future__ import annotations

import base64

import n30_generation_fixed_lifecycle_adapter as fixed


INDEX_SCHEMA = "dams-n120-lifecycle-promotion-index-v1"


class N120GenerationFixedCaseView(fixed.GenerationFixedCaseView):
    """Reuse every object read, generation, EOF, meter and live-authority guard."""

    def __init__(self, backend, *, case_id, archive_bytes, archive_sha256,
                 definition_bytes, definition_sha256, recovery_index_pin,
                 remote_pins, guard, deadline_monotonic,
                 max_total_bytes, max_read_calls):
        fixed.require(isinstance(case_id, str) and fixed.SHA.fullmatch(case_id),
                      "case ID differs")
        fixed.require(type(definition_bytes) is bytes
                      and 0 < len(definition_bytes) <= fixed.MAX_INDEX
                      and isinstance(definition_sha256, str)
                      and fixed.SHA.fullmatch(definition_sha256)
                      and fixed.digest(definition_bytes) == definition_sha256,
                      "original definition pin differs")
        definition = fixed.parse(definition_bytes)
        fixed.require(isinstance(definition, dict)
                      and definition.get("case_id") == case_id,
                      "original definition belongs to another case")
        recovery_key = case_id + "/index.json"
        recovery_pin = fixed.fixed_pin(recovery_index_pin, recovery_key, fixed.MAX_INDEX)
        manifest_key = case_id + "/manifest.json"
        fixed.require(isinstance(remote_pins, dict) and manifest_key in remote_pins,
                      "N120 promotion metadata pin required")
        manifest_pin = fixed.fixed_pin(remote_pins[manifest_key], manifest_key, fixed.MAX_INDEX)
        # The base constructor checks the exact original complete13/object roster.
        # It performs no provider reads. Its historical metadata pin is replaced
        # only after those checks, without aliasing or changing any backend pin.
        original_pins = dict(remote_pins)
        del original_pins[manifest_key]
        fixed.require(recovery_key not in original_pins,
                      "promotion view must not contain a second metadata pin")
        original_pins[recovery_key] = recovery_pin
        super().__init__(backend, case_id=case_id, archive_bytes=archive_bytes,
                         archive_sha256=archive_sha256, remote_pins=original_pins,
                         guard=guard, deadline_monotonic=deadline_monotonic,
                         max_total_bytes=max_total_bytes, max_read_calls=max_read_calls)
        fixed.require(self.definition_sha256 == definition_sha256,
                      "archive and exact original definition pin differ")
        self.definition_bytes = definition_bytes
        self.recovery_key = recovery_key
        self.recovery_index_pin = recovery_pin
        del self.pins[recovery_key]
        self.pins[manifest_key] = manifest_pin
        self.index_key = manifest_key
        self.immutable_locator = (self.binding["locator"] + "/" + manifest_key
            + "#generation=" + manifest_pin["generation"]
            + ";metadata_sha256=" + manifest_pin["sha256"]
            + ";archive_sha256=" + archive_sha256)
        self._check_recovery_pin()

    def _check_recovery_pin(self):
        self._live()
        try:
            actual = fixed.fixed_pin(self.backend._pin(self.recovery_key),
                                     self.recovery_key, fixed.MAX_INDEX)
        except BaseException:
            raise fixed.AdapterRefusal("retained recovery index pin refused") from None
        fixed.require(actual == self.recovery_index_pin,
                      "retained recovery index generation pin drift")
        self._live()

    def read_manifest(self):
        self._check_recovery_pin()
        metadata = fixed.parse(self._read_fixed(self.index_key))
        fixed.require(isinstance(metadata, dict) and set(metadata) == {
            "schema", "case", "archive_base64", "definition_base64", "source_recovery_index_pin"}
            and metadata["schema"] == INDEX_SCHEMA
            and metadata["case"] == {"case_id": self.case_id}
            and metadata["source_recovery_index_pin"] == self.recovery_index_pin,
            "N120 promotion index case/schema/recovery binding differs")
        try:
            archive = base64.b64decode(metadata["archive_base64"], validate=True)
            definition = base64.b64decode(metadata["definition_base64"], validate=True)
        except (KeyError, ValueError, TypeError):
            raise fixed.AdapterRefusal("N120 promotion index base64 differs") from None
        fixed.require(archive == self.archive_bytes
                      and fixed.digest(archive) == self.archive_sha256,
                      "embedded archive is not the exact original production bytes")
        fixed.require(definition == self.definition_bytes
                      and fixed.digest(definition) == self.definition_sha256,
                      "embedded definition is not the exact original production bytes")
        self._check_recovery_pin()
        return archive

    def read_object(self, encoded_sha256, expected_bytes):
        self._check_recovery_pin()
        data = super().read_object(encoded_sha256, expected_bytes)
        self._check_recovery_pin()
        return data
