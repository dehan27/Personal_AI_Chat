"""프롬프트 파일 로더.

settings.PROMPTS_DIR 하위의 .md/.txt 파일을 읽어 문자열로 반환한다.
코드 곳곳에서 직접 open()을 호출하지 않고 이 모듈을 거치게 해서
경로 관리·캐시·예외 처리를 한 곳에 모은다.

Phase 1 단순 구현:
- 프로세스 메모리 dict 기반의 얕은 캐시
- 저장 시 해당 키만 invalidate
- 파일 누락 → PromptNotFound (배포 누락을 조용히 삼키지 않음)
- save_prompt는 path traversal 방지 (PROMPTS_DIR 밖 경로 거부)
"""

from pathlib import Path
from threading import RLock

from django.conf import settings


class PromptNotFound(FileNotFoundError):
    """주어진 상대 경로에 프롬프트 파일이 없을 때."""


# ---------------------------------------------------------------------------
# 내부 캐시
# ---------------------------------------------------------------------------

_cache: dict[str, str] = {}
_cache_lock = RLock()


def _resolve_path(relative_path: str) -> Path:
    """PROMPTS_DIR 기준 안전한 절대경로를 계산. 밖으로 나가면 ValueError."""
    base = Path(settings.PROMPTS_DIR).resolve()
    target = (base / relative_path).resolve()
    # base 하위인지 검사 (심볼릭 링크·상위 경로(..) 차단)
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            f'허용되지 않은 경로입니다: {relative_path}'
        ) from exc
    return target


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def load_prompt(relative_path: str) -> str:
    """프롬프트 파일을 읽어 문자열로 반환.

    - 캐시가 있으면 캐시값 반환
    - 파일이 없으면 PromptNotFound
    - 파일 끝에 붙는 줄바꿈 한 개는 제거(기존 코드 상수에 .strip() 쓰던 동작 매칭)
    """
    with _cache_lock:
        cached = _cache.get(relative_path)
        if cached is not None:
            return cached

    target = _resolve_path(relative_path)
    if not target.is_file():
        raise PromptNotFound(f'프롬프트 파일 없음: {relative_path}')

    text = target.read_text(encoding='utf-8').rstrip('\n')

    with _cache_lock:
        _cache[relative_path] = text

    return text


def save_prompt(relative_path: str, content: str) -> None:
    """프롬프트 파일을 저장하고 캐시를 무효화.

    - PROMPTS_DIR 밖 경로는 ValueError
    - 부모 디렉토리가 없으면 자동 생성
    - 원자적 저장(임시 파일 작성 후 replace)로 쓰는 중 읽히는 케이스 방지
    """
    target = _resolve_path(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp = target.with_suffix(target.suffix + '.tmp')
    tmp.write_text(content, encoding='utf-8')
    tmp.replace(target)

    with _cache_lock:
        _cache.pop(relative_path, None)


def invalidate_cache(relative_path: str | None = None) -> None:
    """테스트/긴급 운영용 캐시 무효화. 인자 없으면 전체 삭제."""
    with _cache_lock:
        if relative_path is None:
            _cache.clear()
        else:
            _cache.pop(relative_path, None)
