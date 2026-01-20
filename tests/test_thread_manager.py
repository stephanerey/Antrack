from antrack.threading_utils.thread_manager import TaskStatus, ThreadManager


def test_thread_manager_records_completion():
    tm = ThreadManager()

    record = tm._ensure_task("TestTask", description="work")
    record.status = TaskStatus.RUNNING
    record.started_at = 100.0

    tm._cleanup_thread("TestTask")

    diag = tm.get_diagnostics()["TestTask"]
    assert diag["status"] == TaskStatus.FINISHED
    assert diag["started_at"] is not None


def test_thread_manager_records_error():
    tm = ThreadManager()

    record = tm._ensure_task("BoomTask", description="boom")
    record.status = TaskStatus.RUNNING
    record.started_at = 200.0

    class DummyWorker:
        last_traceback = "traceback"

    tm.workers["BoomTask"] = DummyWorker()
    tm._record_error("BoomTask", "boom")
    tm._cleanup_thread("BoomTask")

    diag = tm.get_diagnostics()["BoomTask"]
    assert diag["status"] == TaskStatus.FAILED
    assert "boom" in (diag["last_error"] or "")
    assert diag["last_traceback"]
