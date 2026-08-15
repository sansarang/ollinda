"""생성 실패 복구 골든 (2026-08-15).

사고: 생성이 실패하면 화면에 "생성이 중단됐어요 — 다시 시도해 주세요"라는
  **글자만** 뜨고 정작 다시 시도할 방법이 없었다. 사장님은 사진을 처음부터 다시 올려야 했다.
  게다가 실패 사유를 담는 error_note를 만들어놓고 화면에서 아무도 안 읽고 있었다.
  돈 내고 쓰는 분에게 이건 해지 사유다.

★ 사진은 성공했을 때만 지운다 — 실패 건의 사진은 서버에 남아 있다. 그걸 쓰면 된다.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def test_failed_jobs_with_photos_are_retryable():
    """사진이 남아 있는 실패 건만 다시 시도 대상이다 —
    사진이 없으면 다시 시도해도 못 만든다(헛된 버튼을 보여주지 않는다)."""
    d = _src("app/db.py")
    assert "def retryable_gen_jobs" in d, "다시 시도 대상 조회가 없다"
    i = d.find("def retryable_gen_jobs")
    seg = d[i:i + 1500]
    assert "status='failed'" in seg, "실패 건을 안 본다"
    assert "isdir" in seg and "listdir" in seg, "사진이 남아 있는지 확인하지 않는다"


def test_retry_reuses_the_existing_recovery_path():
    """복구 경로는 재시작 복구와 같은 ingest_upload 하나만 쓴다.
    새 경로를 만들면 한쪽만 고쳐진다 — 오늘까지 반복한 사고의 모양이다."""
    m = _src("app/main.py")
    i = m.find('@app.post("/me/gen-retry")')
    assert i > 0, "다시 시도 경로가 없다"
    seg = m[i:i + 2600]
    assert "ingest_upload" in seg, "기존 생성 경로를 안 쓴다(경로 이중화)"
    assert "finish_gen_job" in seg, "결과를 기록하지 않는다"
    assert "set_gen_progress" in seg and "failed" in seg, "재시도까지 실패하면 조용히 끝난다"


def test_failure_reason_reaches_the_screen():
    """error_note를 만들어놓고 아무도 안 읽던 상태로 돌아가면 안 된다."""
    m = _src("app/main.py")
    assert 'out["error"]' in m, "실패 사유를 화면으로 안 넘긴다"
    assert '"can_retry"' in m, "다시 시도 가능 여부를 안 넘긴다"
    assert "pr.error" in m, "화면이 실패 사유를 안 읽는다"


def test_retry_button_exists_on_failure():
    """안내 문구만 띄우고 누를 게 없으면, 사장님은 사진을 처음부터 다시 올려야 한다."""
    m = _src("app/main.py")
    assert "id='gRetry'" in m, "실패 화면에 다시 시도 버튼이 없다"
    assert "/me/gen-retry" in m, "버튼이 재시도 경로를 안 부른다"
    assert "올리신 사진은 그대로 있어요" in m, "사진이 남아 있다는 안내가 없다"
