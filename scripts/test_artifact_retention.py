"""Deletion boundaries and retention behavior, using disposable fixtures only."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import artifact_retention as retention


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / "artifacts/advanced-m0-20261014/e0-test"
        (self.run / "state").mkdir(parents=True)
        self.db = self.run / "state/test.db"
        self.db.write_bytes(b"disposable database")
        (self.run / "report.json").write_text("{}")
        (self.run / "manifest.json").write_text("{}")
        self.input = self.run / "source-only-input.csv"
        self.input.write_text("id\n1\n")

    def test_success_prunes_db_and_sidecars_preserves_evidence(self):
        wal = self.run / "state/test.db-wal"
        wal.write_bytes(b"wal")
        retention.finish_run(self.run, root=self.root)
        self.assertFalse(self.db.exists())
        self.assertFalse(wal.exists())
        self.assertTrue(self.input.exists())
        self.assertTrue((self.run / "report.json").exists())
        receipt = json.loads((self.run / "retention.json").read_text())
        self.assertEqual(set(receipt["deleted"]), {"state/test.db", "state/test.db-wal"})

    def test_debug_keeps_database(self):
        retention.finish_run(self.run, keep_state=True, root=self.root)
        self.assertTrue(self.db.exists())

    def test_no_report_cannot_finalize(self):
        (self.run / "report.json").unlink()
        with self.assertRaises(FileNotFoundError):
            retention.finish_run(self.run, root=self.root)
        self.assertTrue(self.db.exists())

    def test_outside_scope_cannot_finalize(self):
        outside = self.root / "data"
        outside.mkdir()
        with self.assertRaises(ValueError):
            retention.finish_run(outside, root=self.root)

    def test_active_or_failed_runs_not_selected_by_default(self):
        (self.run / "manifest.json").unlink()
        self.assertEqual(retention.candidates(self.root, include_failed=False, include_caches=False), {})
        (self.run / "failure.json").write_text("{}")
        self.assertEqual(retention.candidates(self.root, include_failed=False, include_caches=False), {})
        self.assertIn(self.db, retention.candidates(self.root, include_failed=True, include_caches=False))

    def test_supervisor_failure_overrides_early_report(self):
        (self.run / "supervisor.json").write_text('{"exit_code": 1}')
        self.assertEqual(retention.candidates(self.root, include_failed=False, include_caches=False), {})

    def test_tracked_database_protected(self):
        with patch.object(retention, "tracked_files", return_value={self.db.relative_to(self.root).as_posix()}):
            with self.assertRaises(ValueError):
                retention.finish_run(self.run, root=self.root)
        self.assertTrue(self.db.exists())

    def test_dry_run_then_age_gate_then_apply(self):
        for arguments, exists in ((["--min-age-days", "0"], True), (["--apply"], True),
                                  (["--apply", "--min-age-days", "0"], False)):
            with patch.object(retention, "ROOT", self.root), patch("sys.argv", ["retention", *arguments]):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(retention.main(), 0)
            self.assertEqual(self.db.exists(), exists)
            self.assertTrue(self.input.exists())

    def test_symlink_boundary(self):
        outside = self.root / "outside.db"
        outside.write_bytes(b"keep")
        self.db.unlink()
        try:
            self.db.symlink_to(outside)
        except OSError:
            self.skipTest("Creating symlinks requires a Windows privilege")
        with self.assertRaises(ValueError):
            retention.finish_run(self.run, root=self.root)
        self.assertEqual(outside.read_bytes(), b"keep")

    @unittest.skipUnless(os.name == "nt", "Windows long path behavior")
    def test_long_path_inventory(self):
        directory = self.run / ("x" * 90) / ("y" * 90)
        retention.fs_path(directory).mkdir(parents=True)
        path = directory / ("z" * 90 + ".json")
        retention.fs_path(path).write_text("{}")
        self.addCleanup(retention.fs_path(directory.parent).rmdir)
        self.addCleanup(retention.fs_path(directory).rmdir)
        self.addCleanup(retention.fs_path(path).unlink)
        files, errors = retention.inventory(self.root)
        self.assertFalse(errors)
        self.assertIn(path, files)


if __name__ == "__main__":
    unittest.main()
