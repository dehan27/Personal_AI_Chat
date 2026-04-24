"""Phase 6-2 FieldSpec 단위 테스트."""

from django.test import SimpleTestCase

from chat.workflows.domains.field_spec import FieldSpec, SUPPORTED_TYPES


class FieldSpecTests(SimpleTestCase):
    def test_date_required_by_default(self):
        spec = FieldSpec(type='date')
        self.assertTrue(spec.required)
        self.assertEqual(spec.aliases, ())
        self.assertIsNone(spec.default)
        self.assertEqual(dict(spec.enum_values), {})

    def test_unsupported_type_rejected(self):
        with self.assertRaises(ValueError):
            FieldSpec(type='unknown')

    def test_enum_requires_values(self):
        with self.assertRaises(ValueError):
            FieldSpec(type='enum')

    def test_enum_values_forbidden_for_non_enum(self):
        with self.assertRaises(ValueError):
            FieldSpec(type='date', enum_values={'a': ('x',)})

    def test_all_supported_types_construct(self):
        FieldSpec(type='date')
        FieldSpec(type='number')
        FieldSpec(type='money')
        FieldSpec(type='enum', enum_values={'k': ('v',)})
        FieldSpec(type='number_list')
        FieldSpec(type='text')

    def test_supported_types_exposed(self):
        self.assertIn('date', SUPPORTED_TYPES)
        self.assertIn('money', SUPPORTED_TYPES)
        self.assertIn('number_list', SUPPORTED_TYPES)
        self.assertIn('text', SUPPORTED_TYPES)

    def test_text_type_without_enum_values(self):
        # Phase 6-3: 'text' 필드는 enum_values 가 비어있어야 한다.
        FieldSpec(type='text', required=True)
        with self.assertRaises(ValueError):
            FieldSpec(type='text', enum_values={'k': ('v',)})
