"""Conservative, explainable song matching; scores are heuristics, not probabilities."""
import re
import unicodedata
from difflib import SequenceMatcher


def normalize(value):
    value = unicodedata.normalize('NFKC', value or '').casefold()
    return ''.join(c for c in value if c.isalnum())


def versions(title):
    title = unicodedata.normalize('NFKC', title or '').casefold()
    markers = {
        'live': r'\blive\b|現場|演唱會',
        'remix': r'\bremix\b|混音',
        'cover': r'\bcover\b|翻唱',
        'instrumental': r'\binstrumental\b|伴奏|純音樂',
        'acoustic': r'\bacoustic\b|不插電',
        'sped': r'\bsped\b|\bslowed\b|加速|慢速',
    }
    return {key for key, pattern in markers.items() if re.search(pattern, title)}


def similarity(a, b):
    a, b = normalize(a), normalize(b)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0


def rank(track, results):
    ranked = []
    for result in results:
        if not result.get('videoId') or result.get('isAvailable') is False:
            continue
        artists = [a.get('name', '') for a in result.get('artists', [])]
        title_score = similarity(track['title'], result.get('title', ''))
        artist_score = max((similarity(a, b) for a in track['artists'] for b in artists), default=0)
        duration = result.get('duration_seconds')
        if duration is None and result.get('duration'):
            try:
                duration = 0
                for part in result['duration'].split(':'):
                    duration = duration * 60 + int(part)
            except (ValueError, AttributeError):
                duration = None
        delta = abs(track['duration'] - duration) if track.get('duration') and duration else None
        duration_score = max(0, 1 - delta / 25) if delta is not None else .5
        score = .55 * title_score + .35 * artist_score + .1 * duration_score
        warnings = []
        if versions(track['title']) != versions(result.get('title')):
            warnings.append('歌曲版本可能不同')
            score -= .25
        if delta is not None and delta > 15:
            warnings.append('長度差超過 15 秒')
        if artist_score < .75:
            warnings.append('歌手名稱不一致')
        if title_score < .8:
            warnings.append('歌名相似度較低')
        if delta is None:
            warnings.append('缺少長度資料')
        ranked.append(dict(videoId=result['videoId'], title=result.get('title', ''),
                           artists=artists, duration=duration, score=round(max(0, score) * 100),
                           warnings=warnings))
    ranked.sort(key=lambda x: x['score'], reverse=True)
    seen = set()
    unique = []
    for candidate in ranked:
        if candidate['videoId'] not in seen:
            seen.add(candidate['videoId'])
            unique.append(candidate)
    return unique[:5]
