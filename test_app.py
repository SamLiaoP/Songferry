import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import app as module
from matching import rank


class MatchingTests(unittest.TestCase):
    def test_cover_and_live_are_not_silently_selected(self):
        track = {'title': '晴天', 'artists': ['周杰倫'], 'duration': 269}
        results = [{'videoId':'original','title':'晴天','artists':[{'name':'周杰倫'}], 'duration_seconds':269},
                   {'videoId':'live','title':'晴天 Live','artists':[{'name':'周杰倫'}], 'duration_seconds':269},
                   {'videoId':'cover','title':'晴天','artists':[{'name':'別的歌手'}], 'duration_seconds':269}]
        candidates = rank(track, results)
        self.assertEqual(candidates[0]['videoId'], 'original')
        self.assertEqual(candidates[0]['score'], 100)
        self.assertTrue(all(c['warnings'] for c in candidates[1:]))

    def test_missing_duration_and_unavailable(self):
        candidates = rank({'title':'A','artists':['B'],'duration':200}, [
            {'videoId':'missing','title':'A','artists':[{'name':'B'}]},
            {'videoId':'unavailable','title':'A','isAvailable':False}])
        self.assertEqual(len(candidates), 1)
        self.assertIn('缺少長度資料', candidates[0]['warnings'])


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.local = patch.object(module, 'LOCAL', Path(self.temp.name) / '.local'); self.local.start(); self.addCleanup(self.local.stop)
        module.state['jobs'] = {}
        self.client = module.app.test_client()
        self.base = module.BASE
        self.headers = {'X-CSRF-Token':module.csrf,'Origin':self.base}
        self.row = {'track':{'title':'Song','artists':['Artist'],'duration':200},
                    'candidates':[{'videoId':'abc123','title':'Song','artists':['Artist'],'duration':200,'warnings':[],'score':100}],
                    'selected':'abc123','error':None}
        self.job = {'status':'ready','rows':[copy.deepcopy(self.row), copy.deepcopy(self.row)],'result':None,
                    'creating':False,'attempted':False,'error':None,'total':2,'title':'List'}
        module.state['jobs']['test'] = self.job

    def post(self, path, data):
        return self.client.post('/api/' + path, json=data, headers=self.headers, base_url=self.base)

    def test_local_request_protection(self):
        self.assertEqual(self.client.get('/api/status', base_url='http://evil.test').status_code,403)
        self.assertEqual(self.client.post('/api/config', json={},base_url=self.base).status_code,403)
        self.assertEqual(self.client.post('/api/config', json={},headers={'X-CSRF-Token':module.csrf,'Origin':'https://evil.test'},base_url=self.base).status_code,403)

    def test_new_item_field_and_pagination_keep_order_and_skips(self):
        track={'type':'track','name':'Song','artists':[{'name':'Artist'}],'duration_ms':200000}
        pages=[{'name':'List'}, {'items':[{'item':track}], 'next':'https://api.spotify.com/v1/playlists/id/items?offset=1'},
               {'items':[{'track':track},{'item':{'type':'episode','name':'Podcast'}}],'next':None}]
        job={'rows':[]}
        fake=Mock();fake.search.return_value=[{'videoId':'a','title':'Song','artists':[{'name':'Artist'}],'duration_seconds':200}]
        with patch.object(module,'spotify',side_effect=pages),patch.object(module,'yt',return_value=fake),patch.object(module.time,'sleep'):
            module.run_match(job,'id')
        self.assertEqual(job['status'],'ready')
        self.assertEqual(len(job['rows']),3)
        self.assertTrue(job['rows'][2]['track']['skip'])
        self.assertIsNone(job['rows'][2]['selected'])
        self.assertEqual(fake.search.call_count,1)

    def test_create_is_idempotent_and_preserves_duplicates(self):
        fake=Mock();fake.create_playlist.return_value='PL_123'
        fake.get_playlist.return_value={'tracks':[{'videoId':'abc123'},{'videoId':'abc123'}]}
        payload={'job':'test','title':'New','selections':['abc123','abc123']}
        with patch.object(module,'yt',return_value=fake):
            first=self.post('create',payload);second=self.post('create',payload)
        self.assertEqual(first.status_code,200)
        self.assertTrue(first.json['verified'])
        self.assertEqual(first.json,second.json)
        self.assertEqual(fake.create_playlist.call_count,1)
        self.assertEqual(fake.create_playlist.call_args.kwargs['video_ids'],['abc123','abc123'])

    def test_uncertain_write_is_never_automatically_retried(self):
        fake=Mock();fake.create_playlist.side_effect=TimeoutError()
        payload={'job':'test','title':'New','selections':['abc123',None]}
        with patch.object(module,'yt',return_value=fake):
            self.assertEqual(self.post('create',payload).status_code,400)
            self.assertEqual(self.post('create',payload).status_code,400)
        self.assertEqual(fake.create_playlist.call_count,1)

    def test_tampered_selection_does_not_write(self):
        with patch.object(module,'yt') as fake:
            response=self.post('create',{'job':'test','title':'New','selections':['unknown',None]})
        self.assertEqual(response.status_code,400)
        fake.assert_not_called()

    def test_readback_mismatch_is_not_reported_as_verified(self):
        fake=Mock();fake.create_playlist.return_value='PL_123';fake.get_playlist.return_value={'tracks':[]}
        with patch.object(module,'yt',return_value=fake):
            result=self.post('create',{'job':'test','title':'New','selections':['abc123',None]})
        self.assertFalse(result.json['verified'])
        self.assertEqual(result.json['actual'],0)

    def test_search_index_must_be_a_real_integer(self):
        for index in ['0', '9' * 5000, True, None, 2, -1]:
            response = self.post('search', {'job': 'test', 'index': index, 'query': 'Song'})
            self.assertEqual(response.status_code, 400, repr(index))
            self.assertEqual(response.json['error'], '無效的歌曲編號。')

    def test_invalid_playlist_hosts(self):
        for url in ['https://evil.test/playlist/'+'a'*22,'https://open.spotify.com.evil.test/playlist/'+'a'*22]:
            with self.assertRaises(ValueError):module.playlist_id(url)


class SpotifyLoginTests(unittest.TestCase):
    def setUp(self):
        self.client = module.app.test_client()
        self.headers = {'X-CSRF-Token': module.csrf, 'Origin': module.BASE}
        self.previous_oauth = module.state['oauth']
        self.previous_token = module.state['token']
        module.state['oauth'] = {}
        module.state['token'] = None
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.local = patch.object(module, 'LOCAL', Path(self.temp.name) / '.local'); self.local.start(); self.addCleanup(self.local.stop)

    def tearDown(self):
        module.state['oauth'] = self.previous_oauth
        module.state['token'] = self.previous_token

    def post_login(self):
        return self.client.post('/api/login', json={}, headers=self.headers, base_url=module.BASE)

    def test_missing_configuration_returns_actionable_error(self):
        with patch.object(module, 'config', return_value={}):
            response = self.post_login()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json['code'], 'spotify_setup_required')
        self.assertIn('Client ID', response.json['error'])
        self.assertFalse(module.state['oauth'])

    def test_login_builds_valid_pkce_and_exact_redirect(self):
        from urllib.parse import urlparse, parse_qs
        import base64, hashlib
        with patch.object(module, 'config', return_value={'client_id': 'a' * 32}):
            response = self.post_login()
        self.assertEqual(response.status_code, 200)
        url = urlparse(response.json['url'])
        self.assertEqual(url.netloc, 'accounts.spotify.com')
        query = parse_qs(url.query)
        pending = module.state['oauth']
        expected = base64.urlsafe_b64encode(hashlib.sha256(pending['verifier'].encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(query['code_challenge'], [expected])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertEqual(query['redirect_uri'], [module.BASE + '/callback'])
        self.assertEqual(query['state'], [pending['state']])
        self.assertNotIn('client_secret', query)

    def test_wrong_callback_state_never_exchanges_code(self):
        module.state['oauth'] = {'state':'correct', 'time':module.time.time(), 'verifier':'secret'}
        with patch.object(module.requests, 'post') as exchange:
            response = self.client.get('/callback?state=wrong&code=code', base_url=module.BASE)
        self.assertEqual(response.status_code, 400)
        exchange.assert_not_called()

    def test_successful_callback_completes_login(self):
        module.state['oauth'] = {'state':'correct', 'time':module.time.time(), 'verifier':'verifier'}
        fake = Mock(ok=True)
        fake.json.return_value = {'access_token':'test-token', 'refresh_token':'test-refresh', 'expires_in':3600}
        with patch.object(module, 'config', return_value={'client_id':'a'*32}), patch.object(module.requests, 'post', return_value=fake) as exchange:
            response = self.client.get('/callback?state=correct&code=code', base_url=module.BASE)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/')
        self.assertEqual(module.state['token']['access_token'], 'test-token')
        self.assertFalse(module.state['oauth'])
        self.assertEqual(exchange.call_args.kwargs['data']['code_verifier'], 'verifier')

    def test_favicon_is_available(self):
        response = self.client.get('/favicon.ico', base_url=module.BASE)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/svg+xml')
        response.close()


if __name__ == '__main__':
    unittest.main()
