"""Schedule compatibility and deterministic semester cardinality."""
from collections import defaultdict
import datetime as dt
import unittest

from dataset import identity, meeting_rows, variant_manifest


class ScheduleTest(unittest.TestCase):
    sections = [{'id': identity('section', i)} for i in range(600)]

    def test_phase6_keeps_exact_ids_and_times(self):
        manifest = variant_manifest('phase6-v1')
        rows = meeting_rows(self.sections, manifest)
        self.assertEqual(len(rows), 1200)
        start = dt.datetime(2026, 11, 16, 8, tzinfo=dt.timezone.utc)
        for i, section in enumerate(self.sections):
            for occurrence in range(2):
                index = i * 2 + occurrence
                at = start + dt.timedelta(days=7 * occurrence + i % 5, hours=2 * ((i // 5) % 5))
                self.assertEqual(rows[index], (
                    identity('meeting', index), section['id'], at, at + dt.timedelta(hours=1),
                    'LECTURE', 'true', 'Synthetic Room',
                ))

    def test_semester_has_two_distinct_occurrences_per_section_per_week(self):
        manifest = variant_manifest('semester-v1')
        rows = meeting_rows(self.sections, manifest)
        self.assertEqual(rows, meeting_rows(self.sections, manifest))
        self.assertEqual(len(rows), 16800)
        self.assertEqual(len({row[0] for row in rows}), 16800)
        self.assertTrue({row[0] for row in rows}.isdisjoint(
            row[0] for row in meeting_rows(self.sections, variant_manifest('phase6-v1'))))
        by_section = defaultdict(list)
        for _, section, start, end, *_ in rows:
            self.assertGreater(end, start)
            self.assertTrue(dt.date(2026, 11, 9) <= start.date() <= end.date() <= dt.date(2027, 5, 1))
            by_section[section].append((start, end))
        self.assertEqual(len(by_section), 600)
        for meetings in by_section.values():
            self.assertEqual(len(meetings), 28)
            for first, second in zip(meetings[::2], meetings[1::2], strict=True):
                self.assertEqual(first[0].isocalendar()[:2], second[0].isocalendar()[:2])
                self.assertLess(first[1], second[0])
            self.assertEqual(len({start.isocalendar()[:2] for start, _ in meetings}), 14)


if __name__ == '__main__':
    unittest.main()
