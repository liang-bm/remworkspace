"""Local, explainable fuzzy journal lookup, without indexing user data online.

Exact phrase/terms rank before compact subsequences and a single spelling edit.
Fuzziness never means returning arbitrary unrelated records. Results retain
original body offsets, not positions in a normalized display string.
"""
from dataclasses import dataclass, replace
import re
import unicodedata
from .diary import DiaryError

def normalized(value):
    return ''.join(unicodedata.normalize('NFKC', char).casefold() for char in value)

def _one_edit(left, right):
    if left == right: return True
    if abs(len(left) - len(right)) > 1: return False
    if len(left) == len(right):
        different = [i for i, pair in enumerate(zip(left, right)) if pair[0] != pair[1]]
        return len(different) == 1 or (len(different) == 2 and different[1] == different[0] + 1
            and left[different[0]] == right[different[1]] and left[different[1]] == right[different[0]])
    if len(left) > len(right): left, right = right, left
    position = next((i for i in range(len(left)) if left[i] != right[i]), len(left))
    return left[position:] == right[position + 1:]

def _term_match(term, value, cancelled):
    start = value.find(term)
    if start >= 0: return 0, start, start + len(term)
    if 3 <= len(term) <= 64:
        # A compact in-order fragment: each missing section is at most one
        # character, not a match spread arbitrarily across a whole diary.
        start = value.find(term[0]); attempts = 0
        while start >= 0 and attempts < 512:
            if cancelled(): raise DiaryError('日记搜索已停止')
            position = start
            for char in term[1:]:
                position = value.find(char, position + 1, position + 3)
                if position < 0: break
            if position >= 0: return 1, start, position + 1
            start = value.find(term[0], start + 1); attempts += 1
    if 4 <= len(term) <= 64:
        # One typo (including a transposed pair). Short two-letter/Chinese
        # queries stay literal to avoid pages of false positive matches.
        for word in re.finditer(r'[^\W_]+', value):
            if cancelled(): raise DiaryError('日记搜索已停止')
            candidate = word.group()
            if _one_edit(term, candidate): return 2, word.start(), word.end()
    return None

def original_range(text, start, end):
    position = 0; first = None
    for index, char in enumerate(text):
        following = position + len(unicodedata.normalize('NFKC', char).casefold())
        if first is None and following > start: first = index
        if following >= end: return (first if first is not None else index), index + 1
        position = following
    return max(0, len(text) - 1), len(text)

@dataclass(frozen=True)
class SearchHit:
    entry: object
    snippet: str
    snippet_ranges: tuple
    body_ranges: tuple
    rank: tuple

def match_entry(entry, query, *, cancelled=lambda: False):
    query = normalized(query.strip())
    terms = tuple(dict.fromkeys(query.split()))[:8]
    if not terms: return None
    fields = (normalized(entry.title), normalized(' '.join(entry.tags)), normalized(entry.day), normalized(entry.body))
    body_matches = []; kinds = []; owners = []
    for term in terms:
        found = None
        # Rank lexical precision before field priority: an exact body phrase
        # beats a coincidental fuzzy approximation in the title.
        for owner, field in enumerate(fields):
            match = _term_match(term, field, cancelled)
            if match and owner==3:body_matches.append(original_range(entry.body,match[1],match[2]))
            if match and (found is None or (match[0], owner) < (found[0], found[1])):
                found = (match[0], owner, match[1], match[2])
        if found is None: return None
        kind, owner, start, end = found; kinds.append(kind); owners.append(owner)
    body_matches = tuple(sorted(set(body_matches)))
    first = body_matches[0][0] if body_matches else 0
    begin = max(0, first - 38); finish = min(len(entry.body), max(first + 100, begin + 160))
    snippet = entry.body[begin:finish].replace('\n', ' ').replace('\r', ' ')
    ranges = tuple((max(0, a - begin), min(len(snippet), b - begin)) for a, b in body_matches if b > begin and a < finish)
    if begin: snippet = '…' + snippet; ranges = tuple((a + 1, b + 1) for a, b in ranges)
    if finish < len(entry.body): snippet += '…'
    phrase = 0 if query in fields[0] else 1 if any(query in field for field in fields) else 2
    rank = (max(kinds), phrase, max(owners), sum(kinds))
    return SearchHit(replace(entry, body='', rich=''), snippet, ranges, body_matches, rank)

def find_entries(entries, query, *, limit=200, cancelled=lambda: False):
    if len(query) > 240: raise DiaryError('搜索词最多 240 字')
    hits = []
    for entry in entries:
        if cancelled(): raise DiaryError('日记搜索已停止')
        hit = match_entry(entry, query, cancelled=cancelled)
        if hit is not None:
            hits.append(hit)
            if len(hits) > limit * 2:
                hits.sort(key=lambda value: value.entry.updated_at, reverse=True)
                hits.sort(key=lambda value: value.rank); del hits[limit:]
    hits.sort(key=lambda value: (value.entry.day, value.entry.updated_at), reverse=True)
    hits.sort(key=lambda value: value.rank)
    return hits[:limit]
