"""Every Chinese string the server can return, and every string used by the UI, has an English translation."""
import json
import re
import unittest
from pathlib import Path
import app as module

ROOT = Path(__file__).resolve().parent


class TranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = json.loads((ROOT / 'i18n' / 'en.json').read_text(encoding='utf-8'))
        cls.strings = data['strings']
        cls.patterns = [(re.compile(p), r) for p, r in data['patterns']]

    def covered(self, text):
        return text in self.strings or any(p.search(text) for p, _ in self.patterns)

    def chinese_literals(self, source):
        return {m for m in re.findall(r"'([^'\\]*[\u4e00-\u9fff][^'\\]*)'", source)}

    def test_server_messages_are_translated(self):
        skip = {'由 Songferry 從 Spotify 轉移。', '由 Songferry 從 YouTube Music 轉移。', '不支援的語言。'}
        for name in ('app.py', 'reverse.py', 'matching.py'):
            source = (ROOT / name).read_text(encoding='utf-8')
            for literal in self.chinese_literals(source):
                if literal in skip or '\\b' in literal or '{' in literal:
                    continue
                if literal.startswith(' ') or literal.endswith(' ') or literal.endswith('（Retry-After: ') or literal == '建立結果未確認。請先到 ':
                    continue  # fragments of messages that patterns cover whole
                self.assertTrue(self.covered(literal), f'{name}: no English for {literal!r}')
        for text in ['Spotify 請求過多，請稍後重試（Retry-After: 12 秒）。', 'Spotify 讀取失敗（HTTP 500）。',
                     '建立結果未確認。請先到 YouTube Music 查看是否已有歌單；此工作不會自動重試，以免重複建立。',
                     'YouTube Music 標示 30 首，但只讀到 29 首。請改用一般歌單或稍後重試，以免轉移不完整。']:
            self.assertTrue(self.covered(text), text)

    def test_ui_strings_are_translated(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        for text in re.findall(r'data-i18n>([^<]+)<', html):
            self.assertIn(text.strip(), self.strings, f'index.html: {text!r}')
        for text in re.findall(r'placeholder="([^"]*[\u4e00-\u9fff][^"]*)"', html):
            self.assertIn(text, self.strings, f'placeholder: {text!r}')
        js = (ROOT / 'app.js').read_text(encoding='utf-8')
        for text in re.findall(r"(?<![\w$.])t\('([^']+)'", js):
            self.assertIn(text, self.strings, f'app.js t(): {text!r}')
        for text in re.findall(r"(?:notice|spotifyMessage|showSpotifySetup)\('([^']+)'", js):
            self.assertIn(text, self.strings, f'app.js notice: {text!r}')

    def test_placeholders_match_between_languages(self):
        for zh, en in self.strings.items():
            self.assertEqual(set(re.findall(r'\{\w+\}', zh)), set(re.findall(r'\{\w+\}', en)), zh)

    def test_translation_file_is_served_only_for_known_languages(self):
        client = module.app.test_client()
        response = client.get('/i18n/en.json', base_url=module.BASE)
        self.assertEqual(response.status_code, 200)
        self.assertIn('strings', response.json)
        self.assertEqual(client.get('/i18n/zz.json', base_url=module.BASE).status_code, 400)
        self.assertEqual(client.get('/i18n/..%2Fapp.json', base_url=module.BASE).status_code, 404)


if __name__ == '__main__':
    unittest.main()
