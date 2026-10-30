"""Paste-anything link import: extraction from messy chat text, matching, and adding to existing playlists."""
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import app as module
from links import (add_to_spotify_playlist, add_to_youtube_playlist, clean_author, clean_title, extract_links,
                   run_links_match, spotify_playlists, youtube_playlists)

TRACK_A = 'a' * 22
TRACK_B = 'b' * 22
PLAYLIST = 'p' * 22

CHAT = """那個赤魔 — 2026/9/17 下午5:22
直接連結貼出來r
https://www.youtube.com/watch?v=nKOekzWZH7Y&list=RDnKOekzWZH7Y&start_radio=1
YouTube
MyGO!!!!!
peterpeter991 [BDON],  — 2026/9/17 下午5:24
https://music.youtube.com/watch?v=P2BT98BC1N8&si=mOdAJ5QAhdUgGR9R
https://youtu.be/FvOpPeKSf_4?si=YtnbVG7wML1oi_ok
Problems：https://music.youtube.com/watch?v=bhYoWyHCrLI&feature=share
https://youtube.com/shorts/tFehEsnFvyE?si=4KIQRvoHKP-sIGfU
https://www.bilibili.com/video/BV17WYy63EUd/?trackid=web
https://open.spotify.com/track/""" + TRACK_A + """?si=abc
https://open.spotify.com/intl-zh-tw/track/""" + TRACK_B + """
https://open.spotify.com/playlist/""" + PLAYLIST + """
https://youtu.be/FvOpPeKSf_4?si=duplicate
spotify:track:""" + TRACK_A + """
"""


class ExtractionTests(unittest.TestCase):
    def test_messy_chat_export_yields_ordered_unique_song_links(self):
        items, unsupported = extract_links(CHAT)
        self.assertEqual([(i['platform'], i['id']) for i in items], [
            ('youtube', 'nKOekzWZH7Y'), ('youtube', 'P2BT98BC1N8'), ('youtube', 'FvOpPeKSf_4'), ('youtube', 'bhYoWyHCrLI'),
            ('youtube', 'tFehEsnFvyE'), ('spotify', TRACK_A), ('spotify', TRACK_B)])
        self.assertEqual(items[3]['url'], 'https://music.youtube.com/watch?v=bhYoWyHCrLI&feature=share', 'a label glued to the link does not break it')
        self.assertEqual([u['url'].split('/')[2] for u in unsupported], ['www.bilibili.com', 'open.spotify.com'])
        self.assertIn('歌單', unsupported[1]['reason'])

    def test_empty_or_linkless_text(self):
        self.assertEqual(extract_links(''), ([], []))
        self.assertEqual(extract_links('只有文字 沒有連結 youtube.com'), ([], []))

    def test_title_cleanup_keeps_names_and_drops_marketing(self):
        self.assertEqual(clean_title('Michael Bublé - Feeling Good [Official 4K Remastered Music Video]'), 'Michael Bublé - Feeling Good')
        self.assertEqual(clean_title('ヨルシカ - 千鳥（OFFICIAL VIDEO）'), 'ヨルシカ - 千鳥')
        self.assertEqual(clean_title('Khalil Fong (方大同) Twenty Three (才二十三) Official Music Video'), 'Khalil Fong (方大同) Twenty Three (才二十三)')
        self.assertEqual(clean_title('【Official Live Video】MyGO!!!!!「無路矢」'), 'MyGO!!!!!「無路矢」')
        self.assertEqual(clean_author('Roselia - Topic'), 'Roselia')
        self.assertEqual(clean_author('RuthBVEVO'), 'RuthB')


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.lock = threading.RLock()
        self.saves = []

    def youtube_client(self):
        client = Mock()
        def get_song(video_id):
            songs = {'v1': {'videoDetails': {'title': 'Noroshi (Official Video)', 'author': 'MyGO!!!!! - Topic', 'lengthSeconds': '274'}, 'playabilityStatus': {'status': 'OK'}},
                     'bad': {'videoDetails': {'title': 'Short', 'author': 'x'}, 'playabilityStatus': {'status': 'UNPLAYABLE'}}}
            return songs.get(video_id, {})
        client.get_song.side_effect = get_song
        client.search.return_value = [{'videoId': 'yt_match', 'title': 'Song', 'artists': [{'name': 'Artist'}], 'duration_seconds': 200}]
        return client

    def spotify_api(self, path, method='GET', payload=None):
        if path.startswith('tracks?ids='):
            ids = path.split('=')[1].split(',')
            return {'tracks': [{'id': tid, 'name': 'Song', 'artists': [{'name': 'Artist'}], 'duration_ms': 200000} if tid == TRACK_A else None for tid in ids]}
        if path.startswith('search?'):
            return {'tracks': {'items': [{'id': TRACK_B, 'name': 'Noroshi', 'artists': [{'name': 'MyGO!!!!!'}], 'duration_ms': 274000}]}}
        raise AssertionError(path)

    def test_links_to_youtube_playlist_add_youtube_directly_and_match_spotify(self):
        items = [{'platform': 'youtube', 'id': 'v1', 'url': 'u1'}, {'platform': 'spotify', 'id': TRACK_A, 'url': 'u2'},
                 {'platform': 'youtube', 'id': 'bad', 'url': 'u3'}, {'platform': 'spotify', 'id': TRACK_B, 'url': 'u4'}]
        job = {'target': 'youtube', 'rows': []}
        client = self.youtube_client()
        run_links_match(job, items, lambda: client, self.spotify_api, self.lock, self.saves.append)
        self.assertEqual(job['status'], 'ready')
        rows = job['rows']
        self.assertEqual(rows[0]['track']['title'], 'Noroshi'); self.assertEqual(rows[0]['track']['artists'], ['MyGO!!!!!'])
        self.assertEqual(rows[0]['candidates'][0]['videoId'], 'v1'); self.assertEqual(rows[0]['candidates'][0]['score'], 100)
        self.assertEqual(rows[0]['selected'], 'v1', 'a YouTube link going to YouTube Music needs no matching')
        self.assertEqual(rows[1]['selected'], 'yt_match', 'a Spotify link is matched by search')
        self.assertTrue(rows[2]['track']['skip']); self.assertIsNone(rows[2]['selected'])
        self.assertTrue(rows[3]['track']['skip'], 'unknown Spotify ids are skipped, not dropped')
        client.search.assert_called_once()

    def test_links_to_spotify_playlist_add_spotify_directly_and_match_youtube(self):
        items = [{'platform': 'spotify', 'id': TRACK_A, 'url': 'u1'}, {'platform': 'youtube', 'id': 'v1', 'url': 'u2'}]
        job = {'target': 'spotify', 'rows': []}
        run_links_match(job, items, self.youtube_client, self.spotify_api, self.lock, self.saves.append)
        self.assertEqual(job['rows'][0]['selected'], TRACK_A)
        self.assertEqual(job['rows'][0]['candidates'][0]['url'], 'https://open.spotify.com/track/' + TRACK_A)
        self.assertEqual(job['rows'][1]['selected'], TRACK_B)
        self.assertEqual(job['rows'][1]['track']['url'], 'u2')


class ExistingPlaylistTests(unittest.TestCase):
    def test_youtube_append_skips_songs_already_there_and_verifies_tail(self):
        client = Mock()
        contents = [{'videoId': 'old'}]
        client.get_playlist.side_effect = lambda pid, limit=None: {'tracks': list(contents)}
        def add(pid, ids, duplicates=False):
            contents.extend({'videoId': v} for v in ids); return {'status': 'STATUS_SUCCEEDED'}
        client.add_playlist_items.side_effect = add
        result = add_to_youtube_playlist(client, 'PL1', ['old', 'n1', 'n2', 'n1'])
        client.add_playlist_items.assert_called_once_with('PL1', ['n1', 'n2'], duplicates=True)
        self.assertEqual((result['added'], result['skipped_existing'], result['actual']), (2, 2, 3))
        self.assertTrue(result['verified'])

    def test_youtube_append_without_success_status_is_reported(self):
        client = Mock(); client.get_playlist.return_value = {'tracks': []}
        client.add_playlist_items.return_value = {'status': 'STATUS_FAILED'}
        with self.assertRaises(ValueError):
            add_to_youtube_playlist(client, 'PL1', ['n1'])

    def test_spotify_append_batches_dedupes_and_verifies(self):
        contents = [TRACK_B]
        def api(path, method='GET', payload=None):
            if method == 'POST':
                contents.extend(u.split(':')[-1] for u in payload['uris']); return {'snapshot_id': 'ok'}
            return {'items': [{'item': {'id': t}} for t in contents], 'next': None}
        job = {}
        result = add_to_spotify_playlist(api, job, PLAYLIST, [TRACK_B, TRACK_A])
        self.assertEqual((result['added'], result['skipped_existing'], result['actual']), (1, 1, 2))
        self.assertTrue(result['verified']); self.assertTrue(result['complete'])
        self.assertEqual(job['destination_url'], 'https://open.spotify.com/playlist/' + PLAYLIST)

    def test_playlist_listing_filters_to_writable_ones(self):
        client = Mock(); client.get_library_playlists.return_value = [{'playlistId': 'LM', 'title': 'Liked'}, {'playlistId': 'PLx', 'title': 'Mine', 'count': '3'}]
        self.assertEqual(youtube_playlists(client), [{'id': 'PLx', 'name': 'Mine', 'count': '3'}])
        def api(path, method='GET', payload=None):
            if path == 'me': return {'id': 'me'}
            return {'items': [{'id': PLAYLIST, 'name': 'Private', 'public': False, 'owner': {'id': 'me'}, 'tracks': {'total': 4}},
                              {'id': 'c' * 22, 'name': 'Public', 'public': True, 'owner': {'id': 'me'}, 'items': {'total': 9}},
                              {'id': 'd' * 22, 'name': 'Someone elses', 'public': False, 'owner': {'id': 'other'}}], 'next': None}
        self.assertEqual(spotify_playlists(api), [{'id': PLAYLIST, 'name': 'Private', 'count': 4}, {'id': 'c' * 22, 'name': 'Public', 'count': 9}])


class LinksApiTests(unittest.TestCase):
    def setUp(self):
        self.previous = {k: module.state[k] for k in ('token', 'oauth', 'jobs')}
        module.state.update(token={'access_token': 't', 'refresh_token': 'r', 'expires_at': 9e9, 'scope': 'playlist-read-private playlist-modify-private'}, oauth={}, jobs={})
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name) / '.local'
        patcher = patch.object(module, 'LOCAL', self.local); patcher.start(); self.addCleanup(patcher.stop)
        self.client = module.app.test_client()
        self.headers = {'X-CSRF-Token': module.csrf, 'Origin': module.BASE}

    def tearDown(self):
        module.state.update(self.previous)

    def post(self, path, data):
        return self.client.post('/api/' + path, json=data, headers=self.headers, base_url=module.BASE)

    def test_pages_are_served_and_parse_endpoint_counts_links(self):
        for path in ('/', '/accounts', '/transfer', '/links'):
            response = self.client.get(path, base_url=module.BASE)
            self.assertEqual(response.status_code, 200, path); response.close()
        response = self.post('links/parse', {'text': CHAT})
        self.assertEqual(response.json['counts'], {'youtube': 5, 'spotify': 2})
        self.assertEqual(len(response.json['unsupported']), 2)

    def test_links_job_is_created_persisted_and_listed_with_its_kind(self):
        with patch.object(module.threading, 'Thread') as thread:
            response = self.post('links/jobs', {'text': CHAT, 'target': 'youtube', 'playlist': {'mode': 'existing', 'id': 'PLabc', 'name': 'Mine'}})
        self.assertEqual(response.status_code, 200, response.json)
        thread.assert_called_once()
        job = module.state['jobs'][response.json['id']]
        self.assertEqual((job['kind'], job['target'], job['direction'], job['total']), ('links', 'youtube', module.FORWARD, 7))
        self.assertEqual(job['playlist'], {'mode': 'existing', 'id': 'PLabc', 'name': 'Mine'})
        self.assertTrue((self.local / 'jobs' / (job['id'] + '.json')).exists())
        entry = self.client.get('/api/jobs', base_url=module.BASE).json['jobs'][0]
        self.assertEqual((entry['kind'], entry['target'], entry['playlist']), ('links', 'youtube', 'Mine'))

    def test_links_job_validation(self):
        self.assertEqual(self.post('links/jobs', {'text': 'nothing here', 'target': 'youtube'}).status_code, 400)
        self.assertEqual(self.post('links/jobs', {'text': CHAT, 'target': 'apple'}).status_code, 400)
        self.assertEqual(self.post('links/jobs', {'text': CHAT, 'target': 'spotify', 'playlist': {'mode': 'existing', 'id': '../x'}}).status_code, 400)
        module.state['token'] = None
        self.assertEqual(self.post('links/jobs', {'text': 'https://youtu.be/nKOekzWZH7Y', 'target': 'youtube'}).status_code, 200, 'YouTube-only links to YouTube Music need no Spotify')
        module.state['jobs'] = {}
        self.assertEqual(self.post('links/jobs', {'text': CHAT, 'target': 'youtube'}).status_code, 400, 'Spotify links need a Spotify login to be read')

    def test_create_adds_to_the_existing_playlist_instead_of_creating_one(self):
        row = {'track': {'title': 'Song', 'artists': ['Artist'], 'duration': 200}, 'selected': 'v1', 'error': None,
               'candidates': [{'videoId': 'v1', 'title': 'Song', 'artists': ['Artist'], 'duration': 200, 'score': 100, 'warnings': []}]}
        job = {'id': 'links_aaaaaaaaaaaaaaaaa', 'kind': 'links', 'target': 'youtube', 'direction': module.FORWARD, 'status': 'ready', 'rows': [row],
               'total': 1, 'creating': False, 'attempted': False, 'result': None, 'error': None, 'playlist': {'mode': 'existing', 'id': 'PLabc', 'name': 'Mine'}}
        module.state['jobs'][job['id']] = job
        fake = Mock(); fake.get_playlist.return_value = {'tracks': [{'videoId': 'v1'}]}
        with patch.object(module, 'yt', return_value=fake):
            response = self.post('create', {'job': job['id'], 'title': '', 'selections': ['v1']})
        self.assertEqual(response.status_code, 200, response.json)
        fake.create_playlist.assert_not_called()
        fake.add_playlist_items.assert_not_called()  # already in the playlist
        self.assertEqual(response.json['skipped_existing'], 1)
        self.assertEqual(response.json['url'], 'https://music.youtube.com/playlist?list=PLabc')
        self.assertTrue(job['attempted'])

    def test_playlists_endpoint_validates_target_and_scope(self):
        self.assertEqual(self.client.get('/api/playlists/apple', base_url=module.BASE).status_code, 400)
        module.state['token']['scope'] = 'playlist-read-private playlist-modify-private'
        self.assertEqual(self.client.get('/api/playlists/spotify', base_url=module.BASE).status_code, 400, 'modify-public is needed too')


if __name__ == '__main__':
    unittest.main()
