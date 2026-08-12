"""
Tests for Pydantic Models (schemas.py)
--------------------------------------
Validates request/response schemas and data validation.
"""

import pytest
from pydantic import ValidationError

from app.models.schemas import (
    FiltersModel,
    DeleteBulkRequest,
    UnsubscribeRequest,
    DeleteEmailsRequest,
    ArchiveScanRequest,
    MarkReadScanRequest,
    MarkReadBulkRequest,
    ArchiveRequest,
    ApplyLabelRequest,
    RemoveLabelRequest,
    CreateRoutineRequest,
)


class TestFiltersModel:
    """Tests for FiltersModel validation."""

    def test_empty_filters_valid(self):
        """Empty filters should be valid."""
        filters = FiltersModel()
        assert filters.older_than is None
        assert filters.larger_than is None
        assert filters.category is None

    def test_valid_older_than_values(self):
        """Valid older_than formats should pass."""
        valid_values = ["7d", "30d", "90d", "180d", "365d", "1d", "999d"]
        for value in valid_values:
            filters = FiltersModel(older_than=value)
            assert filters.older_than == value

    def test_invalid_older_than_values(self):
        """Invalid older_than formats should fail."""
        invalid_values = ["7", "d", "7days", "1w", "1m", "1y", "abc", "7D"]
        for value in invalid_values:
            with pytest.raises(ValidationError):
                FiltersModel(older_than=value)

    def test_older_than_edge_cases(self):
        """Edge cases for older_than validation."""
        # Zero days - currently allowed by regex (matches \d+d)
        filters = FiltersModel(older_than="0d")
        assert filters.older_than == "0d"

        # Negative values - should be invalid (no minus in regex)
        with pytest.raises(ValidationError):
            FiltersModel(older_than="-7d")

        # Very large values - syntactically valid
        filters = FiltersModel(older_than="99999d")
        assert filters.older_than == "99999d"

    def test_valid_larger_than_values(self):
        """Valid larger_than formats should pass."""
        valid_values = ["1M", "5M", "10M", "1K", "100K", "1G", "1m", "5k"]
        for value in valid_values:
            filters = FiltersModel(larger_than=value)
            assert filters.larger_than == value

    def test_invalid_larger_than_values(self):
        """Invalid larger_than formats should fail."""
        invalid_values = ["1", "M", "1MB", "5mb", "10 M", "abc"]
        for value in invalid_values:
            with pytest.raises(ValidationError):
                FiltersModel(larger_than=value)

    def test_larger_than_edge_cases(self):
        """Edge cases for larger_than validation."""
        # Zero size - currently allowed by regex (matches \d+[KMG])
        filters = FiltersModel(larger_than="0M")
        assert filters.larger_than == "0M"

        # Negative values - should be invalid (no minus in regex)
        with pytest.raises(ValidationError):
            FiltersModel(larger_than="-5M")

        # Very large values - syntactically valid
        filters = FiltersModel(larger_than="99999M")
        assert filters.larger_than == "99999M"

    def test_valid_category_values(self):
        """Valid category values should pass and be normalized to lowercase."""
        valid_values = [
            "primary",
            "social",
            "promotions",
            "updates",
            "forums",
            "PRIMARY",
            "Social",
            "PROMOTIONS",
        ]
        for value in valid_values:
            filters = FiltersModel(category=value)
            assert filters.category == value.lower()

    def test_invalid_category_values(self):
        """Invalid category values should fail."""
        invalid_values = ["spam", "inbox", "trash", "important", "starred", "random"]
        for value in invalid_values:
            with pytest.raises(ValidationError):
                FiltersModel(category=value)

    def test_empty_string_treated_as_none(self):
        """Empty strings should be treated as None."""
        filters = FiltersModel(older_than="", larger_than="", category="")
        assert filters.older_than is None
        assert filters.larger_than is None
        assert filters.category is None

    def test_combined_filters(self):
        """Multiple filters should work together."""
        filters = FiltersModel(
            older_than="30d", larger_than="5M", category="promotions"
        )
        assert filters.older_than == "30d"
        assert filters.larger_than == "5M"
        assert filters.category == "promotions"

    def test_unread_only_and_has_attachment_default_none(self):
        """New (#99) filters should default to None, not enabled."""
        filters = FiltersModel()
        assert filters.unread_only is None
        assert filters.has_attachment is None

    def test_unread_only_and_has_attachment_accept_booleans(self):
        """New (#99) filters should accept explicit booleans."""
        filters = FiltersModel(unread_only=True, has_attachment=False)
        assert filters.unread_only is True
        assert filters.has_attachment is False


class TestDeleteBulkRequest:
    """Tests for DeleteBulkRequest model."""

    def test_empty_senders(self):
        """Empty senders list should be valid."""
        request = DeleteBulkRequest()
        assert request.senders == []

    def test_valid_senders_list(self):
        """Valid senders list should pass."""
        senders = ["user1@example.com", "user2@example.com"]
        request = DeleteBulkRequest(senders=senders)
        assert request.senders == senders

    def test_large_senders_list(self):
        """Should accept any number of senders (no limit)."""
        senders = [f"user{i}@example.com" for i in range(500)]
        request = DeleteBulkRequest(senders=senders)
        assert len(request.senders) == 500

    def test_very_large_senders_list(self):
        """Should accept very large sender lists."""
        senders = [f"user{i}@example.com" for i in range(1000)]
        request = DeleteBulkRequest(senders=senders)
        assert len(request.senders) == 1000

    def test_excluded_message_ids_defaults_empty(self):
        """Phase 4c: excluding nothing (the common case) needs no field."""
        request = DeleteBulkRequest(senders=["a@example.com"])
        assert request.excluded_message_ids == []

    def test_excluded_message_ids_accepts_list(self):
        request = DeleteBulkRequest(
            senders=["a@example.com"], excluded_message_ids=["m1", "m2"]
        )
        assert request.excluded_message_ids == ["m1", "m2"]


class TestUnsubscribeRequest:
    """Tests for UnsubscribeRequest model."""

    def test_default_values(self):
        """Default values should be empty strings."""
        request = UnsubscribeRequest()
        assert request.domain == ""
        assert request.link == ""

    def test_with_values(self):
        """Should accept domain and link."""
        request = UnsubscribeRequest(
            domain="example.com", link="https://example.com/unsub"
        )
        assert request.domain == "example.com"
        assert request.link == "https://example.com/unsub"


class TestDeleteEmailsRequest:
    """Tests for DeleteEmailsRequest model."""

    def test_default_values(self):
        """Default sender should be empty string."""
        request = DeleteEmailsRequest()
        assert request.sender == ""

    def test_with_sender(self):
        """Should accept sender email."""
        request = DeleteEmailsRequest(sender="newsletter@example.com")
        assert request.sender == "newsletter@example.com"


class TestArchiveScanRequest:
    """Tests for ArchiveScanRequest model (Phase 3 — Archive's own scan)."""

    def test_default_values(self):
        """Default values should be set correctly."""
        request = ArchiveScanRequest()
        assert request.limit == 500
        assert request.filters is None

    def test_limit_below_minimum(self):
        """Limit below 1 should fail."""
        with pytest.raises(ValidationError):
            ArchiveScanRequest(limit=0)

    def test_limit_above_maximum(self):
        """Limit above 10000 should fail."""
        with pytest.raises(ValidationError):
            ArchiveScanRequest(limit=10001)

    def test_with_filters(self):
        """Request with filters should work."""
        filters = FiltersModel(older_than="180d")
        request = ArchiveScanRequest(limit=500, filters=filters)
        assert request.limit == 500
        assert request.filters.older_than == "180d"


class TestMarkReadScanRequest:
    """Tests for MarkReadScanRequest model (Phase 3 — Mark-as-read's own scan)."""

    def test_default_values(self):
        """Default values should be set correctly."""
        request = MarkReadScanRequest()
        assert request.limit == 500
        assert request.filters is None

    def test_limit_below_minimum(self):
        """Limit below 1 should fail."""
        with pytest.raises(ValidationError):
            MarkReadScanRequest(limit=0)

    def test_with_filters(self):
        """Request with filters should work."""
        filters = FiltersModel(unread_only=True)
        request = MarkReadScanRequest(filters=filters)
        assert request.filters.unread_only is True


class TestMarkReadBulkRequest:
    """Tests for MarkReadBulkRequest model (Phase 3 — senders-scoped mark-read)."""

    def test_empty_senders(self):
        """Empty senders list should be valid."""
        request = MarkReadBulkRequest()
        assert request.senders == []
        assert request.filters is None

    def test_with_senders_and_filters(self):
        """Should accept senders and filters together."""
        senders = ["newsletter@example.com", "digest@example.com"]
        filters = FiltersModel(category="promotions")
        request = MarkReadBulkRequest(senders=senders, filters=filters)
        assert request.senders == senders
        assert request.filters.category == "promotions"

    def test_excluded_message_ids_defaults_empty(self):
        """Phase 4c: excluding nothing (the common case) needs no field."""
        request = MarkReadBulkRequest(senders=["a@example.com"])
        assert request.excluded_message_ids == []


class TestArchiveRequestFilters:
    """Tests for ArchiveRequest's Phase 3 filters field."""

    def test_default_filters_none(self):
        """Filters should default to None (archive whole sender, unscoped)."""
        request = ArchiveRequest(senders=["a@example.com"])
        assert request.filters is None

    def test_with_filters(self):
        """Should accept filters to scope the archive to the active scan."""
        filters = FiltersModel(older_than="180d")
        request = ArchiveRequest(senders=["a@example.com"], filters=filters)
        assert request.filters.older_than == "180d"

    def test_excluded_message_ids_defaults_empty(self):
        """Phase 4c: excluding nothing (the common case) needs no field."""
        request = ArchiveRequest(senders=["a@example.com"])
        assert request.excluded_message_ids == []


class TestApplyLabelRequestExclusion:
    """Phase 4c: ApplyLabelRequest/RemoveLabelRequest also accept
    excluded_message_ids, since Label is a per-row action on an expanded
    (checkbox-bearing) sender row too."""

    def test_apply_label_excluded_message_ids_defaults_empty(self):
        request = ApplyLabelRequest(label_id="Label_1", senders=["a@example.com"])
        assert request.excluded_message_ids == []

    def test_apply_label_excluded_message_ids_accepts_list(self):
        request = ApplyLabelRequest(
            label_id="Label_1",
            senders=["a@example.com"],
            excluded_message_ids=["m1"],
        )
        assert request.excluded_message_ids == ["m1"]

    def test_remove_label_excluded_message_ids_defaults_empty(self):
        request = RemoveLabelRequest(label_id="Label_1", senders=["a@example.com"])
        assert request.excluded_message_ids == []


class TestCreateRoutineRequest:
    """Tests for CreateRoutineRequest validation (Phase 4b)."""

    def _base_kwargs(self, **overrides):
        kwargs = {
            "name": "Market newsletters",
            "senders": ["a@example.com"],
            "older_than": "7d",
            "actions": ["delete"],
        }
        kwargs.update(overrides)
        return kwargs

    def test_valid_request(self):
        request = CreateRoutineRequest(**self._base_kwargs())
        assert request.name == "Market newsletters"
        assert request.senders == ["a@example.com"]
        assert request.actions == ["delete"]
        assert request.label_id is None

    def test_multiple_actions_allowed(self):
        request = CreateRoutineRequest(**self._base_kwargs(actions=["delete", "archive"]))
        assert request.actions == ["delete", "archive"]

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            CreateRoutineRequest(**self._base_kwargs(name=""))

    def test_empty_senders_rejected(self):
        with pytest.raises(ValidationError):
            CreateRoutineRequest(**self._base_kwargs(senders=[]))

    def test_blank_senders_are_stripped_out(self):
        with pytest.raises(ValidationError):
            CreateRoutineRequest(**self._base_kwargs(senders=["  ", ""]))

    def test_senders_are_trimmed(self):
        request = CreateRoutineRequest(**self._base_kwargs(senders=[" a@example.com "]))
        assert request.senders == ["a@example.com"]

    @pytest.mark.parametrize("value", ["1week", "30", "7", "abc"])
    def test_invalid_older_than_rejected(self, value):
        with pytest.raises(ValidationError):
            CreateRoutineRequest(**self._base_kwargs(older_than=value))

    def test_empty_actions_rejected(self):
        with pytest.raises(ValidationError):
            CreateRoutineRequest(**self._base_kwargs(actions=[]))

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError):
            CreateRoutineRequest(**self._base_kwargs(actions=["unsubscribe"]))

    def test_label_action_requires_label_id(self):
        with pytest.raises(ValidationError, match="label_id is required"):
            CreateRoutineRequest(**self._base_kwargs(actions=["label"]))

    def test_label_action_with_blank_label_id_rejected(self):
        with pytest.raises(ValidationError, match="label_id is required"):
            CreateRoutineRequest(**self._base_kwargs(actions=["label"], label_id="  "))

    def test_label_action_with_label_id_accepted(self):
        request = CreateRoutineRequest(
            **self._base_kwargs(actions=["label"], label_id="Label_1")
        )
        assert request.label_id == "Label_1"

    def test_non_label_actions_do_not_require_label_id(self):
        request = CreateRoutineRequest(**self._base_kwargs(actions=["delete", "archive"]))
        assert request.label_id is None
