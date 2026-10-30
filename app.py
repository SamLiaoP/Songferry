import base64
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests
from flask import Flask, jsonify, redirect, request, send_file
from connect_youtube import available_browsers, connect as connect_youtube_account, import_from_browser
from matching import rank
from reverse import (FORWARD, REVERSE, candidate_id, youtube_playlist_id, youtube_tracks,
                     search_spotify, create_spotify)

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / '.local'  # Account data and matching history; ignored by git.
PORT = 8876
BASE = f'http://127.0.0.1:{PORT}'
REDIRECT = BASE + '/callback'
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024
csrf = secrets.token_urlsafe(32)
state = {'token': None, 'oauth': {}, 'jobs': {}}
lock = threading.RLock()


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    except (ValueError, OSError):
        return None


def write_private(path, data):
    """Atomically write JSON readable only by the current user."""
    for folder in (LOCAL, path.parent):
        folder.mkdir(mode=0o700, exist_ok=True)
        folder.chmod(0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))
        temporary.chmod(0o600)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def config():
    return read_json(LOCAL / 'config.json') or {}


def save_token(token):
    path = LOCAL / 'spotify_token.json'
    if token:
        write_private(path, token)
    else:
        path.unlink(missing_ok=True)


def jobs_dir():
    return LOCAL / 'jobs'


def save_job(job):
    jid = job.get('id')
    if not jid:
        return
    with lock:
        data = json.dumps(job, ensure_ascii=False, default=str)
    write_private(jobs_dir() / (jid + '.json'), data)


def restore():
    """Reload the Spotify login and matching history saved under .local after a restart."""
    token = read_json(LOCAL / 'spotify_token.json')
    state['token'] = token if isinstance(token, dict) and token.get('access_token') and token.get('refresh_token') else None
    jobs = {}
    for path in sorted(jobs_dir().glob('*.json')):
        job = read_json(path)
        if not isinstance(job, dict) or not isinstance(job.get('rows'), list) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', path.stem):
            continue
        job['id'] = path.stem
        interrupted = job.get('status') == 'matching' or job.get('creating')
        if job.get('status') == 'matching':
            job['status'] = 'error'
            job['error'] = '比對在伺服器重啟時中斷，請重新讀取歌單。'
        job['creating'] = False
        jobs[path.stem] = job
        if interrupted:
            save_job(job)
    state['jobs'] = jobs


def summary(job):
    result = job.get('result') or {}
    return {'id': job.get('id'), 'title': job.get('title', ''), 'direction': job.get('direction', FORWARD),
            'status': job.get('status'), 'created_at': job.get('created_at'), 'total': job.get('total', 0),
            'rows': len(job.get('rows', [])), 'selected': sum(1 for r in job.get('rows', []) if r.get('selected')),
            'attempted': bool(job.get('attempted')), 'error': job.get('error'),
            'url': result.get('url') or job.get('destination_url')}


def yt(auth=False):
    from ytmusicapi import YTMusic
    if auth:
        path = LOCAL / 'browser.json'
        if not path.exists():
            raise ValueError('請先依 README 設定 YouTube Music 的 browser.json，完成後重新整理。')
        return YTMusic(str(path))
    return YTMusic()


def spotify(path, method="GET", payload=None):
    with lock:
        token = state['token']
        if not token:
            raise ValueError('請先連接 Spotify。')
        if token['expires_at'] < time.time() + 60:
            response = requests.post('https://accounts.spotify.com/api/token', data={
                'grant_type': 'refresh_token', 'refresh_token': token['refresh_token'],
                'client_id': config()['client_id']}, timeout=30)
            if not response.ok:
                state['token'] = None
                save_token(None)
                raise ValueError('Spotify 登入已失效，請重新連接。')
            fresh = response.json()
            fresh.setdefault('refresh_token', token['refresh_token'])
            fresh.setdefault('scope', token.get('scope', ''))
            fresh['expires_at'] = time.time() + fresh['expires_in']
            state['token'] = token = fresh
            save_token(fresh)
    url = path if path.startswith('https://api.spotify.com/v1/') else 'https://api.spotify.com/v1/' + path
    if method == 'GET':
        response = requests.get(url, headers={'Authorization': 'Bearer ' + token['access_token']}, timeout=30)
    else:
        response = requests.request(method, url, json=payload, headers={'Authorization': 'Bearer ' + token['access_token']}, timeout=30)
    if response.status_code == 403:
        raise ValueError('Spotify 拒絕請求。請確認授權範圍、Premium 與 App 測試使用者；讀取來源歌單時需為自己擁有或共同編輯。')
    if response.status_code == 401:
        raise ValueError('Spotify 登入已失效，請重新連接 Spotify。')
    if response.status_code == 429:
        raise ValueError('Spotify 請求過多，請稍後重試（Retry-After: ' + response.headers.get('Retry-After', '?') + ' 秒）。')
    if not response.ok:
        raise ValueError(f'Spotify 讀取失敗（HTTP {response.status_code}）。')
    return response.json()


def playlist_id(value):
    if re.fullmatch(r'[A-Za-z0-9]{22}', value):
        return value
    if value.startswith('spotify:playlist:'):
        return playlist_id(value.split(':')[-1])
    parsed = urlparse(value)
    match = re.fullmatch(r'/(?:intl-[a-z]+/)?playlist/([A-Za-z0-9]{22})/?', parsed.path)
    if parsed.scheme == 'https' and parsed.hostname == 'open.spotify.com' and match:
        return match.group(1)
    raise ValueError('請貼上完整的 open.spotify.com/playlist/… 歌單連結。')


def run_match(job, pid):
    try:
        if job.get('direction') == REVERSE:
            return run_reverse_match(job, pid)
        metadata = spotify('playlists/' + pid)
        job['title'] = metadata.get('name', 'Spotify 歌單')
        page = spotify(f'playlists/{pid}/items?limit=50')
        tracks = []
        while True:
            for entry in page.get('items', []):
                item = entry.get('item') or entry.get('track') or {}
                if item.get('type') != 'track' or entry.get('is_local') or item.get('is_local'):
                    tracks.append({'title': item.get('name', '無法讀取的項目'), 'artists': [], 'duration': 0, 'skip': True})
                else:
                    tracks.append({'title': item['name'], 'artists': [a['name'] for a in item.get('artists', [])],
                                   'duration': item.get('duration_ms', 0) / 1000})
            if not page.get('next'):
                break
            page = spotify(page['next'])
        job['total'] = len(tracks)
        client = yt()
        cache = {}
        for track in tracks:
            row = {'track': track, 'candidates': [], 'selected': None, 'error': None}
            if track.get('skip'):
                row['error'] = '本機音檔、Podcast 或無法讀取的歌曲，已略過'
            else:
                query = track['title'] + ' ' + ' '.join(track['artists'])
                try:
                    if query not in cache:
                        cache[query] = client.search(query, filter='songs', limit=8)
                    row['candidates'] = rank(track, cache[query])
                    candidates = row['candidates']
                    if candidates:
                        row['selected'] = candidates[0]['videoId']
                except Exception:
                    row['error'] = '搜尋失敗，可稍後使用手動搜尋重試'
            with lock:
                job['rows'].append(row)
            if len(job['rows']) % 10 == 0:
                save_job(job)
            time.sleep(.15)
        job['status'] = 'ready'
    except Exception as exc:
        job['status'] = 'error'
        job['error'] = str(exc) if isinstance(exc, ValueError) else '服務連線失敗，請檢查網路與帳號設定後重試。'
    finally:
        save_job(job)


def run_reverse_match(job, pid):
    # Public playlists do not need browser credentials; private ones use the saved login.
    client = yt(auth=(LOCAL / 'browser.json').exists())
    metadata = client.get_playlist(pid, limit=None)
    job['title'] = metadata.get('title', 'YouTube Music 歌單')
    tracks = youtube_tracks(metadata)
    job['total'] = len(tracks)
    cache = {}
    for track in tracks:
        row = {'track': track, 'candidates': [], 'selected': None, 'error': None}
        if track.get('skip'):
            row['error'] = '無法讀取、已下架或非歌曲項目，已略過'
        else:
            query = track['title'] + ' ' + ' '.join(track['artists'])
            if query not in cache:
                cache[query] = search_spotify(spotify, track, query)
            row['candidates'] = cache[query]
            if row['candidates']:
                row['selected'] = candidate_id(row['candidates'][0])
        with lock:
            job['rows'].append(row)
        if len(job['rows']) % 10 == 0:
            save_job(job)
        time.sleep(.15)
    job['status'] = 'ready'


def spotify_can_write():
    return 'playlist-modify-private' in (state['token'] or {}).get('scope', '').split()


@app.before_request
def protect():
    if request.host != f'127.0.0.1:{PORT}':
        return jsonify(error='請使用 127.0.0.1 本機網址'), 403
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and (request.headers.get('X-CSRF-Token') != csrf or request.headers.get('Origin') not in (None, BASE)):
        return jsonify(error='工作階段已更新，請重新整理頁面'), 403


@app.after_request
def headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


@app.errorhandler(ValueError)
def invalid(exc):
    return jsonify(error=str(exc)), 400


@app.errorhandler(Exception)
def unexpected(exc):
    from werkzeug.exceptions import HTTPException
    if isinstance(exc, HTTPException):
        return jsonify(error=exc.description), exc.code
    return jsonify(error='連線或服務發生錯誤，請檢查設定後重試。'), 500


@app.get('/')
def index():
    return send_file(ROOT / 'index.html')


@app.get('/app.js')
def javascript():
    return send_file(ROOT / 'app.js')


@app.get('/style.css')
def css():
    return send_file(ROOT / 'style.css')


@app.get('/i18n/<lang>.json')
def translations(lang):
    if lang not in ('en',):
        raise ValueError('不支援的語言。')
    return send_file(ROOT / 'i18n' / (lang + '.json'), mimetype='application/json')


@app.get('/favicon.ico')
@app.get('/favicon.svg')
def favicon():
    return send_file(ROOT / 'favicon.svg', mimetype='image/svg+xml')


@app.get('/api/status')
def status():
    return jsonify(csrf=csrf, spotify=bool(state['token']), client_id=config().get('client_id', ''),
                   youtube=(LOCAL / 'browser.json').exists(), redirect=REDIRECT,
                   youtube_account=(read_json(LOCAL / 'youtube_account.json') or {}).get('accountName', ''),
                   spotify_can_write=spotify_can_write())


@app.post('/api/config')
def save_config():
    client_id = request.json.get('client_id', '').strip()
    if not re.fullmatch(r'[a-fA-F0-9]{32}', client_id):
        raise ValueError('Spotify Client ID 應為 32 位英數字元。')
    write_private(LOCAL / 'config.json', {'client_id': client_id})
    state['token'] = None
    save_token(None)
    return jsonify(ok=True)


@app.post('/api/youtube/connect')
def youtube_connect():
    """Verify pasted request headers against YouTube Music and keep them under .local."""
    text = (request.json or {}).get('text', '')
    if not isinstance(text, str):
        raise ValueError('內容不是請求標頭，請重新複製。')
    name = connect_youtube_account(text, LOCAL)
    return jsonify(ok=True, account=name)


@app.get('/api/youtube/browsers')
def youtube_browsers():
    return jsonify(browsers=available_browsers())


@app.post('/api/youtube/import')
def youtube_import():
    """Read the YouTube login straight from a local browser profile; nothing leaves this machine."""
    browser = (request.json or {}).get('browser')
    if not isinstance(browser, str):
        raise ValueError('不支援的瀏覽器。')
    name = import_from_browser(browser, LOCAL)
    return jsonify(ok=True, account=name)


@app.post('/api/youtube/disconnect')
def youtube_disconnect():
    for name in ('browser.json', 'youtube_account.json'):
        (LOCAL / name).unlink(missing_ok=True)
    return jsonify(ok=True)


@app.post('/api/logout')
def logout():
    state['token'] = None
    state['oauth'] = {}
    save_token(None)
    return jsonify(ok=True)


@app.post('/api/login')
def login():
    client_id = config().get('client_id')
    if not client_id:
        return jsonify(error='尚未設定 Spotify Client ID，請先完成下方的首次連接設定。', code='spotify_setup_required'), 400
    direction = (request.json or {}).get('direction', FORWARD)
    if direction not in (FORWARD, REVERSE):
        raise ValueError('無效的轉換方向。')
    scope = 'playlist-read-private playlist-read-collaborative'
    if direction == REVERSE:
        scope += ' playlist-modify-private'
    verifier = secrets.token_urlsafe(64)
    nonce = secrets.token_urlsafe(32)
    state['oauth'] = {'state': nonce, 'verifier': verifier, 'time': time.time(), 'direction': direction}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return jsonify(url='https://accounts.spotify.com/authorize?' + urlencode({
        'client_id': client_id, 'response_type': 'code', 'redirect_uri': REDIRECT,
        'scope': scope, 'state': nonce,
        'code_challenge_method': 'S256', 'code_challenge': challenge}))


@app.get('/callback')
def callback():
    pending = state['oauth']
    if not pending or not secrets.compare_digest(request.args.get('state', ''), pending['state']) or time.time() - pending['time'] > 600:
        raise ValueError('登入要求已失效，請回首頁重新連接。')
    state['oauth'] = {}
    if request.args.get('error') or not request.args.get('code'):
        return redirect('/?' + urlencode({'login': 'cancelled', 'direction': pending.get('direction', FORWARD)}))
    response = requests.post('https://accounts.spotify.com/api/token', data={
        'client_id': config()['client_id'], 'grant_type': 'authorization_code', 'code': request.args['code'],
        'redirect_uri': REDIRECT, 'code_verifier': pending['verifier']}, timeout=30)
    if not response.ok:
        raise ValueError('Spotify 登入交換失敗，請確認 Redirect URI 設定。')
    token = response.json()
    token['expires_at'] = time.time() + token['expires_in']
    state['token'] = token
    save_token(token)
    if pending.get('direction') == REVERSE:
        return redirect('/?direction=' + REVERSE)
    return redirect('/')


@app.post('/api/jobs')
def start_job():
    direction = request.json.get('direction', FORWARD)
    if direction not in (FORWARD, REVERSE):
        raise ValueError('無效的轉換方向。')
    if direction == REVERSE and not state['token']:
        raise ValueError('請先連接 Spotify，才能搜尋對應歌曲。')
    url = request.json.get('url', '').strip()
    pid = (youtube_playlist_id if direction == REVERSE else playlist_id)(url)
    with lock:
        if any(j['status'] == 'matching' for j in state['jobs'].values()):
            raise ValueError('已有歌單正在比對，請等待完成。')
        jid = secrets.token_urlsafe(16)
        job = {'id': jid, 'status': 'matching', 'title': '', 'rows': [], 'total': 0, 'error': None,
               'result': None, 'creating': False, 'attempted': False, 'direction': direction,
               'created_at': time.time(), 'source_url': url, 'target_title': ''}
        state['jobs'][jid] = job
    save_job(job)
    threading.Thread(target=run_match, args=(job, pid), daemon=True).start()
    return jsonify(id=jid)


@app.get('/api/jobs')
def list_jobs():
    with lock:
        jobs = [summary(j) for j in state['jobs'].values()]
    jobs.sort(key=lambda j: j.get('created_at') or 0, reverse=True)
    return jsonify(jobs=jobs)


def get_job(jid):
    job = state['jobs'].get(jid)
    if job is None:
        raise ValueError('找不到工作。伺服器重啟後需重新讀取歌單。')
    return job


@app.get('/api/jobs/<jid>')
def job_status(jid):
    with lock:
        return jsonify(get_job(jid))


@app.delete('/api/jobs/<jid>')
def delete_job(jid):
    with lock:
        job = get_job(jid)
        if job['status'] == 'matching' or job.get('creating'):
            raise ValueError('工作仍在進行中，請等待完成後再刪除。')
        del state['jobs'][jid]
    if job.get('id'):
        (jobs_dir() / (job['id'] + '.json')).unlink(missing_ok=True)
    return jsonify(ok=True)


@app.post('/api/jobs/<jid>/selections')
def save_selections(jid):
    """Remember the reviewer's choices so a restart or another tab shows the same picks."""
    data = request.json or {}
    selections = data.get('selections', [])
    if not isinstance(selections, list):
        raise ValueError('歌曲選擇資料格式不正確。')
    with lock:
        job = get_job(jid)
        if len(selections) > len(job['rows']):
            raise ValueError('歌曲選擇資料不完整。')
        for row, selected in zip(job['rows'], selections):
            if selected is not None and (not isinstance(selected, str) or selected not in {candidate_id(c) for c in row['candidates']}):
                raise ValueError('所選歌曲不在比對結果中。')
        for row, selected in zip(job['rows'], selections):
            row['selected'] = selected
        if isinstance(data.get('title'), str):
            job['target_title'] = data['title'].strip()[:150]
    save_job(job)
    return jsonify(ok=True)


@app.post('/api/search')
def search():
    data = request.json
    job = get_job(data['job'])
    if job['status'] != 'ready':
        raise ValueError('請先等待比對完成。')
    index = data.get('index')
    if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(job['rows']):
        raise ValueError('無效的歌曲編號。')
    query = data.get('query', '').strip()[:300]
    if not query:
        raise ValueError('請輸入歌名與歌手。')
    row = job['rows'][index]
    if job.get('direction') == REVERSE:
        candidates = search_spotify(spotify, row['track'], query)
    else:
        candidates = rank(row['track'], yt().search(query, filter='songs', limit=10))
    known = {candidate_id(c) for c in row['candidates']}
    with lock:
        row['candidates'].extend(c for c in candidates if candidate_id(c) not in known)
    save_job(job)
    return jsonify(candidates=candidates)


@app.post('/api/create')
def create():
    data = request.json
    job = get_job(data['job'])
    with lock:
        if job['result']:
            return jsonify(job['result'])
        if job['creating'] or job['attempted']:
            raise ValueError('此工作已送出建立要求。請先到 YouTube Music 檢查結果，避免重複建立。')
        if job['status'] != 'ready':
            raise ValueError('請先等待比對完成。')
        selections = data.get('selections', [])
        if not isinstance(selections, list) or len(selections) != len(job['rows']):
            raise ValueError('歌曲選擇資料不完整。')
        ids = []
        for row, selected in zip(job['rows'], selections):
            if selected is not None:
                if not isinstance(selected, str) or selected not in {candidate_id(c) for c in row['candidates']}:
                    raise ValueError('所選歌曲不在比對結果中。')
                ids.append(selected)
        if not ids:
            raise ValueError('請至少選擇一首歌曲。')
        title = data.get('title', '').strip()[:150]
        if not title:
            raise ValueError('請輸入目標歌單名稱。')
        english = data.get('lang') == 'en'
        is_reverse = job.get('direction') == REVERSE
        if is_reverse:
            if not spotify_can_write():
                raise ValueError('請重新連接 Spotify，授權建立私人歌單後再試。')
            client = None
        else:
            client = yt(auth=True)
        for row, selected in zip(job['rows'], selections):
            row['selected'] = selected
        job['target_title'] = title
        job['creating'] = True
        job['attempted'] = True
    save_job(job)
    try:
        if is_reverse:
            job['result'] = create_spotify(spotify, job, title, ids,
                                           'Transferred from YouTube Music by Songferry.' if english else None)
            return jsonify(job['result'])
        result = client.create_playlist(title, 'Transferred from Spotify by Songferry.' if english else '由 Songferry 從 Spotify 轉移。',
                                        privacy_status='PRIVATE', video_ids=ids)
        if not isinstance(result, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', result):
            raise ValueError('YouTube Music 未回傳有效歌單 ID。請先檢查帳號中的歌單，避免重複建立。')
        # Read back the playlist rather than treating an accepted request as a verified transfer.
        count = None
        try:
            created = client.get_playlist(result, limit=None)
            actual = [t.get('videoId') for t in created.get('tracks', [])]
            count = len(actual)
            verified = actual == ids
        except Exception:
            verified = False
        job['result'] = {'url': 'https://music.youtube.com/playlist?list=' + result,
                         'requested': len(ids), 'actual': count, 'verified': verified}
        return jsonify(job['result'])
    except Exception:
        destination = 'Spotify' if is_reverse else 'YouTube Music'
        raise ValueError('建立結果未確認。請先到 ' + destination + ' 查看是否已有歌單；此工作不會自動重試，以免重複建立。')
    finally:
        job['creating'] = False
        save_job(job)


if __name__ == '__main__':
    restore()
    print('\nSongferry → ' + BASE + '\n')
    app.run(host='127.0.0.1', port=PORT, debug=False, threaded=True)
