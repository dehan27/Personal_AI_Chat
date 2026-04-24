"""Phase 5 날짜 헬퍼 단위 테스트."""

from datetime import date, datetime

from django.test import SimpleTestCase

from chat.workflows.core.dates import (
    days_between,
    ensure_date_order,
    months_between,
    parse_date,
    years_between,
)


class ParseDateTests(SimpleTestCase):
    def test_dash_format(self):
        self.assertEqual(parse_date('2025-01-31'), date(2025, 1, 31))

    def test_dot_format(self):
        self.assertEqual(parse_date('2025.01.31'), date(2025, 1, 31))

    def test_slash_format(self):
        self.assertEqual(parse_date('2025/01/31'), date(2025, 1, 31))

    def test_two_digit_year_expands_to_2000s(self):
        self.assertEqual(parse_date('25-01-31'), date(2025, 1, 31))

    def test_korean_natural_format(self):
        self.assertEqual(parse_date('2025년 1월 31일'), date(2025, 1, 31))

    def test_korean_natural_with_padded_zero(self):
        self.assertEqual(parse_date('2025년 01월 31일'), date(2025, 1, 31))

    def test_single_digit_month_and_day(self):
        self.assertEqual(parse_date('2025-1-5'), date(2025, 1, 5))

    def test_passthrough_date_instance(self):
        d = date(2024, 6, 15)
        self.assertIs(parse_date(d), d)

    def test_datetime_coerced_to_date(self):
        dt = datetime(2024, 6, 15, 12, 34)
        self.assertEqual(parse_date(dt), date(2024, 6, 15))

    def test_unknown_format_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_date('31 January 2025')

    def test_invalid_calendar_date_raises(self):
        with self.assertRaises(ValueError):
            parse_date('2025-02-30')

    def test_non_string_non_date_raises_type_error(self):
        with self.assertRaises(TypeError):
            parse_date(12345)

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            parse_date('   ')


class DaysBetweenTests(SimpleTestCase):
    def test_positive(self):
        self.assertEqual(days_between('2025-01-01', '2025-01-10'), 9)

    def test_zero(self):
        self.assertEqual(days_between('2025-01-01', '2025-01-01'), 0)

    def test_negative_when_reversed(self):
        self.assertEqual(days_between('2025-01-10', '2025-01-01'), -9)


class MonthsBetweenTests(SimpleTestCase):
    def test_exact_month(self):
        self.assertEqual(months_between('2025-01-15', '2025-03-15'), 2)

    def test_not_quite_month(self):
        self.assertEqual(months_between('2025-01-15', '2025-03-14'), 1)

    def test_past_anchor_day(self):
        self.assertEqual(months_between('2025-01-15', '2025-03-20'), 2)

    def test_cross_year(self):
        self.assertEqual(months_between('2024-11-01', '2025-02-01'), 3)

    def test_negative_reversed(self):
        self.assertEqual(months_between('2025-03-15', '2025-01-15'), -2)


class YearsBetweenTests(SimpleTestCase):
    def test_exact_year(self):
        self.assertEqual(years_between('2020-05-10', '2025-05-10'), 5)

    def test_not_quite_year(self):
        self.assertEqual(years_between('2020-05-10', '2025-05-09'), 4)

    def test_past_anchor_day(self):
        self.assertEqual(years_between('2020-05-10', '2025-06-01'), 5)

    def test_negative_reversed(self):
        self.assertEqual(years_between('2025-05-10', '2020-05-10'), -5)


class EnsureDateOrderTests(SimpleTestCase):
    def test_ok_when_start_before_end(self):
        r = ensure_date_order('2025-01-01', '2025-12-31')
        self.assertTrue(r.ok)

    def test_ok_when_equal(self):
        r = ensure_date_order('2025-01-01', '2025-01-01')
        self.assertTrue(r.ok)

    def test_fail_when_reversed(self):
        r = ensure_date_order('2025-12-31', '2025-01-01')
        self.assertFalse(r.ok)
        self.assertIn('시작일이 종료일보다 뒤입니다.', r.errors)

    def test_fail_collects_both_parse_errors(self):
        r = ensure_date_order('bad', 'worse')
        self.assertFalse(r.ok)
        self.assertEqual(len(r.errors), 2)
