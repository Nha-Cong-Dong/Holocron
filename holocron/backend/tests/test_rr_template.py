from fractions import Fraction

from holocron_core.sandbox import SandboxStatus, run_template
from holocron_core.template_library import load_template_source

SRC = load_template_source("os_rr_avg_waiting.py")


def _reference_rr_avg_wait(arrivals, bursts, q):
    """Cài đặt tham chiếu độc lập để đối chiếu với solve() của template."""
    n = len(arrivals)
    rem, ready, t, done, ct = list(bursts), [], 0, 0, [0] * n
    pending = sorted(range(n), key=lambda i: arrivals[i])

    def admit(now):
        while pending and arrivals[pending[0]] <= now:
            ready.append(pending.pop(0))

    admit(0)
    while done < n:
        i = ready.pop(0)
        run = min(q, rem[i])
        t += run
        rem[i] -= run
        admit(t)
        if rem[i]:
            ready.append(i)
        else:
            ct[i], done = t, done + 1
    return Fraction(sum(ct[i] - arrivals[i] - bursts[i] for i in range(n)), n)


def test_known_case_seed_5():
    r = run_template(SRC, seed=5)
    assert r.status is SandboxStatus.OK
    assert r.params == {
        "names": ["P1", "P2", "P3", "P4", "P5"],
        "arrivals": [0, 2, 5, 8, 11],
        "bursts": [2, 9, 5, 2, 4],
        "quantum": 4,
    }
    assert r.solution["display"] == "6.00 ms"
    assert sorted(d["display"] for d in r.distractors) == ["10.40 ms", "2.60 ms", "4.20 ms"]


def test_determinism_same_seed_same_output():
    a, b = run_template(SRC, seed=777), run_template(SRC, seed=777)
    assert a.model_dump(exclude={"duration_ms"}) == b.model_dump(exclude={"duration_ms"})


def test_every_accepted_seed_is_correct_and_unambiguous():
    accepted = 0
    for seed in range(120):
        r = run_template(SRC, seed=seed)
        if r.status is not SandboxStatus.OK:
            assert r.status is SandboxStatus.ASSERTION_FAILED
            continue
        accepted += 1
        p = r.params
        expected = round(_reference_rr_avg_wait(p["arrivals"], p["bursts"], p["quantum"]), 2)
        assert r.solution["value"] == float(expected)
        values = [r.solution["value"]] + [d["value"] for d in r.distractors]
        assert len(set(values)) == 4
        assert min(abs(x - y) for i, x in enumerate(values) for y in values[i + 1 :]) >= 0.25
    assert accepted >= 15
