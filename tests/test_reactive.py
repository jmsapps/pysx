"""Reactive graph regression tests."""



from pysx.reactive import effect, signal


def test_basic_tracking() -> None:
    a = signal(1)
    seen: list[int] = []
    effect(lambda: seen.append(a()))
    assert seen == [1], seen
    a.set(2)
    assert seen == [1, 2], seen


def test_set_equal_is_noop() -> None:
    a = signal(1)
    runs: list[int] = []
    def body() -> None:
        a()
        runs.append(1)

    effect(body)
    assert len(runs) == 1
    a.set(1)
    assert len(runs) == 1, f"equal set re-ran the effect: {runs}"
    a.set(2)
    assert len(runs) == 2, runs


def test_dynamic_dependency_retracking() -> None:
    flag = signal(True)
    b = signal(10)
    runs: list[int | None] = []

    def body() -> None:
        runs.append(b() if flag() else None)

    effect(body)
    assert runs == [10], runs

    flag.set(False)                 # body no longer reads b
    assert runs == [10, None], runs

    b.set(99)                       # must NOT wake the effect
    assert runs == [10, None], f"stale subscription on b: {runs}"

    flag.set(True)                  # re-subscribes to b
    assert runs == [10, None, 99], runs
    b.set(100)
    assert runs == [10, None, 99, 100], runs


def test_dispose() -> None:
    a = signal(1)
    runs: list[int] = []

    def body() -> None:
        a()
        runs.append(1)

    e = effect(body)
    e.dispose()
    a.set(2)
    assert len(runs) == 1, runs
