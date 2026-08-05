"""🛡 자가 학습 면역계 — 사고가 데이터가 되고, 데이터가 방어가 된다.

콘텐츠 성과 학습 루프(관측→learned_signals→생성 주입)를 버그에 복제한 것.
원료는 지난 두 달 반의 실제 사고 이력(git log·lessons.md·골든)이다.

목표 둘:
  ① 같은 계열 사고 2회의 구조적 봉쇄
  ② 사장님보다 시스템이 먼저 발견

이 패키지는 **관측·검진 레이어**다. 본체 생성·발행 경로를 고치지 않는다(R6).
"""


import os


def data_root() -> str:
    """면역계 파일이 살 곳 — **DB가 사는 곳이 곧 살아남는 곳이다.**

    ★ 2026-08-05: 백업·진단서를 상대경로 data/ 에 두었다. 그건 컨테이너 파일시스템이라
      배포 한 번에 사라진다 — '원본 보존'이라고 해놓고 배포 때 지워지면 침묵 수정과 같다(R2).
      영속 볼륨은 SHOPCAST_DB가 가리키는 디렉터리다(Dockerfile: /data/shopcast.sqlite).
    ★ 경로 규칙은 이 함수 하나뿐이다. 면역계가 감시하는 '경로 이원화'를 스스로 어기지 않는다.
    """
    dbp = os.environ.get("SHOPCAST_DB", "")
    d = os.path.dirname(dbp) if dbp else ""
    return d or "data"


def is_persistent() -> bool:
    """지금 쓰는 경로가 배포를 넘어 살아남는가.

    살아남지 않으면 **자동 수선을 하지 않는다** — 백업 없이 고치는 것이 R2 위반이다.
    설정 실수로도 그 일이 생기지 않게 규율이 아니라 구조로 막는다.
    """
    root = data_root()
    return os.path.isabs(root) and os.path.isdir(root)


def path(name: str) -> str:
    return os.path.join(data_root(), name)
