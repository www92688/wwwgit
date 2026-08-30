# 冒烟④：SQLite WAL 模式多线程并发写无异常（D1 替身冒烟，对应 T-5 线程约定）
import sqlite3
from threading import Thread

N_THREADS = 4
N_ROWS = 50


def _new_conn(db_path):
    """模拟生产 S3 的 thread-local 连接模式（T-5：每线程独立连接）。"""
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def test_sqlite_wal_multithread_concurrent_write(tmp_path):
    db_path = tmp_path / "wal_smoke.db"

    # 主线程建库建表，确认 WAL 生效
    init = _new_conn(db_path)
    mode = init.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal", f"WAL 未生效: {mode}"
    init.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER NOT NULL)")
    init.commit()
    init.close()

    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            conn = _new_conn(db_path)
            for i in range(N_ROWS):
                conn.execute(
                    "INSERT INTO t(v) VALUES (?)", (idx * N_ROWS + i,)
                )
                conn.commit()
            conn.close()
        except sqlite3.Error as exc:  # 并发冲突/锁异常必须显式暴露
            errors.append(exc)

    threads = [Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)

    assert not errors, f"并发写入出现异常: {errors[:3]}"

    # 校验总行数
    verify = _new_conn(db_path)
    count = verify.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    verify.close()
    assert count == N_THREADS * N_ROWS, f"期望 {N_THREADS * N_ROWS} 行，实得 {count}"
