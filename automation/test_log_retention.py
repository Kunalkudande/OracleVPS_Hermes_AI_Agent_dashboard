from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import tempfile
import unittest
from cleanup_publisher_logs import cleanup, JOB


class LogRetention(unittest.TestCase):
    def test_only_old_empty_publisher_checks_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / JOB
            folder.mkdir()
            now = datetime(2026, 10, 20, tzinfo=timezone.utc)
            def report(hours, status='silent (empty output)', extra='', modified=None):
                stamp = now - timedelta(hours=hours)
                path = folder / (stamp.strftime('%Y-%m-%d_%H-%M-%S') + '.md')
                path.write_text('# Cron Job: X automatic publisher\n\n**Job ID:** ' + JOB + '\n**Run Time:** ' + stamp.strftime('%Y-%m-%d %H:%M:%S') + '\n**Mode:** no_agent (script)\n**Status:** ' + status + '\n' + extra)
                when = (modified or stamp).timestamp()
                os.utime(path, (when, when))
                return path
            old = report(49)
            retained = [report(47), report(48), report(50, 'script failed'), report(51, 'success'),
                        report(52, extra='An error after the silent header'), report(53, modified=now)]
            ledger = root / 'autopublish-state.json'
            ledger.write_text('important receipt')
            self.assertEqual(cleanup(root, now=now.timestamp())['eligible'], 1)
            self.assertTrue(old.exists(), 'Default mode must not delete')
            result = cleanup(root, apply=True, now=now.timestamp())
            self.assertEqual(result['deleted'], 1)
            self.assertFalse(old.exists())
            self.assertTrue(all(path.exists() for path in retained))
            self.assertEqual(ledger.read_text(), 'important receipt')

    def test_directory_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            try:
                (root / JOB).symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest('Creating test symlinks is unavailable')
            with self.assertRaises(ValueError):
                cleanup(root, apply=True)


if __name__ == '__main__':
    unittest.main()
