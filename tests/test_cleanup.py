import datetime as dt
import os
from unittest.mock import patch
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import cleanup_builder_artifacts as clean


class CleanerTests(unittest.TestCase):
    def test_schedule_across_month(self):
        anchor = dt.date(2026, 10, 28)
        for date, expected in [(dt.date(2026, 10, 28), True),
                               (dt.date(2026, 10, 29), False),
                               (dt.date(2026, 10, 30), False),
                               (dt.date(2026, 10, 31), True),
                               (dt.date(2026, 11, 1), False),
                               (dt.date(2026, 11, 3), True)]:
            self.assertEqual(clean.schedule_due(dt.datetime.combine(date, dt.time(), tzinfo=dt.timezone.utc), anchor, 3), expected)

    def test_age_filter(self):
        art = {'created_at': '2026-10-27T06:00:00Z'}
        self.assertTrue(clean.should_delete(art, dt.datetime(2026, 10, 28, 6, tzinfo=dt.timezone.utc), 24))
        self.assertFalse(clean.should_delete(art, dt.datetime(2026, 10, 26, 6, tzinfo=dt.timezone.utc), 24))

    def test_repo_names(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'repos.txt'
            path.write_text('# comment\nFoo-Builder\nvhick/Bar-Builder\n', encoding='utf-8')
            self.assertEqual(clean.load_repo_names(path, 'vhick'), {'foo-builder', 'bar-builder'})
            path.write_text('someone-else/repo\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                clean.load_repo_names(path, 'vhick')

    def test_main_dry_run_never_deletes(self):
        repo = {'name': 'FileOptimizer-Portable-Builder',
                'owner': {'login': 'vhick'}, 'archived': False, 'disabled': False}
        calls = []
        def fake_list(token, path, item_key=None):
            if path.startswith('/user/repos'):
                return [repo]
            return [{'id': 91, 'created_at': '2026-10-27T00:00:00Z',
                     'size_in_bytes': 1048576}]
        def fake_request(token, method, path):
            calls.append((method, path))
            return None
        with patch.dict(os.environ, {'MANUAL_RUN': 'true', 'DRY_RUN': 'true',
                                     'BUILDER_OWNER': 'vhick',
                                     'ARTIFACT_CLEANUP_TOKEN': 'not-a-real-token'}, clear=False), \
             patch.object(clean, 'list_all', side_effect=fake_list), \
             patch.object(clean, 'api_request', side_effect=fake_request):
            self.assertEqual(clean.main(), 0)
        self.assertEqual(calls, [])

    def test_main_deletes_all_artifacts_when_not_dry_run(self):
        repo = {'name': 'My-Portable-Builder',
                'owner': {'login': 'vhick'}, 'archived': False, 'disabled': False}
        deletes = []
        def fake_list(token, path, item_key=None):
            if path.startswith('/user/repos'):
                return [repo]
            return [{'id': 1, 'created_at': '2026-10-27T00:00:00Z', 'size_in_bytes': 1},
                    {'id': 2, 'created_at': '2026-10-26T00:00:00Z', 'size_in_bytes': 2}]
        def fake_request(token, method, path):
            deletes.append((method, path))
        with patch.dict(os.environ, {'MANUAL_RUN': 'true', 'DRY_RUN': 'false',
                                     'BUILDER_OWNER': 'vhick',
                                     'ARTIFACT_CLEANUP_TOKEN': 'not-a-real-token'}, clear=False), \
             patch.object(clean, 'list_all', side_effect=fake_list), \
             patch.object(clean, 'api_request', side_effect=fake_request):
            self.assertEqual(clean.main(), 0)
        self.assertEqual(len(deletes), 2)
        self.assertTrue(all(method == 'DELETE' for method, _ in deletes))

    def test_pagination_collects_before_delete(self):
        old_request = clean.api_request
        seen = []
        try:
            def fake(token, method, path):
                seen.append(path)
                if path.endswith('page=1'):
                    return {'artifacts': [{'id': i} for i in range(100)]}
                if path.endswith('page=2'):
                    return {'artifacts': [{'id': 101}]}
                raise AssertionError(path)
            clean.api_request = fake
            items = clean.list_all('token', '/repos/a/b/actions/artifacts', item_key='artifacts')
            self.assertEqual(len(items), 101)
            self.assertEqual(len(seen), 2)
        finally:
            clean.api_request = old_request


if __name__ == '__main__':
    unittest.main()
