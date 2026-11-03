import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import store
from log_processor import trim_log


class ReviewRegressions(unittest.TestCase):
    def test_github_annotation_prefix_is_removed_from_evidence(self):
        self.assertEqual(
            trim_log("2026-11-03T10:01:02.123Z ##[error]src/demo.ts: error TS2322"),
            "src/demo.ts: error TS2322",
        )

    def test_private_key_is_removed_as_one_block(self):
        log = (
            "2026-11-03T10:01:02.123Z ERROR loading credentials\n"
            "2026-11-03T10:01:02.123Z -----BEGIN RSA PRIVATE KEY-----\n"
            "2026-11-03T10:01:02.123Z synthetic-private-key-material\n"
            "2026-11-03T10:01:02.123Z -----END RSA PRIVATE KEY-----\n"
            "2026-11-03T10:01:02.123Z exit code 1"
        )
        trimmed = trim_log(log)
        self.assertIn("ERROR loading credentials", trimmed)
        self.assertIn("exit code 1", trimmed)
        self.assertNotIn("synthetic-private-key-material", trimmed)

    def test_accepted_event_survives_reopening_sqlite(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"SQLITE_PATH": str(Path(directory) / "events.sqlite")}
        ):
            store.initialize()
            store.enqueue("1:2:1", '{"workflow_run":{"id":2}}')
            store.initialize()
            self.assertEqual(store.pending(), [("1:2:1", '{"workflow_run":{"id":2}}')])
            store.acknowledge("1:2:1")
            self.assertEqual(store.pending(), [])

    def test_success_watermark_rejects_an_older_attempt(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"SQLITE_PATH": str(Path(directory) / "events.sqlite")}
        ):
            store.initialize()
            with store.connect() as db:
                key = ("owner/repo", 12, 34)
                store.record_success(db, key, 56, 2)
                store.record_success(db, key, 56, 1)
                self.assertTrue(store.has_newer_success(db, key, 56, 1))
                self.assertFalse(store.has_newer_success(db, key, 57, 1))
