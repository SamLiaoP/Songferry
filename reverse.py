"""YouTube Music → Spotify adapters. No network calls occur on import."""
import re
from urllib.parse import parse_qs, urlencode, urlparse
from matching import rank

REVERSE = 'youtube-to-spotify'
FORWARD = 'spotify-to-youtube'


def candidate_id(candidate):
    return candidate.get('id') or candidate.get('videoId')


def youtube_playlist_id(value):
    parsed = urlparse(value)
    if (parsed.scheme != 'https' or parsed.hostname not in
            {'music.youtube.com', 'www.youtube.com', 'youtube.com'} or
            parsed.path not in {'/playlist', '/watch'}):
        raise ValueError('請貼上完整的 YouTube Music 歌單連結，例如 https://music.youtube.com/playlist?list=…')
    values = parse_qs(parsed.query).get('list', [])
    if len(values) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{2,200}', values[0]):
        raise ValueError('連結缺少有效的 list 歌單 ID。')
    return values[0]


def seconds(track):
    value = track.get('duration_seconds')
    if isinstance(value, (int, float)):
        return value
    try:
        result = 0
        for part in track.get('duration', '').split(':'):
            result = result * 60 + int(part)
        return result
    except (ValueError, TypeError):
        return 0


def youtube_tracks(playlist):
    tracks = []
    for item in playlist.get('tracks', []):
        tracks.append({'title': item.get('title') or '無法讀取的歌曲',
                       'artists': [a.get('name', '') for a in item.get('artists') or []],
                       'duration': seconds(item),
                       'skip': not item.get('videoId') or item.get('isAvailable') is False or item.get('resultType') == 'episode'})
    reported = playlist.get('trackCount')
    if isinstance(reported, int) and reported > len(tracks):
        raise ValueError(f'YouTube Music 標示 {reported} 首，但只讀到 {len(tracks)} 首。請改用一般歌單或稍後重試，以免轉移不完整。')
    return tracks


def search_spotify(spotify, track, query):
    response = spotify('search?' + urlencode({'q': query, 'type': 'track', 'limit': 10}))
    results = []
    for item in response.get('tracks', {}).get('items', []):
        tid = item.get('id', '')
        if (not re.fullmatch(r'[A-Za-z0-9]{22}', tid) or item.get('is_local') or
                item.get('is_playable') is False or item.get('restrictions')):
            continue
        results.append({'videoId': tid, 'title': item.get('name', ''),
                        'artists': item.get('artists') or [],
                        'duration_seconds': item.get('duration_ms', 0) / 1000})
    candidates = rank(track, results)
    for candidate in candidates:
        candidate['id'] = candidate.pop('videoId')
        candidate['url'] = 'https://open.spotify.com/track/' + candidate['id']
    return candidates


def append_spotify_batches(spotify, pid, ids):
    """Add up to 100 tracks per request. Returns (written, failed); never retries a possibly accepted write."""
    written = 0
    for start in range(0, len(ids), 100):
        try:
            batch = ids[start:start + 100]
            result = spotify(f'playlists/{pid}/items', method='POST',
                             payload={'uris': ['spotify:track:' + tid for tid in batch]})
            if not result.get('snapshot_id'):
                raise ValueError('Spotify 沒有確認新增結果。')
            written += len(batch)
        except Exception:
            return written, True
    return written, False


def read_spotify_ids(spotify, pid):
    ids = []
    page = spotify(f'playlists/{pid}/items?limit=50')
    while True:
        ids.extend((entry.get('item') or entry.get('track') or {}).get('id') for entry in page.get('items', []))
        if not page.get('next'):
            break
        page = spotify(page['next'])
    return ids


def create_spotify(spotify, job, title, ids, description=None):
    if any(not re.fullmatch(r'[A-Za-z0-9]{22}', tid) for tid in ids):
        raise ValueError('Spotify 歌曲 ID 格式不正確。')
    created = spotify('me/playlists', method='POST', payload={
        'name': title, 'public': False, 'description': description or '由 Songferry 從 YouTube Music 轉移。'})
    pid = created.get('id', '')
    if not re.fullmatch(r'[A-Za-z0-9]{22}', pid):
        raise ValueError('Spotify 未回傳有效歌單 ID，請先到 Spotify 檢查建立結果。')
    url = 'https://open.spotify.com/playlist/' + pid
    job['destination_url'] = url
    written, write_failed = append_spotify_batches(spotify, pid, ids)
    actual = None
    try:
        actual = read_spotify_ids(spotify, pid)
    except Exception:
        actual = None
    verified = actual == ids
    complete = verified or (not write_failed and written == len(ids))
    return {'url': url, 'requested': len(ids), 'actual': len(actual) if actual is not None else None,
            'verified': verified, 'complete': complete, 'confirmed_added': written,
            'warning': '' if complete else '部分歌曲未確認加入，請開啟 Spotify 歌單檢查；工具不會自動重送。'}
