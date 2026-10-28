import datetime as dt
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import cleanup_builder_artifacts as c


def repo(name, fork=False, owner='vhick'):
    return {'name': name, 'fork': fork, 'owner': {'login': owner}, 'archived': False, 'disabled': False}


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.repos = [repo('FileOptimizer-Portable-Builder'), repo('etkg', True),
                      repo('my-blog'), repo('fork-sync-controller'),
                      repo('Bad-Archived', True), repo('not-my-repo', True, 'someone')]
        self.repos[4]['archived'] = True
        self.regex = __import__('re').compile(r'(?i)builder')

    def pick(self, mode, keywords='', selected='', extra=set(), excluded=set()):
        return c.select_repos(self.repos, 'vhick', mode, keywords, selected,
                              extra, excluded, 'fork-sync-controller', self.regex)

    def test_default_builders_and_forks(self):
        self.assertEqual(set(self.pick('builders_and_forks')),
                         {'fileoptimizer-portable-builder', 'etkg'})

    def test_forks_only(self):
        self.assertEqual(set(self.pick('forks')), {'etkg'})

    def test_builder_extra(self):
        self.assertIn('my-blog', self.pick('builders', extra={'my-blog'}))

    def test_keyword(self):
        self.assertEqual(set(self.pick('keyword', keywords='etk, optim')),
                         {'etkg', 'fileoptimizer-portable-builder'})

    def test_explicit(self):
        self.assertEqual(set(self.pick('specific_repos', selected='vhick/etkg')),
                         {'etkg'})

    def test_unknown_repo_rejected(self):
        with self.assertRaises(ValueError):
            self.pick('specific_repos', selected='repo-i-cannot-access')

    def test_other_owner_rejected(self):
        with self.assertRaises(ValueError):
            self.pick('specific_repos', selected='other/etkg')

    def test_exclusion_always_wins(self):
        self.assertEqual(set(self.pick('specific_repos', selected='etkg', excluded={'etkg'})), set())

    def test_requires_keyword(self):
        with self.assertRaises(ValueError):
            self.pick('keyword')

    def test_controller_always_protected(self):
        self.assertNotIn('fork-sync-controller', self.pick('keyword', keywords='fork'))


class BehaviorTests(unittest.TestCase):
    def test_date_cycle_crosses_month(self):
        anchor = dt.date(2026, 10, 28)
        self.assertTrue(c.should_run(dt.datetime(2026, 10, 31, tzinfo=dt.timezone.utc), anchor, 3))
        self.assertFalse(c.should_run(dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc), anchor, 3))
        self.assertTrue(c.should_run(dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc), anchor, 3))

    def test_artifact_keep_latest_and_age(self):
        now = dt.datetime(2026, 10, 28, tzinfo=dt.timezone.utc)
        items = [{'id': 1, 'created_at': '2026-10-27T00:00:00Z'},
                 {'id': 2, 'created_at': '2026-10-20T00:00:00Z'},
                 {'id': 3, 'created_at': '2026-10-19T00:00:00Z'}]
        result = c.eligible_artifacts(items, now-dt.timedelta(hours=72), 1)
        self.assertEqual([x['id'] for x in result], [2, 3])

    def test_cache_last_accessed(self):
        cutoff = dt.datetime(2026, 10, 21, tzinfo=dt.timezone.utc)
        items = [{'id': 1, 'created_at': '2026-09-01T00:00:00Z', 'last_accessed_at': '2026-10-27T00:00:00Z'},
                 {'id': 2, 'created_at': '2026-09-01T00:00:00Z', 'last_accessed_at': '2026-10-01T00:00:00Z'}]
        self.assertEqual([x['id'] for x in c.eligible_caches(items, cutoff)], [2])

    def test_workflow_logs_only_completed_old(self):
        cutoff = dt.datetime(2026, 10, 21, tzinfo=dt.timezone.utc)
        items = [{'id': 1, 'status': 'in_progress', 'updated_at': '2026-10-01T00:00:00Z'},
                 {'id': 2, 'status': 'completed', 'updated_at': '2026-10-01T00:00:00Z'},
                 {'id': 3, 'status': 'completed', 'updated_at': '2026-10-27T00:00:00Z'}]
        self.assertEqual([x['id'] for x in c.eligible_logs(items, cutoff, 100)], [2])

    def test_dry_run_never_deletes(self):
        with patch.object(c, 'api_request') as api:
            deleted, gone = c.delete_candidates('fake', '/repos/vhick/etkg', [{'id': 10}], 'artifacts/{id}', True)
        self.assertEqual((deleted, gone), (0, 0))
        api.assert_not_called()

    def test_deletion_urls_correct(self):
        for kind, fragment in [('artifacts/{id}', '/artifacts/10'),
                               ('runs/{id}/logs', '/runs/10/logs'),
                               ('caches/{id}', '/caches/10')]:
            with patch.object(c, 'api_request') as api:
                deleted, gone = c.delete_candidates('fake', '/repos/vhick/etkg', [{'id': 10}], kind, False)
            self.assertEqual((deleted, gone), (1, 0))
            self.assertIn(fragment, api.call_args.args[-1])

    def test_manual_preview_integration(self):
        source = [repo('etkg', True)]
        calls = []
        def fake_list(_token, path, key=None):
            calls.append(('GET', path))
            if path.startswith('/user/repos'):
                return source
            if path.endswith('/actions/artifacts'):
                return [{'id': 13, 'created_at': '2026-09-01T00:00:00Z', 'size_in_bytes': 30000},
                        {'id': 12, 'created_at': '2026-08-01T00:00:00Z', 'size_in_bytes': 10000}]
            raise AssertionError(path)
        environ = {'CLEANUP_OWNER': 'vhick', 'ARTIFACT_CLEANUP_TOKEN': 'fake',
                   'MANUAL_RUN': 'true', 'MANUAL_DRY_RUN': 'true',
                   'MANUAL_TARGET_MODE': 'specific_repos', 'MANUAL_REPOSITORIES': 'etkg',
                   'MANUAL_DELETE_ARTIFACTS': 'true', 'MANUAL_DELETE_LOGS': 'false',
                   'MANUAL_DELETE_CACHES': 'false', 'MANUAL_ARTIFACT_AGE_HOURS': '0',
                   'KEEP_LATEST_PER_REPO': '1', 'CONTROLLER_REPOSITORY': 'fork-sync-controller'}
        with patch.dict(os.environ, environ, clear=True), patch.object(c, 'list_all', side_effect=fake_list), \
             patch.object(c, 'api_request') as api:
            self.assertEqual(c.main(), 0)
        self.assertEqual(len(calls), 2)
        api.assert_not_called()

    def test_schedule_skip_avoids_token_request(self):
        environ = {'CLEANUP_OWNER': 'vhick', 'MANUAL_RUN': 'false',
                   'CLEANUP_ANCHOR_DATE': '2050-01-01', 'CLEANUP_EVERY_DAYS': '7'}
        # Date-independent anchor chosen such that 7-day check might coincide;
        # explicitly verify scheduling short-circuit if not due.
        now = dt.datetime.now(dt.timezone.utc)
        anchor = now.date() - dt.timedelta(days=1)
        environ['CLEANUP_ANCHOR_DATE'] = anchor.isoformat()
        with patch.dict(os.environ, environ, clear=True), patch.object(c, 'api_request') as api:
            self.assertEqual(c.main(), 0)
        api.assert_not_called()


class ScheduledPolicyTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            'CLEANUP_OWNER': 'vhick',
            'ARTIFACT_CLEANUP_TOKEN': 'test-token',
            'CLEANUP_ANCHOR_DATE': dt.datetime.now(dt.timezone.utc).date().isoformat(),
            'CLEANUP_EVERY_DAYS': '3',
            'BUILDER_NAME_REGEX': '(?i)builder',
            'CONTROLLER_REPOSITORY': 'fork-sync-controller',
            'SCHEDULED_TARGET_MODE': 'builders',
            'SCHEDULED_DELETE_ARTIFACTS': 'true',
            'SCHEDULED_DELETE_LOGS': 'true',
            'SCHEDULED_DELETE_CACHES': 'true',
            'SCHEDULED_ARTIFACT_AGE_HOURS': '72',
            'SCHEDULED_LOG_AGE_DAYS': '7',
            'SCHEDULED_CACHE_IDLE_DAYS': '15',
            'SCHEDULED_KEEP_LATEST_PER_REPO': '0',
            'KEEP_LATEST_PER_REPO': '1',
        }
        today = dt.datetime.now(dt.timezone.utc)
        def iso(days):
            return (today - dt.timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.data = {
            'artifacts': [
                {'id': 1, 'created_at': iso(1), 'size_in_bytes': 1024},
                {'id': 2, 'created_at': iso(4), 'size_in_bytes': 4096},
                {'id': 3, 'created_at': iso(10), 'size_in_bytes': 8192},
            ],
            'workflow_runs': [
                {'id': 10, 'status': 'completed', 'updated_at': iso(2)},
                {'id': 11, 'status': 'completed', 'updated_at': iso(8)},
                {'id': 12, 'status': 'in_progress', 'updated_at': iso(25)},
            ],
            'actions_caches': [
                {'id': 20, 'created_at': iso(45), 'last_accessed_at': iso(3), 'size_in_bytes': 123},
                {'id': 21, 'created_at': iso(45), 'last_accessed_at': iso(17), 'size_in_bytes': 456},
            ]
        }

    def fake_list(self, token, path, key=None):
        if path.startswith('/user/repos'):
            return [repo('New-App-Builder'), repo('etkg', True), repo('fork-sync-controller')]
        if '/repos/vhick/New-App-Builder/actions/' in path:
            return self.data[key]
        raise AssertionError(f'Unexpected repository accessed: {path}')

    def test_preview_of_scheduled_policy_never_deletes_and_excludes_forks(self):
        env = dict(self.base, MANUAL_RUN='true', MANUAL_TARGET_MODE='scheduled_policy_preview',
                   MANUAL_DRY_RUN='false', MANUAL_DELETE_LOGS='false', MANUAL_DELETE_CACHES='false')
        with patch.dict(os.environ, env, clear=True), patch.object(c, 'list_all', side_effect=self.fake_list), \
             patch.object(c, 'api_request') as api, patch.object(c, 'summary') as summ:
            self.assertEqual(c.main(), 0)
        api.assert_not_called()
        report = '\n'.join(summ.call_args.args[0])
        self.assertIn('scheduled policy preview', report)
        self.assertIn('Repositories:** 1', report)
        self.assertIn('Artifacts:** 2 / 0', report)
        self.assertIn('Logs:** 1 / 0', report)
        self.assertIn('Caches:** 1 / 0', report)
        self.assertNotIn('`etkg`', report)

    def test_real_schedule_deletes_only_old_records_in_builder(self):
        env = dict(self.base, MANUAL_RUN='false')
        with patch.dict(os.environ, env, clear=True), patch.object(c, 'list_all', side_effect=self.fake_list), \
             patch.object(c, 'api_request') as api, patch.object(c, 'summary') as summ:
            api.return_value = None
            self.assertEqual(c.main(), 0)
        deleted_paths = [x.args[-1] for x in api.call_args_list]
        self.assertEqual(len(deleted_paths), 4)
        self.assertTrue(any(x.endswith('/artifacts/2') for x in deleted_paths))
        self.assertTrue(any(x.endswith('/artifacts/3') for x in deleted_paths))
        self.assertTrue(any(x.endswith('/runs/11/logs') for x in deleted_paths))
        self.assertTrue(any(x.endswith('/caches/21') for x in deleted_paths))
        self.assertFalse(any('etkg' in x for x in deleted_paths))


if __name__ == '__main__':
    unittest.main()
