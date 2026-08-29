"""Krillion daily leaderboard: share parsing, ranking and one-day JSON storage.

Kept free of any discord imports so the logic can be exercised without a bot
connection. bot.py wraps the rendered text in an embed.
"""

import json
import os
import re
import tempfile
import time
import unicodedata
from datetime import datetime, timedelta

import pytz

MOUNTAIN = pytz.timezone('America/Denver')

# Scores are wiped daily at 10:00 PM Mountain, so a "Krillion day" runs 22:00 -> 22:00.
RESET_HOUR = 22

MAX_SCORE = 700
RESULT_COUNT = 7

STORE_VERSION = 1
DEFAULT_DATA_FILE = '/data/krillion.json'

# "Krillion #45 🦐"
_HEADER_RE = re.compile(r'^krillion\s*#\s*(\d{1,5})\s*\U0001F990$')
_SCORE_RE = re.compile(r'^(\d{1,4})$')

_ZWJ = 0x200D
_KEYCAP = 0x20E3
_VARIATION_SELECTORS = (0xFE0E, 0xFE0F)
_SKIN_TONES = (0x1F3FB, 0x1F3FF)
_REGIONAL_INDICATORS = (0x1F1E6, 0x1F1FF)
_TAGS = (0xE0020, 0xE007F)


def _in(codepoint, bounds):
    return bounds[0] <= codepoint <= bounds[1]


def split_emoji_clusters(text):
    """Split a run of emoji into user-perceived characters.

    Python 3.7 has no grapheme segmentation in the stdlib, so this joins the
    sequences that actually show up in shared results: ZWJ sequences, variation
    selectors, skin tone modifiers, keycaps, flags and tag sequences.
    """
    clusters = []
    current = ''
    pending_join = False

    for char in text:
        codepoint = ord(char)

        if not current:
            current = char
            continue

        if codepoint == _ZWJ:
            current += char
            pending_join = True
        elif pending_join:
            current += char
            pending_join = False
        elif (codepoint in _VARIATION_SELECTORS
                or codepoint == _KEYCAP
                or _in(codepoint, _SKIN_TONES)
                or _in(codepoint, _TAGS)
                or unicodedata.combining(char)):
            current += char
        elif _in(codepoint, _REGIONAL_INDICATORS) and len(current) == 1 and _in(ord(current), _REGIONAL_INDICATORS):
            current += char
        else:
            clusters.append(current)
            current = char

    if current:
        clusters.append(current)

    return clusters


def parse_krillion_message(content):
    """Return a dict for a well-formed Krillion share, otherwise None.

    Expects the four-part share exactly as the game produces it: header line,
    score line, blank line, then the seven result emoji.
    """
    if not content or '\U0001F990' not in content:
        return None

    lines = [line.strip() for line in content.replace('\r\n', '\n').split('\n')]

    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()

    if len(lines) != 4:
        return None

    header, raw_score, separator, results = lines

    if separator:
        return None

    header_match = _HEADER_RE.match(header.lower())
    if not header_match:
        return None

    score_match = _SCORE_RE.match(raw_score)
    if not score_match:
        return None

    score = int(score_match.group(1))
    if score > MAX_SCORE:
        return None

    # Any ASCII in the results line means it is prose, not a shared result.
    if any(ord(char) < 128 for char in results):
        return None

    clusters = split_emoji_clusters(results)
    if len(clusters) != RESULT_COUNT:
        return None

    return {
        'puzzle': int(header_match.group(1)),
        'score': score,
        'emojis': results,
    }


def current_window_key(now=None):
    """Identify the active retention window by the local date it started on."""
    if now is None:
        now = datetime.now(pytz.utc)
    elif now.tzinfo is None:
        now = pytz.utc.localize(now)

    local = now.astimezone(MOUNTAIN)
    start = local.date()
    if local.hour < RESET_HOUR:
        start -= timedelta(days=1)

    return start.isoformat()


def rank_entries(entries):
    """Sort highest score first and assign competition-style ranks."""
    ordered = sorted(entries, key=lambda entry: (-entry['score'], entry.get('submitted_at', 0)))

    ranked = []
    previous_score = None
    rank = 0
    for position, entry in enumerate(ordered, start=1):
        if entry['score'] != previous_score:
            rank = position
            previous_score = entry['score']
        ranked.append((rank, entry))

    return ranked


_MEDALS = {1: '\U0001F947', 2: '\U0001F948', 3: '\U0001F949'}
_MARKDOWN = re.compile(r'([\\*_~`|>])')

# Embed descriptions allow 4096 characters; stop short so the overflow note fits.
_DESCRIPTION_BUDGET = 3800


def escape_markdown(text):
    return _MARKDOWN.sub(r'\\\1', text)


def build_leaderboard_content(puzzle, entries):
    """Render the leaderboard as (title, description, footer)."""
    title = '\U0001F990 Krillion #{} Leaderboard'.format(puzzle)
    footer = 'Resets daily at 10:00 PM Mountain Time'

    if not entries:
        return title, 'No scores yet today.', footer

    ranked = rank_entries(entries)

    blocks = []
    used = 0
    hidden = 0
    for rank, entry in ranked:
        marker = _MEDALS.get(rank, '{}.'.format(rank))
        block = '{} **{}** \u2014 {}\n{}'.format(
            marker,
            escape_markdown(entry['name']),
            entry['score'],
            entry['emojis'],
        )
        if hidden or used + len(block) > _DESCRIPTION_BUDGET:
            hidden += 1
            continue
        blocks.append(block)
        used += len(block) + 1

    if hidden:
        blocks.append('\u2026and {} more'.format(hidden))

    return title, '\n'.join(blocks), footer


class KrillionStore(object):
    """One JSON document holding today's scores and the boards showing them."""

    def __init__(self, path=None):
        self.path = path or os.getenv('KRILLION_DATA_FILE', DEFAULT_DATA_FILE)
        self.data = self._empty()
        self.persistent = True

    @staticmethod
    def _empty(window=None):
        return {
            'version': STORE_VERSION,
            'window': window or current_window_key(),
            'guilds': {},
        }

    def load(self):
        try:
            with open(self.path, 'r') as handle:
                data = json.load(handle)
        except IOError:
            self.data = self._empty()
            return self.data
        except ValueError as error:
            backup = '{}.corrupt-{}'.format(self.path, int(time.time()))
            print('Krillion: unreadable store ({}); preserving it at {}'.format(error, backup))
            try:
                os.rename(self.path, backup)
            except OSError as rename_error:
                print('Krillion: could not preserve the unreadable store: {}'.format(rename_error))
            self.data = self._empty()
            return self.data

        if not isinstance(data, dict) or data.get('version') != STORE_VERSION:
            print('Krillion: ignoring store with unexpected shape or version')
            self.data = self._empty()
            return self.data

        data.setdefault('guilds', {})
        data.setdefault('window', current_window_key())
        self.data = data
        return self.data

    def save(self):
        directory = os.path.dirname(os.path.abspath(self.path))
        handle = None
        temp_path = None
        try:
            if directory and not os.path.isdir(directory):
                os.makedirs(directory)
            # Write beside the target and swap it in, so a crash mid-write cannot
            # leave a half-written leaderboard behind.
            handle, temp_path = tempfile.mkstemp(dir=directory, prefix='.krillion-', suffix='.json')
            with os.fdopen(handle, 'w') as stream:
                handle = None
                json.dump(self.data, stream)
            os.replace(temp_path, self.path)
            temp_path = None
            self.persistent = True
        except (IOError, OSError) as error:
            if self.persistent:
                print('Krillion: could not write {} ({}); keeping scores in memory only'.format(self.path, error))
            self.persistent = False
        finally:
            if handle is not None:
                os.close(handle)
            if temp_path is not None and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def run_retention(self, now=None):
        """Drop the scores from earlier windows and return whether anything reset.

        Boards from previous days are forgotten rather than deleted, so the last
        leaderboard of each day stays in the channel as a record of the scores.
        """
        window = current_window_key(now)
        if self.data.get('window') == window:
            return False

        self.data = self._empty(window)
        self.save()
        return True

    def _puzzle_bucket(self, guild_id, puzzle, create=False):
        guilds = self.data.setdefault('guilds', {})
        if create:
            guild = guilds.setdefault(str(guild_id), {})
            puzzles = guild.setdefault('puzzles', {})
            return puzzles.setdefault(str(puzzle), {'scores': {}, 'board': None})

        guild = guilds.get(str(guild_id))
        if not guild:
            return None
        return guild.get('puzzles', {}).get(str(puzzle))

    def record_score(self, guild_id, user_id, name, puzzle, score, emojis, submitted_at=None):
        """Store one score per user per puzzle; a repost replaces the old one."""
        bucket = self._puzzle_bucket(guild_id, puzzle, create=True)
        existing = bucket['scores'].get(str(user_id))

        bucket['scores'][str(user_id)] = {
            'name': name,
            'score': score,
            'emojis': emojis,
            # Keep the original time so a correction does not lose tie-break order.
            'submitted_at': existing['submitted_at'] if existing else (submitted_at or time.time()),
        }
        self.save()

    def get_entries(self, guild_id, puzzle):
        bucket = self._puzzle_bucket(guild_id, puzzle)
        if not bucket:
            return []
        return list(bucket.get('scores', {}).values())

    def get_board(self, guild_id, puzzle):
        bucket = self._puzzle_bucket(guild_id, puzzle)
        if not bucket:
            return None
        return bucket.get('board')

    def set_board(self, guild_id, puzzle, channel_id, message_id):
        bucket = self._puzzle_bucket(guild_id, puzzle, create=True)
        bucket['board'] = {'channel_id': channel_id, 'message_id': message_id}
        self.save()

    def clear_board(self, guild_id, puzzle):
        bucket = self._puzzle_bucket(guild_id, puzzle)
        if bucket:
            bucket['board'] = None
            self.save()
