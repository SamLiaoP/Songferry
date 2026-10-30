"""Read locally copied request headers, verify YT Music, and save privately.
Never prints request headers, cookies, tokens, or service exception details.
"""
import glob
import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path


class SetupError(ValueError):
    pass


def parse_curl(text):
    """Chromium/Firefox "Copy as cURL" (bash). Only the -H/--header and -b/--cookie values are read."""
    cleaned = text.replace('\\\n', ' ').replace('^\n', ' ')
    try:
        parts = shlex.split(cleaned)
    except ValueError:
        raise SetupError('無法解析 cURL 指令，請重新複製一次。') from None
    headers = {}
    i = 0
    while i < len(parts):
        part = parts[i]
        if part in ('-H', '--header') and i + 1 < len(parts):
            key, sep, value = parts[i + 1].partition(':')
            if sep:
                headers[key.strip()] = value.strip()
            i += 2
        elif part in ('-b', '--cookie') and i + 1 < len(parts):
            headers['cookie'] = parts[i + 1].strip()
            i += 2
        else:
            i += 1
    return headers


def parse_headers(text):
    if not text.strip():
        raise SetupError('剪貼簿是空的。請先從 YouTube Music 複製已登入的請求。')
    stripped = text.strip()
    # Chromium: Copy as fetch (Node.js). Parse only JSON; never execute clipboard code.
    match = re.search(r'[\"\']headers[\"\']\s*:\s*', text)
    if stripped.startswith('curl '):
        headers = parse_curl(stripped)
    elif match:
        try:
            headers, _ = json.JSONDecoder().raw_decode(text[match.end():])
        except (ValueError, TypeError):
            raise SetupError('無法解析。Chrome 請使用「Copy as cURL」或「Copy as fetch (Node.js)」，Firefox 請複製 Request Headers。') from None
    else:
        try:
            headers = json.loads(text)
        except ValueError:
            headers = {}
            for line in text.splitlines():
                key, sep, value = line.partition(':')
                if sep and key.strip() and ' ' not in key.strip():
                    headers[key.strip()] = value.strip()
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise SetupError('內容不是請求標頭，請重新複製。')
    headers = {k.lower(): v for k, v in headers.items()}
    cookie = headers.get('cookie', '')
    if not cookie:
        raise SetupError('複製的內容沒有 Cookie。請確認已登入 YouTube Music，並用「Copy as cURL」或「Copy as fetch (Node.js)」複製 music.youtube.com 的請求。')
    if not re.search(r'(?:^|;\s*)__Secure-3PAPISID=[^;\s]', cookie):  # the one cookie ytmusicapi signs requests with
        raise SetupError('Cookie 裡沒有登入資訊。請先在該瀏覽器登入 YouTube Music，再重新複製。')
    headers.setdefault('x-goog-authuser', '0')  # First Google account in the browser session.
    # Only authentication-related headers are retained, for the fixed YouTube Music service.
    allowed = {'cookie', 'x-goog-authuser', 'authorization', 'origin', 'x-origin', 'accept', 'content-type', 'user-agent'}
    return {k: v for k, v in headers.items() if k in allowed}


# Browsers whose cookie store browser_cookie3 can read, with the places their cookie files live.
# Detection only checks these paths; it never opens the files or the keychain.
BROWSERS = {
    'brave': ('Brave', {
        'darwin': ['~/Library/Application Support/BraveSoftware/Brave-Browser/Default/Cookies', '~/Library/Application Support/BraveSoftware/Brave-Browser/Profile */Cookies'],
        'linux': ['~/.config/BraveSoftware/Brave-Browser/Default/Cookies', '~/.config/BraveSoftware/Brave-Browser/Profile */Cookies'],
        'win32': ['%LOCALAPPDATA%/BraveSoftware/Brave-Browser/User Data/Default/Network/Cookies', '%LOCALAPPDATA%/BraveSoftware/Brave-Browser/User Data/Profile */Network/Cookies']}),
    'chrome': ('Chrome', {
        'darwin': ['~/Library/Application Support/Google/Chrome/Default/Cookies', '~/Library/Application Support/Google/Chrome/Profile */Cookies'],
        'linux': ['~/.config/google-chrome/Default/Cookies', '~/.config/google-chrome/Profile */Cookies'],
        'win32': ['%LOCALAPPDATA%/Google/Chrome/User Data/Default/Network/Cookies', '%LOCALAPPDATA%/Google/Chrome/User Data/Profile */Network/Cookies']}),
    'edge': ('Microsoft Edge', {
        'darwin': ['~/Library/Application Support/Microsoft Edge/Default/Cookies', '~/Library/Application Support/Microsoft Edge/Profile */Cookies'],
        'linux': ['~/.config/microsoft-edge/Default/Cookies', '~/.config/microsoft-edge/Profile */Cookies'],
        'win32': ['%LOCALAPPDATA%/Microsoft/Edge/User Data/Default/Network/Cookies', '%LOCALAPPDATA%/Microsoft/Edge/User Data/Profile */Network/Cookies']}),
    'chromium': ('Chromium', {
        'darwin': ['~/Library/Application Support/Chromium/Default/Cookies', '~/Library/Application Support/Chromium/Profile */Cookies'],
        'linux': ['~/.config/chromium/Default/Cookies', '~/.config/chromium/Profile */Cookies', '~/snap/chromium/common/chromium/Default/Cookies'],
        'win32': ['%LOCALAPPDATA%/Chromium/User Data/Default/Network/Cookies']}),
    'firefox': ('Firefox', {
        'darwin': ['~/Library/Application Support/Firefox/Profiles/*/cookies.sqlite'],
        'linux': ['~/.mozilla/firefox/*/cookies.sqlite', '~/snap/firefox/common/.mozilla/firefox/*/cookies.sqlite'],
        'win32': ['%APPDATA%/Mozilla/Firefox/Profiles/*/cookies.sqlite']}),
}
YOUTUBE_COOKIE_DOMAINS = {'.youtube.com', 'youtube.com', 'music.youtube.com', '.music.youtube.com'}


def available_browsers(platform=None, expand=os.path.expanduser):
    """Browsers that appear to have a profile on this machine, in display order."""
    platform = platform or ('linux' if sys.platform.startswith('linux') else sys.platform)
    found = []
    for key, (name, paths) in BROWSERS.items():
        for pattern in paths.get(platform, []):
            pattern = os.path.expandvars(expand(pattern))
            if glob.glob(pattern):
                found.append({'id': key, 'name': name})
                break
    return found


def cookie_header_from_browser(browser, loader=None):
    """Read the YouTube cookies of a local browser profile. May prompt for keychain access on macOS."""
    if browser not in BROWSERS:
        raise SetupError('不支援的瀏覽器。')
    name = BROWSERS[browser][0]
    if loader is None:
        import browser_cookie3
        loader = getattr(browser_cookie3, browser)
    try:
        jar = loader(domain_name='youtube.com')
        cookies = [c for c in jar if (c.domain or '').lower() in YOUTUBE_COOKIE_DOMAINS]
    except SetupError:
        raise
    except Exception:
        raise SetupError('無法讀取 ' + name + ' 的 Cookie。請確認已允許鑰匙圈存取、瀏覽器已安裝且登入過 YouTube Music；或改用下方貼上的方式。') from None
    if not cookies:
        raise SetupError(name + ' 裡沒有 YouTube 的 Cookie。請先在該瀏覽器登入 YouTube Music。')
    # Prefer the most specific domain when the same cookie name appears more than once.
    chosen = {}
    for cookie in sorted(cookies, key=lambda c: len(c.domain or '')):
        chosen[cookie.name] = cookie.value
    return '; '.join(f'{k}={v}' for k, v in chosen.items())


def import_from_browser(browser, folder=None, loader=None):
    header = cookie_header_from_browser(browser, loader)
    headers = parse_headers(json.dumps({'cookie': header, 'origin': 'https://music.youtube.com'}))
    return connect_headers(headers, folder)


def connect(text, folder=None):
    return connect_headers(parse_headers(text), folder)


def complete_headers(headers):
    """Add the standard client headers and the SAPISIDHASH that ytmusicapi uses to recognise browser auth."""
    from ytmusicapi.helpers import get_authorization, initialize_headers, sapisid_from_cookie
    full = {k.lower(): v for k, v in initialize_headers().items()}
    full.update(headers)
    full.setdefault('origin', 'https://music.youtube.com')
    full.pop('accept-encoding', None)
    try:
        sapisid = sapisid_from_cookie(full['cookie'])
    except Exception:
        raise SetupError('Cookie 裡沒有登入資訊。請先在該瀏覽器登入 YouTube Music，再重新複製。') from None
    full['authorization'] = get_authorization(sapisid + ' ' + full['origin'])
    return full


def connect_headers(headers, folder=None):
    from ytmusicapi import YTMusic
    import requests
    headers = complete_headers(headers)

    class TimedSession(requests.Session):
        def request(self, *args, **kwargs):
            if kwargs.get('timeout') is None:
                kwargs['timeout'] = (10, 30)
            return super().request(*args, **kwargs)

    with TimedSession() as session:
        try:
            account = YTMusic(headers, requests_session=session).get_account_info()
            name = account.get('accountName')
            if not name:
                raise SetupError('無法確認登入帳號，請重新複製已登入的請求。')
        except SetupError:
            raise
        except Exception:
            raise SetupError('登入驗證失敗。請確認網路正常、已登入 YouTube Music，並重新複製最新的請求。') from None
    folder = Path(folder) if folder else Path(__file__).resolve().parent / '.local'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    write_private(folder / 'browser.json', headers)
    write_private(folder / 'youtube_account.json', {'accountName': str(name)})
    return str(name)


def write_private(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file, ensure_ascii=False)
        temporary.chmod(0o600)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def main():
    print('正在驗證 YouTube Music 登入，請稍候…', flush=True)
    try:
        text = sys.stdin.read(2 * 1024 * 1024 + 1)
        if len(text) > 2 * 1024 * 1024:
            raise SetupError('複製內容太長，請只複製單一 browse 請求。')
        name = connect(text)
    except SetupError as exc:
        print(str(exc))
        return 1
    except Exception:
        print('無法儲存登入設定，請確認執行環境與資料夾權限。')
        return 1
    print('YouTube Music 已驗證並連接成功（帳號：' + name + '）！回到本機工具重新整理，就能開始轉移歌單。')
    print('登入資訊只保存在本機 .local/browser.json，請勿分享此檔案。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
