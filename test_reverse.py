import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse
import app as module
from reverse import REVERSE, FORWARD, youtube_playlist_id, youtube_tracks, search_spotify, create_spotify

TRACK_A = 'a' * 22
TRACK_B = 'b' * 22
PLAYLIST = 'p' * 22


class ReverseAdaptersTests(unittest.TestCase):
    def test_youtube_urls_require_a_real_host_and_list(self):
        self.assertEqual(youtube_playlist_id('https://music.youtube.com/playlist?list=PLaXJTdswsoIA&si=test'), 'PLaXJTdswsoIA')
        self.assertEqual(youtube_playlist_id('https://www.youtube.com/watch?v=song&list=PL_test'), 'PL_test')
        for value in ['https://music.youtube.com.evil.test/playlist?list=PL_test',
                      'https://music.youtube.com/playlist', 'http://music.youtube.com/playlist?list=PL_test',
                      'https://music.youtube.com/playlist?list=PL_one&list=PL_two']:
            with self.assertRaises(ValueError):
                youtube_playlist_id(value)

    def test_partial_source_is_rejected_instead_of_silently_truncated(self):
        with self.assertRaisesRegex(ValueError, '只讀到 1'):
            youtube_tracks({'trackCount':3, 'tracks':[{'title':'One','videoId':'one'}]})

    def test_duration_is_decoded_and_unavailable_entries_keep_positions(self):
        tracks=youtube_tracks({'trackCount':3,'tracks':[
            {'title':'Song','videoId':'one','artists':[{'name':'Singer'}],'duration':'1:02:03'},
            {'title':'Removed','videoId':None},
            {'title':'Unavailable','videoId':'two','isAvailable':False}]})
        self.assertEqual(tracks[0]['duration'],3723)
        self.assertFalse(tracks[0]['skip'])
        self.assertEqual([t['title'] for t in tracks],['Song','Removed','Unavailable'])
        self.assertTrue(tracks[1]['skip'])
        self.assertTrue(tracks[2]['skip'])

    def test_spotify_candidates_exclude_unavailable_tracks_and_use_spotify_urls(self):
        spotify=Mock(return_value={'tracks':{'items':[
            {'id':TRACK_A,'name':'Song','artists':[{'name':'Artist'}],'duration_ms':200000},
            {'id':TRACK_B,'name':'Song','is_playable':False,'artists':[]}]}})
        results=search_spotify(spotify,{'title':'Song','artists':['Artist'],'duration':200},'Song Artist')
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]['id'],TRACK_A)
        self.assertEqual(results[0]['url'],'https://open.spotify.com/track/'+TRACK_A)
        self.assertNotIn('videoId',results[0])
        self.assertEqual(parse_qs(urlparse(spotify.call_args.args[0]).query)['limit'],['10'])

    def test_batching_preserves_order_duplicates_and_verifies_all_pages(self):
        ids=[TRACK_A,TRACK_B]*51
        def api(path, method='GET', payload=None):
            if path=='me/playlists':
                self.assertIs(payload['public'],False)
                return {'id':PLAYLIST}
            if method=='POST':return {'snapshot_id':'ok'}
            if path.startswith('https:'):
                return {'items':[{'track':{'id':tid}} for tid in ids[50:]],'next':None}
            return {'items':[{'item':{'id':tid}} for tid in ids[:50]], 'next':'https://api.spotify.com/v1/playlists/'+PLAYLIST+'/items?offset=50'}
        spotify=Mock(side_effect=api)
        result=create_spotify(spotify,{},'New',ids)
        self.assertTrue(result['verified'])
        batches=[call.kwargs['payload']['uris'] for call in spotify.call_args_list if call.kwargs.get('method')=='POST' and call.args[0]!='me/playlists']
        self.assertEqual([len(batch) for batch in batches],[100,2])
        self.assertEqual([uri for batch in batches for uri in batch],['spotify:track:'+tid for tid in ids])

    def test_partial_write_keeps_playlist_link_and_never_retries(self):
        ids=[TRACK_A]*101
        writes=0
        def api(path, method='GET', payload=None):
            nonlocal writes
            if path=='me/playlists':return {'id':PLAYLIST}
            if method=='POST':
                writes+=1
                if writes==2:raise TimeoutError('uncertain')
                return {'snapshot_id':'ok'}
            return {'items':[{'item':{'id':TRACK_A}} for _ in range(100)],'next':None}
        result=create_spotify(api,{},'New',ids)
        self.assertFalse(result['complete'])
        self.assertFalse(result['verified'])
        self.assertEqual(result['actual'],100)
        self.assertEqual(result['url'],'https://open.spotify.com/playlist/'+PLAYLIST)
        self.assertEqual(writes,2)


class ReverseApiTests(unittest.TestCase):
    def setUp(self):
        self.previous_token=module.state['token']
        self.previous_jobs=module.state['jobs']
        self.previous_oauth=module.state['oauth']
        module.state['jobs']={}
        module.state['token']={'scope':'playlist-read-private playlist-modify-private'}
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.local=patch.object(module,'LOCAL',Path(self.temp.name)/'.local');self.local.start();self.addCleanup(self.local.stop)
        self.client=module.app.test_client()
        self.headers={'X-CSRF-Token':module.csrf,'Origin':module.BASE}

    def tearDown(self):
        module.state['token']=self.previous_token
        module.state['jobs']=self.previous_jobs
        module.state['oauth']=self.previous_oauth

    def post(self,path,data):
        return self.client.post('/api/'+path,json=data,headers=self.headers,base_url=module.BASE)

    def ready_job(self):
        row={'track':{'title':'Song','artists':['Artist'],'duration':200},'selected':TRACK_A,'error':None,
             'candidates':[{'id':TRACK_A,'url':'https://open.spotify.com/track/'+TRACK_A,'title':'Song','artists':['Artist'],'duration':200,'score':100,'warnings':[]}]}
        return {'direction':REVERSE,'title':'Source','status':'ready','rows':[row],'total':1,
                'creating':False,'attempted':False,'result':None,'error':None}

    def test_new_direction_is_validated_before_starting_job(self):
        with patch.object(module.threading,'Thread') as thread:
            self.assertEqual(self.post('jobs',{'direction':'invalid','url':'anything'}).status_code,400)
            self.assertEqual(self.post('jobs',{'direction':REVERSE,'url':'https://evil.test/playlist?list=PL_one'}).status_code,400)
        thread.assert_not_called()

    def test_reverse_match_gets_entire_source_and_defaults_first_candidate(self):
        client=Mock()
        client.get_playlist.return_value={'title':'Source','trackCount':2,'tracks':[
            {'title':'Song','videoId':'v1','artists':[{'name':'Artist'}],'duration_seconds':200},
            {'title':'Removed','videoId':None}]}
        candidates=[{'id':TRACK_A,'score':30,'warnings':['歌手名稱不一致']}]
        job={'direction':REVERSE,'rows':[]}
        with patch.object(module,'yt',return_value=client),patch.object(module,'search_spotify',return_value=candidates),patch.object(module.time,'sleep'):
            module.run_match(job,'PL_test')
        client.get_playlist.assert_called_once_with('PL_test',limit=None)
        self.assertEqual(job['status'],'ready')
        self.assertEqual(job['rows'][0]['selected'],TRACK_A)
        self.assertIsNone(job['rows'][1]['selected'])

    def test_write_permission_is_requested_only_for_reverse_login(self):
        for direction,expected in [(FORWARD,False),(REVERSE,True)]:
            with patch.object(module,'config',return_value={'client_id':'a'*32}):
                result=self.post('login',{'direction':direction})
            scopes=parse_qs(urlparse(result.json['url']).query)['scope'][0].split()
            self.assertEqual('playlist-modify-private' in scopes,expected)
            self.assertNotIn('playlist-modify-public',scopes)

    def test_missing_write_permission_does_not_attempt_create(self):
        module.state['token']={'scope':'playlist-read-private'}
        job=self.ready_job();module.state['jobs']['j']=job
        with patch.object(module,'create_spotify') as create:
            result=self.post('create',{'job':'j','title':'New','selections':[TRACK_A]})
        self.assertEqual(result.status_code,400)
        self.assertIn('授權',result.json['error'])
        self.assertFalse(job['attempted'])
        create.assert_not_called()

    def test_reverse_create_is_idempotent_and_does_not_touch_youtube(self):
        job=self.ready_job();module.state['jobs']['j']=job
        result={'url':'https://open.spotify.com/playlist/'+PLAYLIST,'requested':1,'actual':1,'verified':True,'complete':True}
        with patch.object(module,'create_spotify',return_value=result) as create,patch.object(module,'yt') as youtube:
            first=self.post('create',{'job':'j','title':'New','selections':[TRACK_A]})
            second=self.post('create',{'job':'j','title':'New','selections':[TRACK_A]})
        self.assertEqual(first.json,second.json)
        self.assertEqual(first.json['url'],result['url'])
        create.assert_called_once()
        youtube.assert_not_called()

    def test_foreign_candidate_cannot_be_injected_into_reverse_create(self):
        module.state['jobs']['j']=self.ready_job()
        with patch.object(module,'create_spotify') as create:
            result=self.post('create',{'job':'j','title':'New','selections':[TRACK_B]})
        self.assertEqual(result.status_code,400)
        create.assert_not_called()


if __name__=='__main__':
    unittest.main()
