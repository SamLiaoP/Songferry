"""Read locally copied request headers, verify YT Music, and save privately.
Never prints request headers, cookies, tokens, or service exception details.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path


class SetupError(ValueError):
    pass


def parse_headers(text):
    if not text.strip():
        raise SetupError('剪貼簿是空的。請先從 YouTube Music 複製已登入請求的標頭。')
    # Chromium: Copy as fetch (Node.js). Parse only JSON; never execute clipboard code.
    match = re.search(r'[\"\']headers[\"\']\s*:\s*', text)
    if match:
        try:
            headers, _ = json.JSONDecoder().raw_decode(text[match.end():])
        except (ValueError, TypeError):
            raise SetupError('無法解析。Chrome 請使用「Copy as fetch (Node.js)」，Firefox 請複製 Request Headers。') from None
    else:
        try:
            headers = json.loads(text)
        except ValueError:
            headers = {}
            for line in text.splitlines():
                key, sep, value = line.partition(':')
                if sep and key.strip():
                    headers[key.strip()] = value.strip()
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise SetupError('內容不是請求標頭，請重新複製。')
    headers = {k.lower(): v for k, v in headers.items()}
    if not headers.get('cookie') or not headers.get('x-goog-authuser'):
        raise SetupError('缺少 Cookie 或 X-Goog-AuthUser。請確認已登入 YouTube Music，選擇 browse 請求；Chrome 要用 Copy as fetch (Node.js)。')
    # Only authentication-related headers are retained, for the fixed YouTube Music service.
    allowed = {'cookie', 'x-goog-authuser', 'authorization', 'origin', 'x-origin', 'accept', 'content-type', 'user-agent'}
    return {k: v for k, v in headers.items() if k in allowed}


def connect(text, folder=None):
    from ytmusicapi import YTMusic
    import requests
    headers = parse_headers(text)

    class TimedSession(requests.Session):
        def request(self, *args, **kwargs):
            if kwargs.get('timeout') is None:
                kwargs['timeout'] = (10, 30)
            return super().request(*args, **kwargs)

    with TimedSession() as session:
        try:
            account = YTMusic(headers, requests_session=session).get_account_info()
            if not account.get('accountName'):
                raise SetupError('無法確認登入帳號，請重新複製已登入請求的標頭。')
        except SetupError:
            raise
        except Exception:
            raise SetupError('登入驗證失敗。請確認網路正常、已登入 YouTube Music，並重新複製最新的 browse 請求。') from None
    folder = Path(folder) if folder else Path(__file__).resolve().parent / '.local'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder, delete=False) as file:
            temporary = Path(file.name)
            json.dump(headers, file)
        os.replace(temporary, folder / 'browser.json')
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def main():
    print('正在驗證 YouTube Music 登入，請稍候…', flush=True)
    try:
        text = sys.stdin.read(2 * 1024 * 1024 + 1)
        if len(text) > 2 * 1024 * 1024:
            raise SetupError('複製內容太長，請只複製單一 browse 請求。')
        connect(text)
    except SetupError as exc:
        print(str(exc))
        return 1
    except Exception:
        print('無法儲存登入設定，請確認執行環境與資料夾權限。')
        return 1
    print('YouTube Music 已驗證並連接成功！回到本機工具重新整理，就能開始轉移歌單。')
    print('登入資訊只保存在本機 .local/browser.json，請勿分享此檔案。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
