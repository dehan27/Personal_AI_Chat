"""Phase 5 result 타입 단위 테스트."""

from django.test import SimpleTestCase

from chat.workflows.core.result import (
    ValidationResult,
    WorkflowResult,
    WorkflowStatus,
)


class ValidationResultTests(SimpleTestCase):
    def test_success_factory_is_ok_and_empty(self):
        r = ValidationResult.success()
        self.assertTrue(r.ok)
        self.assertEqual(r.missing_fields, ())
        self.assertEqual(r.errors, ())

    def test_fail_with_missing_only(self):
        r = ValidationResult.fail(missing=['start_date'])
        self.assertFalse(r.ok)
        self.assertEqual(r.missing_fields, ('start_date',))
        self.assertEqual(r.errors, ())

    def test_fail_with_errors_only(self):
        r = ValidationResult.fail(errors=['invalid date'])
        self.assertFalse(r.ok)
        self.assertEqual(r.missing_fields, ())
        self.assertEqual(r.errors, ('invalid date',))

    def test_fail_with_both(self):
        r = ValidationResult.fail(missing=['a'], errors=['b'])
        self.assertEqual(r.missing_fields, ('a',))
        self.assertEqual(r.errors, ('b',))

    def test_fail_rejects_empty_inputs(self):
        with self.assertRaises(ValueError):
            ValidationResult.fail()

    def test_frozen_dataclass_is_hashable(self):
        r1 = ValidationResult.success()
        r2 = ValidationResult.success()
        self.assertEqual(hash(r1), hash(r2))
        # equality
        self.assertEqual(r1, r2)


class WorkflowResultTests(SimpleTestCase):
    def test_ok_factory_sets_status_and_value(self):
        r = WorkflowResult.ok(12345)
        self.assertEqual(r.status, WorkflowStatus.OK)
        self.assertEqual(r.value, 12345)
        self.assertEqual(r.missing_fields, ())
        self.assertEqual(r.warnings, ())

    def test_ok_factory_accepts_details_and_warnings(self):
        r = WorkflowResult.ok(
            1,
            details={'base': 100, 'rate': 0.01},
            warnings=['data stale'],
        )
        self.assertEqual(r.details['base'], 100)
        self.assertEqual(r.warnings, ('data stale',))
        # details is read-only (MappingProxyType)
        with self.assertRaises(TypeError):
            r.details['base'] = 0

    def test_missing_input_factory(self):
        r = WorkflowResult.missing_input(['hire_date'])
        self.assertEqual(r.status, WorkflowStatus.MISSING_INPUT)
        self.assertEqual(r.missing_fields, ('hire_date',))
        self.assertIsNone(r.value)

    def test_invalid_input_factory_carries_errors_in_details(self):
        r = WorkflowResult.invalid_input(errors=['date order wrong'])
        self.assertEqual(r.status, WorkflowStatus.INVALID_INPUT)
        self.assertEqual(r.details['errors'], ('date order wrong',))

    def test_unsupported_factory_carries_reason(self):
        r = WorkflowResult.unsupported('no domain handler yet')
        self.assertEqual(r.status, WorkflowStatus.UNSUPPORTED)
        self.assertEqual(r.details['reason'], 'no domain handler yet')

    def test_not_found_factory(self):
        # Phase 6-3: 자료에서 매치 실패.
        r = WorkflowResult.not_found('질문에 맞는 자료를 찾지 못했습니다.')
        self.assertEqual(r.status, WorkflowStatus.NOT_FOUND)
        self.assertEqual(r.details['reason'], '질문에 맞는 자료를 찾지 못했습니다.')
        self.assertIsNone(r.value)

    def test_upstream_error_factory(self):
        # Phase 6-3: LLM / 네트워크 일시 장애.
        r = WorkflowResult.upstream_error('표 해석 중 일시 오류')
        self.assertEqual(r.status, WorkflowStatus.UPSTREAM_ERROR)
        self.assertEqual(r.details['reason'], '표 해석 중 일시 오류')

    def test_new_status_values_are_plain_strings(self):
        self.assertEqual(WorkflowStatus.NOT_FOUND.value, 'not_found')
        self.assertEqual(WorkflowStatus.UPSTREAM_ERROR.value, 'upstream_error')

    def test_status_enum_value_is_plain_string(self):
        # JSON / DB 저장을 위해 enum 이 문자열로 직접 쓰여야 한다.
        self.assertEqual(WorkflowStatus.OK.value, 'ok')
        self.assertEqual(f'{WorkflowStatus.OK}', 'WorkflowStatus.OK')  # enum repr
        self.assertEqual(str(WorkflowStatus.OK.value), 'ok')


# ---------------------------------------------------------------------------
# Phase 8-1: BaseResult Protocol 도입 회귀 가드
# ---------------------------------------------------------------------------


class WorkflowResultBackwardCompatTests(SimpleTestCase):
    """Phase 8-1: BaseResult 추출 후 WorkflowResult public contract 변경 0 검증.

    외부 호출부 (~50 곳 단위 테스트 포함) 한 줄도 안 바뀜을 보장하는 회귀 가드.
    """

    def test_workflow_result_dataclass_fields_unchanged(self):
        # 기존 5 필드 그대로 — 추가/제거 없음.
        from dataclasses import fields
        names = {f.name for f in fields(WorkflowResult)}
        self.assertEqual(
            names,
            {'status', 'value', 'details', 'missing_fields', 'warnings'},
        )

    def test_workflow_result_factory_signatures_unchanged(self):
        # 6 팩토리 모두 호출 가능 + 반환 타입 그대로.
        self.assertIsInstance(WorkflowResult.ok(1), WorkflowResult)
        self.assertIsInstance(WorkflowResult.missing_input(['a']), WorkflowResult)
        self.assertIsInstance(WorkflowResult.invalid_input(['e']), WorkflowResult)
        self.assertIsInstance(WorkflowResult.unsupported('r'), WorkflowResult)
        self.assertIsInstance(WorkflowResult.not_found('r'), WorkflowResult)
        self.assertIsInstance(WorkflowResult.upstream_error('r'), WorkflowResult)

    def test_workflow_status_enum_values_unchanged(self):
        # 6 enum 값 변경 0 — DB 저장 / JSON 직렬화 호환 유지.
        self.assertEqual(
            {s.value for s in WorkflowStatus},
            {'ok', 'missing_input', 'invalid_input', 'unsupported',
             'not_found', 'upstream_error'},
        )


class BaseResultProtocolTests(SimpleTestCase):
    """Phase 8-1: BaseResult Protocol structural typing 검증."""

    def test_workflow_result_satisfies_base_result_protocol(self):
        from chat.workflows.core.result import BaseResult
        r = WorkflowResult.ok(42)
        # runtime_checkable Protocol — isinstance check.
        self.assertIsInstance(r, BaseResult)

    def test_protocol_imports_from_core_package(self):
        # __init__.py 에 export 됐는지.
        from chat.workflows.core import BaseResult as BR
        self.assertIsNotNone(BR)
