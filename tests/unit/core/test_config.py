"""
Tests for Application Configuration
------------------------------------
The unified data/ layout: token_file's default and the one-time migration
for pre-unification local installs. See app/core/config.py's module
docstring-level comments for why Docker never needed migration (its files
were already under data/ via the old /app/data auto-detection this
replaced).
"""

import os

import pytest

from app.core import config

# conftest.py's autouse mock_gmail_auth fixture blanket-mocks os.path.exists
# to report False for any path containing "token.json" (to block real OAuth
# during tests) - and since pathlib.Path.exists() delegates straight to
# os.path.exists(), that mock catches assertions in this file too, not just
# app code. test_accounts.py's isolate_accounts fixture sidesteps this by
# using a non-"token.json" filename; migration specifically needs to move a
# real token.json, so instead this restores the real os.path.exists for
# tests that need to see one on disk. Captured at import time, before any
# fixture has a chance to monkeypatch it.
_real_os_path_exists = os.path.exists


@pytest.fixture
def real_os_path_exists(monkeypatch):
    monkeypatch.setattr(os.path, "exists", _real_os_path_exists)


class TestDefaultTokenFile:
    def test_default_is_under_data_subfolder(self):
        assert config.DEFAULT_TOKEN_FILE == "data/token.json"


class TestMigrateLegacyDataLayout:
    def test_no_op_when_legacy_and_new_dirs_are_the_same(self, monkeypatch, tmp_path):
        # If token_file was overridden to point at the legacy-style bare
        # location too, there's nothing to migrate.
        legacy = tmp_path / "token.json"
        monkeypatch.setattr(config, "LEGACY_TOKEN_FILE", str(legacy))
        monkeypatch.setattr(config.settings, "token_file", str(legacy))

        config.migrate_legacy_data_layout()  # would raise if it tried to self-replace

    def test_no_op_when_no_legacy_files_exist(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "LEGACY_TOKEN_FILE", str(tmp_path / "legacy" / "token.json"))
        monkeypatch.setattr(config.settings, "token_file", str(tmp_path / "data" / "token.json"))

        config.migrate_legacy_data_layout()

        assert not (tmp_path / "data").exists()

    def test_migrates_a_legacy_file_into_the_new_directory(
        self, monkeypatch, tmp_path, real_os_path_exists
    ):
        legacy_dir = tmp_path / "legacy"
        new_dir = tmp_path / "data"
        legacy_dir.mkdir()
        (legacy_dir / "token.json").write_text('{"token": "abc"}')
        monkeypatch.setattr(config, "LEGACY_TOKEN_FILE", str(legacy_dir / "token.json"))
        monkeypatch.setattr(config.settings, "token_file", str(new_dir / "token.json"))

        config.migrate_legacy_data_layout()

        assert (new_dir / "token.json").read_text() == '{"token": "abc"}'
        assert not (legacy_dir / "token.json").exists()

    def test_migrates_every_known_legacy_file(
        self, monkeypatch, tmp_path, real_os_path_exists
    ):
        legacy_dir = tmp_path / "legacy"
        new_dir = tmp_path / "data"
        legacy_dir.mkdir()
        for name in config._LEGACY_DATA_FILENAMES:
            (legacy_dir / name).write_text("{}")
        monkeypatch.setattr(config, "LEGACY_TOKEN_FILE", str(legacy_dir / "token.json"))
        monkeypatch.setattr(config.settings, "token_file", str(new_dir / "token.json"))

        config.migrate_legacy_data_layout()

        for name in config._LEGACY_DATA_FILENAMES:
            assert (new_dir / name).exists()
            assert not (legacy_dir / name).exists()

    def test_migrates_the_legacy_tokens_directory(self, monkeypatch, tmp_path):
        legacy_dir = tmp_path / "legacy"
        new_dir = tmp_path / "data"
        (legacy_dir / "tokens").mkdir(parents=True)
        (legacy_dir / "tokens" / "a@example.com.json").write_text("{}")
        monkeypatch.setattr(config, "LEGACY_TOKEN_FILE", str(legacy_dir / "token.json"))
        monkeypatch.setattr(config.settings, "token_file", str(new_dir / "token.json"))

        config.migrate_legacy_data_layout()

        assert (new_dir / "tokens" / "a@example.com.json").exists()
        assert not (legacy_dir / "tokens").exists()

    def test_does_not_overwrite_an_existing_file_at_the_new_location(self, monkeypatch, tmp_path):
        # Real-world case: local-Python and Docker runs signed into
        # different accounts before this unification existed - both
        # locations have genuinely different content. Migration must never
        # silently pick a winner.
        legacy_dir = tmp_path / "legacy"
        new_dir = tmp_path / "data"
        legacy_dir.mkdir()
        new_dir.mkdir()
        (legacy_dir / "accounts.json").write_text('{"active": "old@example.com"}')
        (new_dir / "accounts.json").write_text('{"active": "new@example.com"}')
        monkeypatch.setattr(config, "LEGACY_TOKEN_FILE", str(legacy_dir / "token.json"))
        monkeypatch.setattr(config.settings, "token_file", str(new_dir / "token.json"))

        config.migrate_legacy_data_layout()

        assert (new_dir / "accounts.json").read_text() == '{"active": "new@example.com"}'
        assert (legacy_dir / "accounts.json").read_text() == '{"active": "old@example.com"}'

    def test_does_not_overwrite_an_existing_tokens_directory(self, monkeypatch, tmp_path):
        legacy_dir = tmp_path / "legacy"
        new_dir = tmp_path / "data"
        (legacy_dir / "tokens").mkdir(parents=True)
        (legacy_dir / "tokens" / "old@example.com.json").write_text("{}")
        (new_dir / "tokens").mkdir(parents=True)
        (new_dir / "tokens" / "new@example.com.json").write_text("{}")
        monkeypatch.setattr(config, "LEGACY_TOKEN_FILE", str(legacy_dir / "token.json"))
        monkeypatch.setattr(config.settings, "token_file", str(new_dir / "token.json"))

        config.migrate_legacy_data_layout()

        assert (new_dir / "tokens" / "new@example.com.json").exists()
        assert (legacy_dir / "tokens" / "old@example.com.json").exists()

    def test_partial_migration_when_some_new_files_exist_and_others_dont(
        self, monkeypatch, tmp_path, real_os_path_exists
    ):
        legacy_dir = tmp_path / "legacy"
        new_dir = tmp_path / "data"
        legacy_dir.mkdir()
        new_dir.mkdir()
        (legacy_dir / "token.json").write_text('{"token": "legacy"}')
        (legacy_dir / "routines.json").write_text("[]")
        (new_dir / "token.json").write_text('{"token": "already-here"}')
        monkeypatch.setattr(config, "LEGACY_TOKEN_FILE", str(legacy_dir / "token.json"))
        monkeypatch.setattr(config.settings, "token_file", str(new_dir / "token.json"))

        config.migrate_legacy_data_layout()

        # token.json: new side already had one - untouched, legacy kept.
        assert (new_dir / "token.json").read_text() == '{"token": "already-here"}'
        assert (legacy_dir / "token.json").exists()
        # routines.json: no conflict - migrated normally.
        assert (new_dir / "routines.json").exists()
        assert not (legacy_dir / "routines.json").exists()
