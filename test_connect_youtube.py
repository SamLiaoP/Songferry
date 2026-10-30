import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from http.cookiejar import Cookie, CookieJar
from connect_youtube import SetupError, available_browsers, connect, cookie_header_from_browser, import_from_browser, parse_headers


def make_cookie(name, value, domain):
    return Cookie(0, name, value, None, False, domain, True, domain.startswith('.'), '/', True, True, None, False, None, None, {})


class YouTubeConnectionTests(unittest.TestCase):
    def setUp(self):
        self.headers = {'cookie': '__Secure-3PAPISID=fake-test-only', 'x-goog-authuser': '0', 'origin':'https://music.youtube.com'}

    def test_parse_node_fetch_without_executing_code(self):
        text = 'fetch("https://music.youtube.com/youtubei/v1/browse", ' + json.dumps({'headers':self.headers,'method':'POST'}) + '); throw new Error("never execute");'
        self.assertEqual(parse_headers(text), self.headers)

    def test_parse_raw_firefox_headers(self):
        text = 'POST /youtubei/v1/browse HTTP/2\nCookie: __Secure-3PAPISID=fake-test-only\nX-Goog-AuthUser: 0\nSec-Fetch-Site: same-origin'
        parsed = parse_headers(text)
        self.assertEqual(parsed['x-goog-authuser'], '0')
        self.assertNotIn('sec-fetch-site', parsed)

    def test_parse_chrome_copy_as_curl_with_continuations_and_cookie_flag(self):
        text = ("curl 'https://music.youtube.com/youtubei/v1/browse?prettyPrint=false' \\\n"
                "  -H 'accept: */*' \\\n"
                "  -b '__Secure-3PAPISID=fake-test-only; HSID=x' \\\n"
                "  -H 'x-goog-authuser: 1' \\\n"
                "  -H 'sec-fetch-site: same-origin' \\\n"
                "  --data-raw '{\"context\":{}}'")
        parsed = parse_headers(text)
        self.assertEqual(parsed['cookie'], '__Secure-3PAPISID=fake-test-only; HSID=x')
        self.assertEqual(parsed['x-goog-authuser'], '1')
        self.assertNotIn('sec-fetch-site', parsed)
        self.assertNotIn('--data-raw', str(parsed))

    def test_any_signed_in_request_works_and_authuser_defaults_to_first_account(self):
        text = "curl 'https://music.youtube.com/' -H 'cookie: __Secure-3PAPISID=fake-test-only' -H 'accept-language: en'"
        parsed = parse_headers(text)
        self.assertEqual(parsed['x-goog-authuser'], '0')
        self.assertNotIn('accept-language', parsed)

    def test_signed_out_cookie_is_rejected_with_a_clear_message(self):
        with self.assertRaises(SetupError) as caught:
            parse_headers("curl 'https://music.youtube.com/' -H 'cookie: VISITOR_INFO1_LIVE=anonymous; YSC=x'")
        self.assertIn('登入', str(caught.exception))
        self.assertNotIn('anonymous', str(caught.exception))

    def test_missing_cookie_is_actionable_and_does_not_echo_headers(self):
        with self.assertRaises(SetupError) as caught:
            parse_headers(json.dumps({'authorization':'sensitive-test-value'}))
        self.assertNotIn('sensitive-test-value', str(caught.exception))
        self.assertIn('Cookie', str(caught.exception))

    def test_malformed_fetch_does_not_execute_or_expose_text(self):
        with self.assertRaises(SetupError):
            parse_headers('fetch("https://example.com", {"headers": secret_function()})')

    def test_success_verifies_before_saving_with_private_permissions(self):
        fake = Mock()
        fake.get_account_info.return_value = {'accountName':'Test'}
        with tempfile.TemporaryDirectory() as temp, patch('ytmusicapi.YTMusic', return_value=fake):
            folder = Path(temp)/'.local'
            self.assertEqual(connect(json.dumps(self.headers), folder), 'Test')
            file = folder/'browser.json'
            saved = json.loads(file.read_text())
            self.assertEqual({k: saved[k] for k in self.headers}, self.headers)
            self.assertTrue(saved['authorization'].startswith('SAPISIDHASH '), 'ytmusicapi only treats headers with SAPISIDHASH as browser auth')
            self.assertIn('user-agent', saved)
            self.assertEqual(json.loads((folder/'youtube_account.json').read_text()), {'accountName': 'Test'})
            self.assertEqual(stat.S_IMODE((folder/'youtube_account.json').stat().st_mode),0o600)
            self.assertEqual(stat.S_IMODE(file.stat().st_mode),0o600)
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode),0o700)
        fake.get_account_info.assert_called_once_with()
        fake.create_playlist.assert_not_called()

    def test_failed_validation_keeps_existing_login_and_hides_exception(self):
        fake = Mock()
        fake.get_account_info.side_effect = Exception('private-cookie-do-not-print')
        with tempfile.TemporaryDirectory() as temp, patch('ytmusicapi.YTMusic', return_value=fake):
            file = Path(temp)/'browser.json'
            file.write_text('existing-login')
            with self.assertRaises(SetupError) as caught:
                connect(json.dumps(self.headers), temp)
            self.assertEqual(file.read_text(),'existing-login')
            self.assertNotIn('private-cookie',str(caught.exception))


class BrowserImportTests(unittest.TestCase):
    def test_detection_only_looks_at_profile_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home/'Library/Application Support/BraveSoftware/Brave-Browser/Default').mkdir(parents=True)
            (home/'Library/Application Support/BraveSoftware/Brave-Browser/Default/Cookies').write_bytes(b'')
            (home/'Library/Application Support/Firefox/Profiles/abc.default-release').mkdir(parents=True)
            (home/'Library/Application Support/Firefox/Profiles/abc.default-release/cookies.sqlite').write_bytes(b'')
            expand = lambda p: p.replace('~', str(home))
            self.assertEqual(available_browsers('darwin', expand), [{'id':'brave','name':'Brave'},{'id':'firefox','name':'Firefox'}])
            self.assertEqual(available_browsers('linux', expand), [])

    def test_cookie_header_keeps_only_youtube_cookies_and_prefers_specific_domain(self):
        jar = CookieJar()
        for c in [make_cookie('SAPISID','fake-test-only','.youtube.com'), make_cookie('SID','sid','.youtube.com'),
                  make_cookie('SAPISID','other','.google.com'), make_cookie('PREF','p','music.youtube.com'), make_cookie('X','x','.youtube.com.evil.test')]:
            jar.set_cookie(c)
        header = cookie_header_from_browser('brave', loader=lambda domain_name: jar)
        self.assertEqual(header, 'SAPISID=fake-test-only; SID=sid; PREF=p')

    def test_signed_out_browser_and_unreadable_store_give_clear_errors(self):
        with self.assertRaisesRegex(SetupError, 'Brave 裡沒有 YouTube 的 Cookie'):
            cookie_header_from_browser('brave', loader=lambda domain_name: CookieJar())
        def broken(domain_name):
            raise RuntimeError('keychain secret-looking-text')
        with self.assertRaises(SetupError) as caught:
            cookie_header_from_browser('firefox', loader=broken)
        self.assertIn('Firefox', str(caught.exception)); self.assertNotIn('secret-looking', str(caught.exception))
        with self.assertRaises(SetupError):
            cookie_header_from_browser('safari', loader=lambda domain_name: CookieJar())

    def test_import_verifies_and_saves_like_a_pasted_login(self):
        jar = CookieJar(); jar.set_cookie(make_cookie('__Secure-3PAPISID','fake-test-only','.youtube.com'))
        fake = Mock(); fake.get_account_info.return_value = {'accountName':'Imported'}
        with tempfile.TemporaryDirectory() as temp, patch('ytmusicapi.YTMusic', return_value=fake):
            folder = Path(temp)/'.local'
            self.assertEqual(import_from_browser('chrome', folder, loader=lambda domain_name: jar), 'Imported')
            saved = json.loads((folder/'browser.json').read_text())
        self.assertEqual(saved['cookie'], '__Secure-3PAPISID=fake-test-only')
        self.assertEqual(saved['x-goog-authuser'], '0')
        self.assertEqual(saved['origin'], 'https://music.youtube.com')
        with self.assertRaises(SetupError), tempfile.TemporaryDirectory() as temp:
            import_from_browser('chrome', temp, loader=lambda domain_name: CookieJar())


if __name__ == '__main__':
    unittest.main()
