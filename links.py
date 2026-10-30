"""Paste-anything import: find song links in messy text and add them to a playlist.

No network calls on import. Everything that talks to a service takes the client or
callable as an argument so it can be exercised with fakes.
"""
import re
from matching import rank
from reverse import append_spotify_batches, read_spotify_ids, search_spotify

YOUTUBE_ID = r'[A-Za-z0-9_-]{11}'
SPOTIFY_ID = r'[A-Za-z0-9]{22}'
URL = re.compile(r'https?://[^\s<>"\'）)\]】」]+')
SONG_LINKS = [
    ('youtube', re.compile(r'https?://(?:www\.|m\.|music\.)?youtube\.com/(?:watch\?(?:[^\s#]*?&)?v=|shorts/|embed/|live/)(' + YOUTUBE_ID + r')(?![A-Za-z0-9_-])')),
    ('youtube', re.compile(r'https?://youtu\.be/(' + YOUTUBE_ID + r')(?![A-Za-z0-9_-])')),
    ('spotify', re.compile(r'https?://open\.spotify\.com/(?:intl-[a-z-]+/)?track/(' + SPOTIFY_ID + r')(?![A-Za-z0-9])')),
]
SPOTIFY_URI = re.compile(r'spotify:track:(' + SPOTIFY_ID + r')(?![A-Za-z0-9])')
NOISE = re.compile(r'\b(official|mv|m/v|music video|lyric|lyrics|video|audio|visuali[sz]er|hd|4k|remaster(?:ed)?|ver\.?|version|live video|full)\b|官方|完整版|歌詞|動態歌詞|高音質', re.I)
BRACKETS = re.compile(r'[\(\[【（「『]([^\)\]】）」』]*)[\)\]】）」』]')
TARGETS = ('youtube', 'spotify')


def extract_links(text):
    """Song links in order of appearance, de-duplicated, plus the URLs that were skipped and why."""
    items, seen, unsupported = [], set(), []
    for match in URL.finditer(text or ''):
        url = match.group(0).rstrip('.,;:!?')
        for platform, pattern in SONG_LINKS:
            found = pattern.match(url)
            if found:
                key = (platform, found.group(1))
                if key not in seen:
                    seen.add(key)
                    items.append({'platform': platform, 'id': found.group(1), 'url': url})
                break
        else:
            host = re.sub(r'^https?://(?:www\.)?', '', url).split('/')[0].lower()
            if 'youtu' in host or 'spotify' in host:
                unsupported.append({'url': url, 'reason': '不是單首歌曲的連結（例如歌單、頻道或專輯）'})
            else:
                unsupported.append({'url': url, 'reason': '不支援的網站'})
    for found in SPOTIFY_URI.finditer(text or ''):
        key = ('spotify', found.group(1))
        if key not in seen:
            seen.add(key)
            items.append({'platform': 'spotify', 'id': found.group(1), 'url': 'https://open.spotify.com/track/' + found.group(1)})
    return items, unsupported


def clean_title(title):
    """Drop bracketed marketing noise such as (Official Video) or 【MV】 but keep bracketed names."""
    def keep(match):
        return '' if NOISE.search(match.group(1)) else match.group(0)
    cleaned = BRACKETS.sub(keep, title or '')
    cleaned = re.sub(r'\s*[\|｜]\s*.*$', '', cleaned)  # "Song | Official Site" style suffixes
    cleaned = NOISE.sub('', cleaned) if re.search(r'\b(official|lyric)', cleaned, re.I) else cleaned
    return re.sub(r'\s{2,}', ' ', cleaned).strip(' -–—_·')


def clean_author(author):
    author = re.sub(r'\s*-\s*Topic$', '', author or '', flags=re.I)
    author = re.sub(r'(VEVO|Official|Music Channel|Ch\.)$', '', author, flags=re.I).strip()
    return author


def resolve_youtube(client, item):
    """Look a YouTube video up in YouTube Music's catalogue (works for plain YouTube links too)."""
    try:
        song = client.get_song(item['id'])
    except Exception:
        return {'title': item['url'], 'artists': [], 'duration': 0, 'skip': True, 'url': item['url'], 'source': 'youtube', 'id': item['id']}
    details = song.get('videoDetails') or {}
    status = (song.get('playabilityStatus') or {}).get('status')
    title = clean_title(details.get('title', ''))
    track = {'title': title or item['url'], 'artists': [a for a in [clean_author(details.get('author', ''))] if a],
             'duration': int(details.get('lengthSeconds') or 0), 'url': item['url'], 'source': 'youtube', 'id': item['id']}
    if not title or status not in (None, 'OK'):
        track['skip'] = True
    return track


def resolve_spotify(spotify, items):
    """Batch-fetch Spotify tracks (50 per request); unknown ids become skipped rows."""
    found = {}
    ids = [i['id'] for i in items]
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        response = spotify('tracks?ids=' + ','.join(batch))
        for tid, track in zip(batch, response.get('tracks') or []):
            if track and track.get('id'):
                found[tid] = track
    tracks = []
    for item in items:
        track = found.get(item['id'])
        if not track or track.get('is_local'):
            tracks.append({'title': item['url'], 'artists': [], 'duration': 0, 'skip': True, 'url': item['url'], 'source': 'spotify', 'id': item['id']})
        else:
            tracks.append({'title': track.get('name', ''), 'artists': [a['name'] for a in track.get('artists', [])],
                           'duration': track.get('duration_ms', 0) / 1000, 'url': item['url'], 'source': 'spotify', 'id': item['id']})
    return tracks


def direct_candidate(track, target):
    """A link that already lives on the target service needs no matching."""
    base = {'title': track['title'], 'artists': track['artists'], 'duration': track['duration'], 'score': 100, 'warnings': []}
    if target == 'youtube':
        return dict(base, videoId=track['id'])
    return dict(base, id=track['id'], url='https://open.spotify.com/track/' + track['id'])


def run_links_match(job, items, yt, spotify, lock, save, sleep=lambda: None):
    """Resolve every link, then match the ones that live on the other service. Fills job['rows'] in place."""
    target = job['target']
    yt_client = None
    youtube_items = [i for i in items if i['platform'] == 'youtube']
    spotify_items = [i for i in items if i['platform'] == 'spotify']
    resolved = {}
    if youtube_items:
        yt_client = yt()
        for item in youtube_items:
            resolved[('youtube', item['id'])] = resolve_youtube(yt_client, item)
    if spotify_items:
        for item, track in zip(spotify_items, resolve_spotify(spotify, spotify_items)):
            resolved[('spotify', item['id'])] = track
    job['total'] = len(items)
    cache = {}
    for item in items:
        track = resolved[(item['platform'], item['id'])]
        row = {'track': track, 'candidates': [], 'selected': None, 'error': None}
        if track.get('skip'):
            row['error'] = '無法讀取、已下架或非歌曲項目，已略過'
        elif item['platform'] == target:
            row['candidates'] = [direct_candidate(track, target)]
            row['selected'] = track['id']
        else:
            query = track['title'] + ' ' + ' '.join(track['artists'])
            try:
                if query not in cache:
                    if target == 'youtube':
                        yt_client = yt_client or yt()
                        cache[query] = rank(track, yt_client.search(query, filter='songs', limit=8))
                    else:
                        cache[query] = search_spotify(spotify, track, query)
                row['candidates'] = list(cache[query])
                if row['candidates']:
                    row['selected'] = row['candidates'][0].get('id') or row['candidates'][0].get('videoId')
            except Exception:
                row['error'] = '搜尋失敗，可稍後使用手動搜尋重試'
        with lock:
            job['rows'].append(row)
        if len(job['rows']) % 10 == 0:
            save(job)
        sleep()
    job['status'] = 'ready'


def add_to_youtube_playlist(client, playlist_id, ids):
    """Append to an existing YouTube Music playlist, skipping songs already in it, then read back."""
    before = client.get_playlist(playlist_id, limit=None)
    have = {t.get('videoId') for t in before.get('tracks', [])}
    new = []
    for vid in ids:
        if vid not in have:
            have.add(vid)
            new.append(vid)
    if new:
        result = client.add_playlist_items(playlist_id, new, duplicates=True)
        status = result.get('status', '') if isinstance(result, dict) else str(result)
        if 'SUCCEEDED' not in status:
            raise ValueError('YouTube Music 未確認加入結果。')
    actual = None
    try:
        actual = [t.get('videoId') for t in client.get_playlist(playlist_id, limit=None).get('tracks', [])]
        verified = all(vid in set(actual) for vid in ids) and (not new or actual[-len(new):] == new)
    except Exception:
        verified = False
    return {'url': 'https://music.youtube.com/playlist?list=' + playlist_id, 'requested': len(ids), 'added': len(new),
            'skipped_existing': len(ids) - len(new), 'actual': len(actual) if actual is not None else None,
            'verified': verified, 'complete': True, 'warning': ''}


def add_to_spotify_playlist(spotify, job, playlist_id, ids):
    """Append to an existing Spotify playlist in batches of 100, skipping songs already in it, then read back."""
    url = 'https://open.spotify.com/playlist/' + playlist_id
    job['destination_url'] = url
    have = set(read_spotify_ids(spotify, playlist_id))
    new = []
    for tid in ids:
        if tid not in have:
            have.add(tid)
            new.append(tid)
    written, failed = append_spotify_batches(spotify, playlist_id, new)
    actual = None
    try:
        actual = read_spotify_ids(spotify, playlist_id)
        verified = all(tid in set(actual) for tid in ids) and (not new or actual[-len(new):] == new)
    except Exception:
        verified = False
    complete = verified or (not failed and written == len(new))
    return {'url': url, 'requested': len(ids), 'added': written, 'skipped_existing': len(ids) - len(new),
            'actual': len(actual) if actual is not None else None, 'verified': verified, 'complete': complete,
            'warning': '' if complete else '部分歌曲未確認加入，請開啟 Spotify 歌單檢查；工具不會自動重送。'}


def youtube_playlists(client):
    playlists = []
    for entry in client.get_library_playlists(limit=None) or []:
        pid = entry.get('playlistId')
        if pid and pid not in ('LM', 'SE'):
            playlists.append({'id': pid, 'name': entry.get('title', ''), 'count': entry.get('count')})
    return playlists


def spotify_playlists(spotify):
    """Playlists the signed-in user owns. Spotify reports the public flag inconsistently, so ownership is the only filter."""
    me = spotify('me').get('id')
    playlists = []
    page = spotify('me/playlists?limit=50')
    while True:
        for entry in page.get('items', []):
            if (entry.get('owner') or {}).get('id') == me and re.fullmatch(r'[A-Za-z0-9]{22}', entry.get('id', '')):
                count = (entry.get('tracks') or {}).get('total')
                if count is None:
                    count = (entry.get('items') or {}).get('total')
                playlists.append({'id': entry['id'], 'name': entry.get('name', ''), 'count': count})
        if not page.get('next'):
            break
        page = spotify(page['next'])
    return playlists
