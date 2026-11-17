import pytest


@pytest.fixture
def magemin_subprocess_lock_dir(tmp_path, monkeypatch):
    """Give mocked MAGEMin subprocess calls a private slot-lock directory."""
    import simulator.melt_backend.magemin as magemin

    lock_dir = tmp_path / 'magemin-subprocess-locks'
    monkeypatch.setattr(magemin, '_MAGEMIN_SUBPROCESS_LOCK_DIR', lock_dir)
    monkeypatch.setattr(
        magemin,
        '_MAGEMIN_SUBPROCESS_LOCK',
        lock_dir / magemin._MAGEMIN_SUBPROCESS_LOCK.name,
    )
    return lock_dir
