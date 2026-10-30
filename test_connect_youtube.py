import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from connect_youtube import SetupError, connect, parse_headers


class YouTubeConnectionTests(unittest.TestCase):
    def setUp(self):
        self.headers = {'cookie': 'SAPISID=fake-test-only', 'x-goog-authuser': '0', 'origin':'https://music.youtube.com'}

    def test_parse_node_fetch_without_executing_code(self):
        text = 'fetch("https://music.youtube.com/youtubei/v1/browse", ' + json.dumps({'headers':self.headers,'method':'POST'}) + '); throw new Error("never execute");'
        self.assertEqual(parse_headers(text), self.headers)

    def test_parse_raw_firefox_headers(self):
        text = 'POST /youtubei/v1/browse HTTP/2\nCookie: SAPISID=fake-test-only\nX-Goog-AuthUser: 0\nSec-Fetch-Site: same-origin'
        parsed = parse_headers(text)
        self.assertEqual(parsed['x-goog-authuser'], '0')
        self.assertNotIn('sec-fetch-site', parsed)

    def test_parse_chrome_copy_as_curl_with_continuations_and_cookie_flag(self):
        text = ("curl 'https://music.youtube.com/youtubei/v1/browse?prettyPrint=false' \\\n"
                "  -H 'accept: */*' \\\n"
                "  -b 'SAPISID=fake-test-only; HSID=x' \\\n"
                "  -H 'x-goog-authuser: 1' \\\n"
                "  -H 'sec-fetch-site: same-origin' \\\n"
                "  --data-raw '{\"context\":{}}'")
        parsed = parse_headers(text)
        self.assertEqual(parsed['cookie'], 'SAPISID=fake-test-only; HSID=x')
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
            self.assertEqual(json.loads(file.read_text()),self.headers)
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


if __name__ == '__main__':
    unittest.main()
