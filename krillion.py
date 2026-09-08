"""Krillion leaderboards: share parsing, ranking, one-day JSON storage and the
weekly rollup built by reading past leaderboards back out of the channel.

Kept free of any discord imports so the logic can be exercised without a bot
connection. bot.py wraps the rendered text in an embed.
"""

import json
import os
import re
import shutil
import tempfile
import time
import unicodedata
from datetime import datetime, timedelta

import pytz

MOUNTAIN = pytz.timezone('America/Denver')

# Scores are wiped daily at 10:00 PM Mountain, so a "Krillion day" runs 22:00 -> 22:00.
RESET_HOUR = 22

# The weekly rollup covers the seven days closing on Monday at the same hour.
WEEKLY_WEEKDAY = 0

MAX_SCORE = 700
RESULT_COUNT = 7

STORE_VERSION = 1
DEFAULT_DATA_FILE = '/data/krillion.json'


def _env_number(name, fallback, cast):
    raw = os.getenv(name)
    if not raw:
        return fallback
    try:
        value = cast(raw)
    except ValueError:
        return fallback
    return value if value >= 0 else fallback


# How many imaginary average games everyone starts the week with. Raising it makes
# a short week of play count for less; it is measured in games, so 5 against a
# seven-day week means you have to play most days to fully claim your average.
WEEKLY_PRIOR = _env_number('KRILLION_WEEKLY_PRIOR', 5.0, float)

# Below this many plays a person is listed but not ranked.
WEEKLY_MIN_PLAYS = _env_number('KRILLION_WEEKLY_MIN_PLAYS', 2, int)

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


def leading_emoji_run(line):
    """Return the run of result emoji starting a line, or None if it is not one.

    Stops at the first ordinary character so a comment written next to the results
    is ignored, while a run that is not exactly seven emoji is still rejected.
    """
    end = 0
    for char in line:
        if ord(char) < 128 or char.isspace():
            break
        end += 1

    run = line[:end]
    if not run or len(split_emoji_clusters(run)) != RESULT_COUNT:
        return None

    return run


def parse_krillion_message(content):
    """Return a dict for a well-formed Krillion share, otherwise None.

    The share has to open with the four parts the game produces: header line,
    score line, blank line, then the seven result emoji. Anything after those is
    treated as the poster's own commentary and ignored.
    """
    if not content or '\U0001F990' not in content:
        return None

    lines = [line.strip() for line in content.replace('\r\n', '\n').split('\n')]

    while lines and not lines[0]:
        lines.pop(0)

    if len(lines) < 4:
        return None

    header, raw_score, separator, results = lines[:4]

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

    emojis = leading_emoji_run(results)
    if emojis is None:
        return None

    return {
        'puzzle': int(header_match.group(1)),
        'score': score,
        'emojis': emojis,
    }


def _local(now=None):
    if now is None:
        now = datetime.now(pytz.utc)
    elif now.tzinfo is None:
        now = pytz.utc.localize(now)

    return now.astimezone(MOUNTAIN)


def _mountain_moment(day, hour):
    """Build an aware Mountain timestamp, letting pytz pick the DST offset.

    Going through localize rather than replace keeps both ends of a week that
    straddles a clock change pinned to 10:00 PM local.
    """
    return MOUNTAIN.localize(datetime(day.year, day.month, day.day, hour))


def current_window_key(now=None):
    """Identify the active retention window by the local date it started on."""
    local = _local(now)
    start = local.date()
    if local.hour < RESET_HOUR:
        start -= timedelta(days=1)

    return start.isoformat()


def weekly_window_key(now=None):
    """Name the most recent completed week by the Monday its 10:00 PM close fell on."""
    local = _local(now)
    day = local.date()

    day -= timedelta(days=(day.weekday() - WEEKLY_WEEKDAY) % 7)
    if day == local.date() and local.hour < RESET_HOUR:
        day -= timedelta(days=7)

    return day.isoformat()


def shift_week(key, weeks_back):
    day = datetime.strptime(key, '%Y-%m-%d').date() - timedelta(days=7 * weeks_back)
    return day.isoformat()


def week_bounds(key):
    """Return the UTC start and end of a weekly key, plus a label for the embed."""
    closes_on = datetime.strptime(key, '%Y-%m-%d').date()

    end = _mountain_moment(closes_on, RESET_HOUR)
    start = _mountain_moment(closes_on - timedelta(days=7), RESET_HOUR)
    label = 'week ending {} {}'.format(end.strftime('%a, %b'), end.day)

    return start.astimezone(pytz.utc), end.astimezone(pytz.utc), label


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


# The daily board is the only record of a day's scores that survives: the share it
# came from is deleted once counted, and the store is wiped at 10:00 PM. These read
# build_leaderboard_content's output back, so the two have to stay in step.
_BOARD_TITLE_RE = re.compile(r'^\U0001F990 Krillion #(\d{1,5}) Leaderboard$')
_BOARD_ENTRY_RE = re.compile(
    r'^(?:\U0001F947|\U0001F948|\U0001F949|\d{1,3}\.)\s+\*\*(.+)\*\*\s+\u2014\s+(\d{1,4})$'
)
_BOARD_OVERFLOW_RE = re.compile(r'^\u2026and \d+ more$')
_MARKDOWN_ESCAPED = re.compile(r'\\([\\*_~`|>])')


def unescape_markdown(text):
    return _MARKDOWN_ESCAPED.sub(r'\1', text)


def parse_board_embed(title, description):
    """Recover a day's scores from a leaderboard the bot posted earlier.

    Returns None for anything that is not a daily board, so the weekly rollup
    cannot swallow its own output or an unrelated embed.
    """
    if not title or not description:
        return None

    title_match = _BOARD_TITLE_RE.match(title.strip())
    if not title_match:
        return None

    entries = []
    truncated = False
    for line in description.replace('\r\n', '\n').split('\n'):
        line = line.strip()
        if not line:
            continue

        if _BOARD_OVERFLOW_RE.match(line):
            truncated = True
            continue

        # Anything else is the emoji line under an entry or the empty-board notice
        entry_match = _BOARD_ENTRY_RE.match(line)
        if not entry_match:
            continue

        score = int(entry_match.group(2))
        if score > MAX_SCORE:
            continue

        entries.append({
            'name': unescape_markdown(entry_match.group(1)),
            'score': score,
        })

    return {
        'puzzle': int(title_match.group(1)),
        'entries': entries,
        'truncated': truncated,
    }


def aggregate_week(boards):
    """Collapse a week of boards into one record per player.

    Only the newest board for each puzzle counts, since the bot rewrites the board
    on every submission and the finished one is left in the channel.
    """
    latest = {}
    for board in boards:
        current = latest.get(board['puzzle'])
        if current is None or board['posted_at'] >= current['posted_at']:
            latest[board['puzzle']] = board

    stats = {}
    truncated = False
    for puzzle in sorted(latest):
        board = latest[puzzle]
        truncated = truncated or board.get('truncated', False)

        for entry in board['entries']:
            # Keying on the member keeps a mid-week nickname change from splitting
            # one player in two; an unmatched name falls back to its own text.
            key = entry.get('user_id') or entry['name'].casefold()
            record = stats.setdefault(key, {'name': entry['name'], 'scores': []})
            record['name'] = entry['name']
            record['scores'].append(entry['score'])

    return stats, truncated


def weighted_leaderboard(stats, prior=None, min_plays=None):
    """Rank by an average pulled toward the week's mean in proportion to plays.

    Everyone is treated as having started the week with `prior` games at the week's
    own average, so those games dilute as real ones arrive: a week of playing earns
    its average outright, while a single lucky score stays close to the middle and
    cannot top the board. Using the week's own mean keeps this honest whether the
    puzzles were easy or brutal.
    """
    if prior is None:
        prior = WEEKLY_PRIOR
    if min_plays is None:
        min_plays = WEEKLY_MIN_PLAYS

    scores = [score for record in stats.values() for score in record['scores']]
    if not scores:
        return [], [], 0.0

    week_mean = sum(scores) / float(len(scores))

    ranked = []
    unranked = []
    for record in stats.values():
        plays = len(record['scores'])
        mean = sum(record['scores']) / float(plays)
        row = {
            'name': record['name'],
            'plays': plays,
            'mean': mean,
            'best': max(record['scores']),
            'adjusted': (plays * mean + prior * week_mean) / (plays + prior),
        }
        (ranked if plays >= min_plays else unranked).append(row)

    ranked.sort(key=lambda row: (-row['adjusted'], -row['plays'], -row['mean'], row['name'].casefold()))
    unranked.sort(key=lambda row: (-row['mean'], row['name'].casefold()))

    return ranked, unranked, week_mean


def _plays_label(plays):
    return '1 play' if plays == 1 else '{} plays'.format(plays)


def build_weekly_content(label, ranked, unranked, week_mean, truncated=False, min_plays=None):
    """Render the weekly rollup as (title, description, footer)."""
    if min_plays is None:
        min_plays = WEEKLY_MIN_PLAYS

    title = '\U0001F990 Krillion Weekly \u2014 {}'.format(label)
    footer = 'Week average {} \u00b7 weighted by plays \u00b7 posted Mondays at 10:00 PM Mountain Time'.format(
        int(round(week_mean))
    )
    if truncated:
        footer = 'Some daily boards were too long to list everyone \u00b7 ' + footer

    if not ranked and not unranked:
        return title, 'Nobody posted a score this week.', footer

    blocks = []
    used = 0
    hidden = 0
    for position, row in enumerate(ranked, start=1):
        marker = _MEDALS.get(position, '{}.'.format(position))
        block = '{} **{}** \u2014 {}  (avg {} \u00b7 {})'.format(
            marker,
            escape_markdown(row['name']),
            int(round(row['adjusted'])),
            int(round(row['mean'])),
            _plays_label(row['plays']),
        )
        if hidden or used + len(block) > _DESCRIPTION_BUDGET:
            hidden += 1
            continue
        blocks.append(block)
        used += len(block) + 1

    if hidden:
        blocks.append('\u2026and {} more'.format(hidden))

    if unranked:
        names = ', '.join(
            '{} ({}, {})'.format(escape_markdown(row['name']), int(round(row['mean'])), _plays_label(row['plays']))
            for row in unranked
        )
        tail = '\nNot enough plays to rank (needs {}+): {}'.format(min_plays, names)
        if used + len(tail) <= _DESCRIPTION_BUDGET:
            blocks.append(tail)

    return title, '\n'.join(blocks), footer


class KrillionStore(object):
    """One JSON document holding today's scores, the boards showing them, and the
    small amount of per-guild bookkeeping that has to outlive the daily wipe."""

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
            'meta': {},
        }

    def load(self):
        try:
            with open(self.path, 'r') as handle:
                raw = handle.read()
        except IOError:
            self.data = self._empty()
            return self.data

        # The deploy script creates the file up front so docker mounts it as a
        # file rather than a directory, so an empty one just means "no scores yet".
        if not raw.strip():
            self.data = self._empty()
            return self.data

        try:
            data = json.loads(raw)
        except ValueError as error:
            backup = '{}.corrupt-{}'.format(self.path, int(time.time()))
            print('Krillion: unreadable store ({}); preserving it at {}'.format(error, backup))
            try:
                shutil.copyfile(self.path, backup)
            except (IOError, OSError) as copy_error:
                print('Krillion: could not preserve the unreadable store: {}'.format(copy_error))
            self.data = self._empty()
            return self.data

        if not isinstance(data, dict) or data.get('version') != STORE_VERSION:
            print('Krillion: ignoring store with unexpected shape or version')
            self.data = self._empty()
            return self.data

        data.setdefault('guilds', {})
        data.setdefault('meta', {})
        data.setdefault('window', current_window_key())
        self.data = data
        return self.data

    def _write_atomically(self, directory, payload):
        """Write beside the target and swap it in, so a crash mid-write cannot
        leave a half-written leaderboard behind."""
        handle, temp_path = tempfile.mkstemp(dir=directory, prefix='.krillion-', suffix='.json')
        try:
            with os.fdopen(handle, 'w') as stream:
                handle = None
                stream.write(payload)
            os.replace(temp_path, self.path)
            temp_path = None
        finally:
            if handle is not None:
                os.close(handle)
            if temp_path is not None and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _write_in_place(self, payload):
        with open(self.path, 'w') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

    def save(self):
        directory = os.path.dirname(os.path.abspath(self.path))
        payload = json.dumps(self.data)
        try:
            if directory and not os.path.isdir(directory):
                os.makedirs(directory)
            try:
                self._write_atomically(directory, payload)
            except OSError:
                # When the file itself is a docker bind mount it is a mount point,
                # and nothing can be renamed over it, so write through it instead.
                self._write_in_place(payload)
            self.persistent = True
        except (IOError, OSError) as error:
            if self.persistent:
                print('Krillion: could not write {} ({}); keeping scores in memory only'.format(self.path, error))
            self.persistent = False

    def run_retention(self, now=None):
        """Drop the scores from earlier windows and return whether anything reset.

        Boards from previous days are forgotten rather than deleted, so the last
        leaderboard of each day stays in the channel as a record of the scores.
        """
        window = current_window_key(now)
        if self.data.get('window') == window:
            return False

        # meta is carried across because the weekly rollup needs to remember which
        # week it last posted, and that outlives any single day of scores.
        meta = self.data.get('meta', {})
        self.data = self._empty(window)
        self.data['meta'] = meta
        self.save()
        return True

    def _guild_meta(self, guild_id, create=False):
        if create:
            return self.data.setdefault('meta', {}).setdefault(str(guild_id), {})
        return self.data.get('meta', {}).get(str(guild_id), {})

    def get_meta(self, guild_id, field, default=None):
        return self._guild_meta(guild_id).get(field, default)

    def set_meta(self, guild_id, field, value):
        self._guild_meta(guild_id, create=True)[field] = value
        self.save()

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
        # Remembered past the daily wipe so the weekly rollup knows where the
        # leaderboards live without a channel ever being configured.
        self._guild_meta(guild_id, create=True)['last_board_channel_id'] = channel_id
        self.save()

    def clear_board(self, guild_id, puzzle):
        bucket = self._puzzle_bucket(guild_id, puzzle)
        if bucket:
            bucket['board'] = None
            self.save()
