"""Plain-assert tests; the project has no test dependency."""

import sys

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0] + "/src")

from pysx.reactive import effect, signal  # noqa: E402


def test_basic_tracking():
    a = signal(1)
    seen = []
    effect(lambda: seen.append(a()))
    assert seen == [1], seen
    a.set(2)
    assert seen == [1, 2], seen


def test_set_equal_is_noop():
    a = signal(1)
    runs = []
    effect(lambda: (a(), runs.append(1)))
    assert len(runs) == 1
    a.set(1)
    assert len(runs) == 1, f"equal set re-ran the effect: {runs}"
    a.set(2)
    assert len(runs) == 2, runs


def test_dynamic_dependency_retracking():
    flag = signal(True)
    b = signal(10)
    runs = []

    def body():
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


def test_dispose():
    a = signal(1)
    runs = []
    e = effect(lambda: (a(), runs.append(1)))
    e.dispose()
    a.set(2)
    assert len(runs) == 1, runs


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"{len(fns)} passed")
