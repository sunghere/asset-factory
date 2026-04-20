"""lib 패키지 — 백엔드 공통 유틸.

현재는 이벤트 타입 선언만 담는다. 파일이 늘어나면 ``server.py`` 에서 점진적으로 분리한다.
"""

from lib import events as events  # re-export for convenience

__all__ = ["events"]
