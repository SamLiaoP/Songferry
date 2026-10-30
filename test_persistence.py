"""Spotify login and matching history survive a restart; nothing is written outside .local."""
import copy
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import app as module
from reverse import FORWARD, REVERSE

TRACK_A = 'a' * 22


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.previous = {k: module.state[k] for k in ('token', 'oauth', 'jobs')}
        module.state.update(token=None, oauth={}, jobs={})
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name) / '.local'
        patcher = patch.object(module, 'LOCAL', self.local); patcher.start(); self.addCleanup(patcher.stop)
        self.client = module.app.test_client()
        self.headers = {'X-CSRF-Token': module.csrf, 'Origin': module.BASE}

    def tearDown(self):
        module.state.update(self.previous)

    def post(self, path, data):
        return self.client.post('/api/' + path, json=data, headers=self.headers, base_url=module.BASE)

    def ready_job(self, jid='job_abcdefghijklmnop'):
        row = {'track': {'title': 'Song', 'artists': ['Artist'], 'duration': 200}, 'selected': 'abc123', 'error': None,
               'candidates': [{'videoId': 'abc123', 'title': 'Song', 'artists': ['Artist'], 'duration': 200, 'score': 100, 'warnings': []},
                              {'videoId': 'def456', 'title': 'Song Live', 'artists': ['Artist'], 'duration': 210, 'score': 80, 'warnings': ['live']}]}
        return {'id': jid, 'direction': FORWARD, 'title': 'Source', 'status': 'ready', 'rows': [copy.deepcopy(row), copy.deepcopy(row)],
                'total': 2, 'creating': False, 'attempted': False, 'result': None, 'error': None,
                'created_at': 1_700_000_000.0, 'source_url': 'https://open.spotify.com/playlist/' + 'x' * 22, 'target_title': ''}

    def mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    # --- Spotify login ---

    def test_login_is_saved_privately_and_restored_after_restart(self):
        module.state['oauth'] = {'state': 'correct', 'time': module.time.time(), 'verifier': 'verifier'}
        fake = Mock(ok=True)
        fake.json.return_value = {'access_token': 'test-token', 'refresh_token': 'test-refresh', 'expires_in': 3600, 'scope': 'playlist-read-private'}
        with patch.object(module, 'config', return_value={'client_id': 'a' * 32}), patch.object(module.requests, 'post', return_value=fake):
            self.client.get('/callback?state=correct&code=code', base_url=module.BASE)
        token_file = self.local / 'spotify_token.json'
        self.assertTrue(token_file.exists())
        self.assertEqual(self.mode(token_file), 0o600)
        self.assertEqual(self.mode(self.local), 0o700)
        module.state['token'] = None  # simulate restart
        module.restore()
        self.assertEqual(module.state['token']['access_token'], 'test-token')
        self.assertTrue(self.client.get('/api/status', base_url=module.BASE).json['spotify'])

    def test_refresh_updates_saved_login_and_failed_refresh_forgets_it(self):
        module.state['token'] = {'access_token': 'old', 'refresh_token': 'r', 'expires_at': 0, 'scope': 's'}
        fresh = Mock(ok=True); fresh.json.return_value = {'access_token': 'new', 'expires_in': 3600}
        api = Mock(ok=True, status_code=200); api.json.return_value = {'name': 'List'}
        with patch.object(module, 'config', return_value={'client_id': 'a' * 32}), patch.object(module.requests, 'post', return_value=fresh), \
                patch.object(module.requests, 'get', return_value=api):
            module.spotify('playlists/x')
        self.assertEqual(json.loads((self.local / 'spotify_token.json').read_text())['access_token'], 'new')
        module.state['token']['expires_at'] = 0
        with patch.object(module, 'config', return_value={'client_id': 'a' * 32}), patch.object(module.requests, 'post', return_value=Mock(ok=False)):
            with self.assertRaises(ValueError):
                module.spotify('playlists/x')
        self.assertIsNone(module.state['token'])
        self.assertFalse((self.local / 'spotify_token.json').exists())

    def test_logout_and_new_client_id_delete_saved_login(self):
        for action in (lambda: self.post('logout', {}), lambda: self.post('config', {'client_id': 'b' * 32})):
            module.state['token'] = {'access_token': 't', 'refresh_token': 'r', 'expires_at': 9e9}
            module.save_token(module.state['token'])
            self.assertEqual(action().status_code, 200)
            self.assertIsNone(module.state['token'])
            self.assertFalse((self.local / 'spotify_token.json').exists())

    def test_corrupt_or_partial_token_file_is_ignored(self):
        self.local.mkdir(mode=0o700)
        (self.local / 'spotify_token.json').write_text('{not json')
        module.restore()
        self.assertIsNone(module.state['token'])
        (self.local / 'spotify_token.json').write_text(json.dumps({'access_token': 'only'}))
        module.restore()
        self.assertIsNone(module.state['token'])

    # --- Matching history ---

    def test_new_job_is_written_during_matching_and_restored_after_restart(self):
        track = {'type': 'track', 'name': 'Song', 'artists': [{'name': 'Artist'}], 'duration_ms': 200000}
        pages = [{'name': 'List'}, {'items': [{'item': track}] * 12, 'next': None}]
        fake = Mock(); fake.search.return_value = [{'videoId': 'a', 'title': 'Song', 'artists': [{'name': 'Artist'}], 'duration_seconds': 200}]
        with patch.object(module, 'spotify', side_effect=pages), patch.object(module, 'yt', return_value=fake), \
                patch.object(module.time, 'sleep'), patch.object(module.threading, 'Thread') as thread:
            response = self.post('jobs', {'url': 'https://open.spotify.com/playlist/' + 'x' * 22, 'direction': FORWARD})
            jid = response.json['id']
            path = self.local / 'jobs' / (jid + '.json')
            self.assertTrue(path.exists(), 'job is saved as soon as it starts')
            self.assertEqual(self.mode(path), 0o600)
            job, pid = thread.call_args.kwargs['args']
            module.run_match(job, pid)
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(saved['status'], 'ready')
        self.assertEqual(len(saved['rows']), 12)
        self.assertEqual(saved['rows'][0]['selected'], 'a')
        self.assertEqual(saved['source_url'], 'https://open.spotify.com/playlist/' + 'x' * 22)
        module.state['jobs'] = {}
        module.restore()
        self.assertIn(jid, module.state['jobs'])
        listing = self.client.get('/api/jobs', base_url=module.BASE).json['jobs']
        self.assertEqual([(j['id'], j['title'], j['rows'], j['selected'], j['status']) for j in listing], [(jid, 'List', 12, 12, 'ready')])
        self.assertEqual(self.client.get('/api/jobs/' + jid, base_url=module.BASE).json['title'], 'List')

    def test_interrupted_matching_is_marked_after_restart_and_never_resumed(self):
        job = self.ready_job(); job['status'] = 'matching'; job['rows'] = job['rows'][:1]
        module.save_job(job)
        module.state['jobs'] = {}
        module.restore()
        restored = module.state['jobs'][job['id']]
        self.assertEqual(restored['status'], 'error')
        self.assertIn('中斷', restored['error'])
        self.assertEqual(len(restored['rows']), 1)
        self.assertEqual(json.loads((self.local / 'jobs' / (job['id'] + '.json')).read_text(encoding='utf-8'))['status'], 'error')
        with patch.object(module.threading, 'Thread') as thread:
            self.assertEqual(self.post('jobs', {'url': 'https://open.spotify.com/playlist/' + 'x' * 22, 'direction': FORWARD}).status_code, 200)
        thread.assert_called_once()

    def test_history_lists_newest_first_and_delete_removes_file(self):
        older = self.ready_job('older_aaaaaaaaaaaaaaaa'); older['created_at'] = 1
        newer = self.ready_job('newer_bbbbbbbbbbbbbbbb'); newer['created_at'] = 2; newer['direction'] = REVERSE
        for job in (older, newer):
            module.state['jobs'][job['id']] = job; module.save_job(job)
        listing = self.client.get('/api/jobs', base_url=module.BASE).json['jobs']
        self.assertEqual([j['id'] for j in listing], [newer['id'], older['id']])
        self.assertEqual(listing[0]['direction'], REVERSE)
        self.assertNotIn('candidates', json.dumps(listing))
        self.assertEqual(self.client.delete('/api/jobs/' + older['id'], base_url=module.BASE).status_code, 403, 'delete needs the CSRF token')
        response = self.client.delete('/api/jobs/' + older['id'], headers=self.headers, base_url=module.BASE)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(older['id'], module.state['jobs'])
        self.assertFalse((self.local / 'jobs' / (older['id'] + '.json')).exists())
        self.assertTrue((self.local / 'jobs' / (newer['id'] + '.json')).exists())
        self.assertEqual(self.client.delete('/api/jobs/' + older['id'], headers=self.headers, base_url=module.BASE).status_code, 400)

    def test_running_job_cannot_be_deleted(self):
        job = self.ready_job(); job['status'] = 'matching'; module.state['jobs'][job['id']] = job
        self.assertEqual(self.client.delete('/api/jobs/' + job['id'], headers=self.headers, base_url=module.BASE).status_code, 400)
        self.assertIn(job['id'], module.state['jobs'])

    def test_selections_and_title_are_saved_and_validated(self):
        job = self.ready_job(); module.state['jobs'][job['id']] = job
        response = self.post('jobs/' + job['id'] + '/selections', {'selections': ['def456', None], 'title': '  我的歌單  '})
        self.assertEqual(response.status_code, 200)
        saved = json.loads((self.local / 'jobs' / (job['id'] + '.json')).read_text(encoding='utf-8'))
        self.assertEqual([r['selected'] for r in saved['rows']], ['def456', None])
        self.assertEqual(saved['target_title'], '我的歌單')
        self.assertEqual(self.post('jobs/' + job['id'] + '/selections', {'selections': ['unknown', None]}).status_code, 400)
        self.assertEqual(self.post('jobs/' + job['id'] + '/selections', {'selections': [{'x': 1}, None]}).status_code, 400, 'non-string ids are rejected, not a 500')
        self.assertEqual(self.post('create', {'job': job['id'], 'title': 'New', 'selections': [['abc123'], None]}).status_code, 400)
        self.assertEqual(self.post('jobs/' + job['id'] + '/selections', {'selections': ['abc123'] * 3}).status_code, 400)
        self.assertEqual([r['selected'] for r in job['rows']], ['def456', None], 'rejected updates leave choices untouched')
        self.assertEqual(self.post('jobs/' + job['id'] + '/selections', {'selections': ['abc123']}).status_code, 200, 'partial prefix while matching')
        self.assertEqual([r['selected'] for r in job['rows']], ['abc123', None])

    def test_create_result_is_persisted_for_history(self):
        job = self.ready_job(); module.state['jobs'][job['id']] = job
        fake = Mock(); fake.create_playlist.return_value = 'PL_123'
        fake.get_playlist.return_value = {'tracks': [{'videoId': 'abc123'}, {'videoId': 'def456'}]}
        with patch.object(module, 'yt', return_value=fake):
            response = self.post('create', {'job': job['id'], 'title': 'New', 'selections': ['abc123', 'def456']})
        self.assertEqual(response.status_code, 200)
        saved = json.loads((self.local / 'jobs' / (job['id'] + '.json')).read_text(encoding='utf-8'))
        self.assertTrue(saved['attempted']); self.assertFalse(saved['creating'])
        self.assertEqual(saved['result']['url'], 'https://music.youtube.com/playlist?list=PL_123')
        self.assertEqual([r['selected'] for r in saved['rows']], ['abc123', 'def456'])
        self.assertEqual(saved['target_title'], 'New')
        entry = self.client.get('/api/jobs', base_url=module.BASE).json['jobs'][0]
        self.assertEqual(entry['url'], saved['result']['url'])
        self.assertTrue(entry['attempted'])

    def test_restart_during_create_keeps_job_locked_against_duplicates(self):
        job = self.ready_job(); job['creating'] = True; job['attempted'] = True
        module.save_job(job)
        module.state['jobs'] = {}
        module.restore()
        restored = module.state['jobs'][job['id']]
        self.assertFalse(restored['creating']); self.assertTrue(restored['attempted'])
        with patch.object(module, 'yt') as youtube:
            self.assertEqual(self.post('create', {'job': job['id'], 'title': 'New', 'selections': ['abc123', 'abc123']}).status_code, 400)
        youtube.assert_not_called()

    def test_restore_skips_foreign_or_broken_files(self):
        folder = self.local / 'jobs'; folder.mkdir(parents=True)
        (folder / 'bad name.json').write_text(json.dumps(self.ready_job()))
        (folder / 'broken_cccccccccccccccc.json').write_text('{')
        (folder / 'norows_dddddddddddddddd.json').write_text(json.dumps({'status': 'ready'}))
        module.restore()
        self.assertEqual(module.state['jobs'], {})

    def test_jobs_without_id_are_never_written(self):
        module.save_job({'status': 'ready', 'rows': []})
        self.assertFalse((self.local / 'jobs').exists())


if __name__ == '__main__':
    unittest.main()
