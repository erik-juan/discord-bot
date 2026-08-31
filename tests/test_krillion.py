import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import krillion

SAMPLE = 'Krillion #45 \U0001F990\n285\n\n\U0001F3EE\U0001F991\U0001F41F\U0001FAE7\U0001F991\U0001FAE7\U0001F41F'
RESULTS = '\U0001F3EE\U0001F991\U0001F41F\U0001FAE7\U0001F991\U0001FAE7\U0001F41F'


def mountain(year, month, day, hour, minute=0):
    return krillion.MOUNTAIN.localize(datetime(year, month, day, hour, minute))


class ParseTest(unittest.TestCase):
    def test_parses_the_shared_result(self):
        result = krillion.parse_krillion_message(SAMPLE)
        self.assertEqual(result, {'puzzle': 45, 'score': 285, 'emojis': RESULTS})

    def test_tolerates_surrounding_blank_lines_and_carriage_returns(self):
        padded = '\r\n' + SAMPLE.replace('\n', '\r\n') + '\r\n\r\n'
        self.assertEqual(krillion.parse_krillion_message(padded)['puzzle'], 45)

    def test_accepts_the_score_boundaries(self):
        for score in (0, 700):
            message = 'Krillion #1 \U0001F990\n{}\n\n{}'.format(score, RESULTS)
            self.assertEqual(krillion.parse_krillion_message(message)['score'], score)

    def test_rejects_a_score_above_the_maximum(self):
        message = 'Krillion #1 \U0001F990\n701\n\n{}'.format(RESULTS)
        self.assertIsNone(krillion.parse_krillion_message(message))

    def test_requires_exactly_seven_results(self):
        for count in (6, 8):
            message = 'Krillion #45 \U0001F990\n285\n\n{}'.format('\U0001F41F' * count)
            self.assertIsNone(krillion.parse_krillion_message(message))

    def test_counts_multi_codepoint_emoji_as_one_result(self):
        joined = '\U0001F468\u200D\U0001F469\u200D\U0001F467'
        skin_toned = '\U0001F44D\U0001F3FD'
        flag = '\U0001F1FA\U0001F1F8'
        results = joined + skin_toned + flag + '\U0001F41F' * 4
        message = 'Krillion #45 \U0001F990\n285\n\n{}'.format(results)
        self.assertEqual(krillion.parse_krillion_message(message)['emojis'], results)

    def test_rejects_stray_text_hidden_among_the_results(self):
        results = '\U0001F41F' * 6 + 'x'
        message = 'Krillion #45 \U0001F990\n285\n\n{}'.format(results)
        self.assertIsNone(krillion.parse_krillion_message(message))

    def test_rejects_messages_that_are_not_shared_results(self):
        malformed = [
            '',
            'Krillion #45',
            'hey did anyone play Krillion \U0001F990 today',
            'Krillion #45 \U0001F990\n285\n{}'.format(RESULTS),
            'Krillion #45 \U0001F990\n\n{}'.format(RESULTS),
            'Krillion #45\n285\n\n{}'.format(RESULTS),
            'Wordle 1,234 4/6\n285\n\n{}'.format(RESULTS),
            'Krillion #45 \U0001F990\nlots\n\n{}'.format(RESULTS),
            'Krillion #45 \U0001F990\n285\n\nnice run today!',
            'Krillion #45 \U0001F990\n285\n\n{} gg'.format(RESULTS),
            'Krillion #45 \U0001F990\n285\n\n{}\nbeat that'.format(RESULTS),
        ]
        for message in malformed:
            self.assertIsNone(krillion.parse_krillion_message(message), message)


class WindowTest(unittest.TestCase):
    def test_window_turns_over_at_ten_pm_mountain(self):
        self.assertEqual(krillion.current_window_key(mountain(2026, 8, 29, 21, 59)), '2026-08-28')
        self.assertEqual(krillion.current_window_key(mountain(2026, 8, 29, 22, 0)), '2026-08-29')
        self.assertEqual(krillion.current_window_key(mountain(2026, 8, 29, 22, 1)), '2026-08-29')

    def test_window_spans_the_whole_day_until_the_next_reset(self):
        self.assertEqual(krillion.current_window_key(mountain(2026, 8, 30, 9, 0)), '2026-08-29')
        self.assertEqual(krillion.current_window_key(mountain(2026, 8, 30, 21, 59)), '2026-08-29')

    def test_window_follows_local_time_across_the_spring_change(self):
        before = mountain(2026, 3, 7, 22, 0)
        after = mountain(2026, 3, 8, 22, 0)
        self.assertNotEqual(before.utcoffset(), after.utcoffset())
        self.assertEqual(krillion.current_window_key(before), '2026-03-07')
        self.assertEqual(krillion.current_window_key(mountain(2026, 3, 8, 21, 0)), '2026-03-07')
        self.assertEqual(krillion.current_window_key(after), '2026-03-08')

    def test_window_follows_local_time_across_the_autumn_change(self):
        before = mountain(2026, 10, 31, 22, 0)
        after = mountain(2026, 11, 1, 22, 0)
        self.assertNotEqual(before.utcoffset(), after.utcoffset())
        self.assertEqual(krillion.current_window_key(before), '2026-10-31')
        self.assertEqual(krillion.current_window_key(mountain(2026, 11, 1, 21, 0)), '2026-10-31')
        self.assertEqual(krillion.current_window_key(after), '2026-11-01')

    def test_naive_times_are_read_as_utc(self):
        # 04:00 UTC is 22:00 the previous evening in Mountain daylight time
        self.assertEqual(krillion.current_window_key(datetime(2026, 8, 30, 4, 0)), '2026-08-29')


class RankingTest(unittest.TestCase):
    def entry(self, name, score, submitted_at):
        return {'name': name, 'score': score, 'emojis': RESULTS, 'submitted_at': submitted_at}

    def test_highest_score_comes_first(self):
        ranked = krillion.rank_entries([
            self.entry('low', 100, 1),
            self.entry('high', 400, 2),
            self.entry('mid', 285, 3),
        ])
        self.assertEqual([entry['name'] for _, entry in ranked], ['high', 'mid', 'low'])

    def test_ties_share_a_rank_and_the_next_rank_is_skipped(self):
        ranked = krillion.rank_entries([
            self.entry('second', 300, 20),
            self.entry('first', 300, 10),
            self.entry('third', 200, 5),
        ])
        self.assertEqual([rank for rank, _ in ranked], [1, 1, 3])
        # an equal score is broken by who posted first
        self.assertEqual([entry['name'] for _, entry in ranked], ['first', 'second', 'third'])


class LeaderboardContentTest(unittest.TestCase):
    def entry(self, name, score):
        return {'name': name, 'score': score, 'emojis': RESULTS, 'submitted_at': 1}

    def test_shows_the_puzzle_and_reset_time(self):
        title, description, footer = krillion.build_leaderboard_content(45, [self.entry('Alice', 285)])
        self.assertIn('#45', title)
        self.assertIn('Alice', description)
        self.assertIn('285', description)
        self.assertIn(RESULTS, description)
        self.assertIn('10:00 PM Mountain Time', footer)

    def test_reports_an_empty_board(self):
        _, description, _ = krillion.build_leaderboard_content(45, [])
        self.assertEqual(description, 'No scores yet today.')

    def test_escapes_markdown_in_names(self):
        _, description, _ = krillion.build_leaderboard_content(45, [self.entry('_sneaky_', 285)])
        self.assertIn('\\_sneaky\\_', description)

    def test_truncates_a_very_long_board(self):
        entries = [self.entry('player number {}'.format(index), index) for index in range(200)]
        _, description, _ = krillion.build_leaderboard_content(45, entries)
        self.assertLess(len(description), 4096)
        self.assertIn('more', description)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, 'krillion.json')
        self.store = krillion.KrillionStore(self.path)
        self.store.load()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_starts_empty_when_there_is_no_file(self):
        self.assertEqual(self.store.get_entries(1, 45), [])
        self.assertIsNone(self.store.get_board(1, 45))

    def test_scores_survive_a_reload(self):
        self.store.record_score(1, 7, 'Alice', 45, 285, RESULTS, submitted_at=10)
        self.store.set_board(1, 45, 900, 901)

        reopened = krillion.KrillionStore(self.path)
        reopened.load()

        self.assertEqual(reopened.get_entries(1, 45), [
            {'name': 'Alice', 'score': 285, 'emojis': RESULTS, 'submitted_at': 10},
        ])
        self.assertEqual(reopened.get_board(1, 45), {'channel_id': 900, 'message_id': 901})

    def test_reposting_replaces_a_players_score(self):
        self.store.record_score(1, 7, 'Alice', 45, 285, RESULTS, submitted_at=10)
        self.store.record_score(1, 7, 'Alice', 45, 410, RESULTS, submitted_at=99)

        entries = self.store.get_entries(1, 45)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['score'], 410)
        # the first post still decides tie ordering
        self.assertEqual(entries[0]['submitted_at'], 10)

    def test_players_puzzles_and_guilds_are_kept_apart(self):
        self.store.record_score(1, 7, 'Alice', 45, 285, RESULTS)
        self.store.record_score(1, 8, 'Bob', 45, 310, RESULTS)
        self.store.record_score(1, 7, 'Alice', 46, 120, RESULTS)
        self.store.record_score(2, 7, 'Alice', 45, 500, RESULTS)

        self.assertEqual(len(self.store.get_entries(1, 45)), 2)
        self.assertEqual(len(self.store.get_entries(1, 46)), 1)
        self.assertEqual(len(self.store.get_entries(2, 45)), 1)

    def test_saving_leaves_no_temporary_files_behind(self):
        self.store.record_score(1, 7, 'Alice', 45, 285, RESULTS)

        leftovers = [name for name in os.listdir(self.directory) if name != 'krillion.json']
        self.assertEqual(leftovers, [])

        with open(self.path) as handle:
            self.assertEqual(json.load(handle)['version'], krillion.STORE_VERSION)

    def test_an_unwritable_location_keeps_scores_in_memory(self):
        blocker = os.path.join(self.directory, 'blocker')
        with open(blocker, 'w') as handle:
            handle.write('not a directory')

        store = krillion.KrillionStore(os.path.join(blocker, 'nested', 'krillion.json'))
        store.load()
        store.record_score(1, 7, 'Alice', 45, 285, RESULTS)

        self.assertFalse(store.persistent)
        self.assertEqual(len(store.get_entries(1, 45)), 1)

    def test_an_unreadable_file_is_preserved_rather_than_overwritten(self):
        with open(self.path, 'w') as handle:
            handle.write('{ this is not json')

        store = krillion.KrillionStore(self.path)
        store.load()

        self.assertEqual(store.get_entries(1, 45), [])
        preserved = [name for name in os.listdir(self.directory) if '.corrupt-' in name]
        self.assertEqual(len(preserved), 1)
        with open(os.path.join(self.directory, preserved[0])) as handle:
            self.assertEqual(handle.read(), '{ this is not json')


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.store = krillion.KrillionStore(os.path.join(self.directory, 'krillion.json'))
        self.store.load()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def seed(self, window):
        self.store.data['window'] = window
        self.store.record_score(1, 7, 'Alice', 45, 285, RESULTS)
        self.store.set_board(1, 45, 900, 901)
        self.store.data['window'] = window

    def test_scores_are_kept_until_the_reset(self):
        self.seed('2026-08-29')

        self.assertFalse(self.store.run_retention(mountain(2026, 8, 30, 21, 59)))
        self.assertEqual(len(self.store.get_entries(1, 45)), 1)
        self.assertEqual(self.store.get_board(1, 45), {'channel_id': 900, 'message_id': 901})

    def test_scores_are_wiped_at_the_reset(self):
        self.seed('2026-08-29')

        self.assertTrue(self.store.run_retention(mountain(2026, 8, 30, 22, 0)))
        self.assertEqual(self.store.get_entries(1, 45), [])
        self.assertEqual(self.store.data['window'], '2026-08-30')

    def test_yesterdays_leaderboard_is_forgotten_rather_than_deleted(self):
        self.seed('2026-08-29')
        self.store.run_retention(mountain(2026, 8, 30, 22, 0))

        # nothing points at the old message any more, so it stays in the channel
        # as a record of yesterday's scores
        self.assertIsNone(self.store.get_board(1, 45))

    def test_a_missed_reset_is_caught_up_on_the_next_check(self):
        self.seed('2026-08-01')

        self.assertTrue(self.store.run_retention(mountain(2026, 8, 30, 9, 0)))
        self.assertEqual(self.store.get_entries(1, 45), [])

    def test_a_wipe_is_written_to_disk(self):
        self.seed('2026-08-29')
        self.store.run_retention(mountain(2026, 8, 30, 22, 0))

        reopened = krillion.KrillionStore(self.store.path)
        reopened.load()

        self.assertEqual(reopened.get_entries(1, 45), [])
        self.assertEqual(reopened.data['window'], '2026-08-30')


if __name__ == '__main__':
    unittest.main()
