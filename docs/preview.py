"""Run Songferry with fake accounts and sample history, for taking the README screenshots.

    .venv/bin/python docs/preview.py            # http://127.0.0.1:8877

Nothing here touches the real `.local/` folder or any service: the sample data lives in a
temporary folder, the Spotify and YouTube Music clients are replaced with stubs, and the
account names are made up. Open /accounts, /transfer?job=<id> and /links in a browser
about 1360 pixels wide and crop as needed.
"""
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app as module  # noqa: E402

PORT = 8877
TEMP = Path(tempfile.mkdtemp(prefix='songferry-preview-'))
module.LOCAL = TEMP / '.local'
module.PORT = PORT
module.BASE = f'http://127.0.0.1:{PORT}'
module.REDIRECT = module.BASE + '/callback'

DAY = 86400
NOW = time.time()


def at(days_ago, hour, minute=0):
    day = datetime.now() - timedelta(days=days_ago)
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()


def yt_track(title, artist, seconds, url=None):
    track = {'title': title, 'artists': [artist], 'duration': seconds}
    if url:
        track['url'] = url
    return track


def yt_candidate(title, artist, seconds, score, warnings=(), video='dQw4w9WgXcQ'):
    return {'videoId': video, 'title': title, 'artists': [artist], 'duration': seconds, 'score': score,
            'warnings': list(warnings)}


def sp_candidate(title, artist, seconds, score, warnings=(), tid='4cOdK2wGLETKBW3PvgPWqT'):
    return {'id': tid, 'url': 'https://open.spotify.com/track/' + tid, 'title': title, 'artists': [artist],
            'duration': seconds, 'score': score, 'warnings': list(warnings)}


def rows(songs, reverse=False):
    out = []
    for index, (title, artist, seconds, score, warnings) in enumerate(songs):
        video = f'preview{index:03d}xyz'
        tid = f'{index:022d}'[-22:].replace('0', 'a')
        if reverse:
            candidate = sp_candidate(title, artist, seconds, score, warnings, tid=tid)
            alternative = sp_candidate(title + ' (Live)', artist, seconds + 31, max(score - 30, 40), ['現場版本'], tid=tid[::-1])
            track = yt_track(title, artist, seconds)
        else:
            candidate = yt_candidate(title, artist, seconds, score, warnings, video=video)
            alternative = yt_candidate(title + ' (Cover)', 'Someone Else', seconds - 12, max(score - 35, 40), ['翻唱版本'], video=video[::-1])
            track = yt_track(title, artist, seconds)
        out.append({'track': track, 'candidates': [candidate, alternative],
                    'selected': module.candidate_id(candidate), 'error': None})
    return out


NIGHT_DRIVE = [
    ('晴天', '周杰倫', 269, 97, []), ('小幸運', '田馥甄', 266, 84, ['長度相差 14 秒']), ('倒數', 'G.E.M. 鄧紫棋', 233, 97, []),
    ('說好不哭', '周杰倫', 223, 97, []), ('漂向北方', '黃明志', 288, 92, []), ('你，好不好？', '周興哲', 263, 95, []),
    ('光年之外', 'G.E.M. 鄧紫棋', 235, 96, []), ('演員', '薛之謙', 261, 93, []), ('告白氣球', '周杰倫', 215, 98, []),
    ('追光者', '岑寧兒', 234, 90, ['歌手名稱不一致']), ('體面', '于文文', 265, 94, []), ('剛好遇見你', '李玉剛', 200, 91, []),
]
WORKOUT = [
    ('Blinding Lights', 'The Weeknd', 200, 98, []), ('Levitating', 'Dua Lipa', 203, 97, []),
    ('Physical', 'Dua Lipa', 194, 96, []), ('Stronger', 'Kanye West', 312, 95, []),
    ('Titanium', 'David Guetta', 245, 94, []), ('Don\'t Start Now', 'Dua Lipa', 183, 97, []),
    ('Lose Yourself', 'Eminem', 326, 96, []), ('Eye of the Tiger', 'Survivor', 245, 93, []),
]
CAFE = [
    ('Sunday Morning', 'Maroon 5', 244, 96, []), ('Put Your Records On', 'Corinne Bailey Rae', 215, 95, []),
    ('Banana Pancakes', 'Jack Johnson', 192, 97, []), ('Better Together', 'Jack Johnson', 207, 96, []),
    ('Lucky', 'Jason Mraz', 189, 92, ['長度相差 9 秒']),
]

JOBS = {
    'preview-night-drive-0001': {
        'title': '深夜開車歌單', 'direction': module.FORWARD, 'status': 'ready', 'created_at': at(0, 9, 0),
        'total': len(NIGHT_DRIVE), 'rows': rows(NIGHT_DRIVE), 'source_url': 'https://open.spotify.com/playlist/preview',
        'target_title': '', 'attempted': False, 'creating': False},
    'preview-workout-mix-0002': {
        'title': 'Workout Mix 2026', 'direction': module.REVERSE, 'status': 'ready', 'created_at': at(2, 9, 30),
        'total': len(WORKOUT), 'rows': rows(WORKOUT, reverse=True), 'source_url': 'https://music.youtube.com/playlist?list=preview',
        'target_title': 'Workout Mix 2026', 'attempted': True, 'creating': False,
        'result': {'url': 'https://open.spotify.com/playlist/preview', 'requested': 8, 'actual': 8, 'verified': True}},
    'preview-cafe-afternoon-003': {
        'title': '咖啡店下午', 'direction': module.FORWARD, 'status': 'ready', 'created_at': at(6, 9, 30),
        'total': len(CAFE), 'rows': rows(CAFE), 'source_url': 'https://open.spotify.com/playlist/preview2',
        'target_title': '咖啡店下午', 'attempted': True, 'creating': False,
        'result': {'url': 'https://music.youtube.com/playlist?list=preview', 'requested': 5, 'actual': 5, 'verified': True}},
}


def seed():
    module.write_private(module.LOCAL / 'config.json', {'client_id': 'a' * 32})
    module.write_private(module.LOCAL / 'spotify_token.json', {
        'access_token': 'preview', 'refresh_token': 'preview', 'expires_at': NOW + 10 * DAY,
        'scope': 'playlist-read-private playlist-read-collaborative playlist-modify-private playlist-modify-public'})
    module.write_private(module.LOCAL / 'spotify_account.json', {'accountName': 'alex.listens'})
    module.write_private(module.LOCAL / 'browser.json', {'preview': True})
    module.write_private(module.LOCAL / 'youtube_account.json', {'accountName': 'Alex Chen'})
    for jid, job in JOBS.items():
        module.write_private(module.jobs_dir() / (jid + '.json'), dict(job, id=jid))


def stub_services():
    """Keep every screenshot offline: playlist lists come from here and nothing else is reachable."""
    module.spotify = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('預覽模式沒有連線。'))
    module.yt = lambda auth=False: None
    module.spotify_playlists = lambda client: [
        {'id': 'b' * 22, 'name': '深夜開車歌單', 'count': 12}, {'id': 'c' * 22, 'name': 'Workout Mix 2026', 'count': 8}]
    module.youtube_playlists = lambda client: [
        {'id': 'PLpreview0001', 'name': '咖啡店下午', 'count': 5}, {'id': 'PLpreview0002', 'name': '通勤', 'count': 31}]


if __name__ == '__main__':
    seed()
    stub_services()
    module.restore()
    print('\nSongferry preview → ' + module.BASE + '  (sample data in ' + str(TEMP) + ')\n')
    for jid, job in JOBS.items():
        print(f'  {module.BASE}/transfer?job={jid}   {job["title"]}')
    module.app.run(host='127.0.0.1', port=PORT, debug=False, threaded=True)
