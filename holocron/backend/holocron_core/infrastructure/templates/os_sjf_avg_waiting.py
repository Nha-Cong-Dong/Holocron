import random
from fractions import Fraction

TOPIC_KEY = "os.cpu_sched.sjf.avg_waiting_time"
UNIT = "ms"
DECIMALS = 2
MIN_OPTION_GAP = Fraction(1, 4)
MAX_TIMELINE = 40

# Chỉ dùng kiểu builtin: sandbox không cho import typing và cấm tên `object`.
Params = dict[str, list[int] | list[str]]
# (thứ tự chạy, thời điểm bắt đầu, thời điểm hoàn thành, số lần CPU rảnh, số lần hòa burst giữa các tiến trình sẵn sàng)
Sim = tuple[list[int], list[int], list[int], int, int]


def _ints(value: list[int] | list[str]) -> list[int]:
    return [x for x in value if isinstance(x, int)]


def _strs(value: list[int] | list[str]) -> list[str]:
    return [x for x in value if isinstance(x, str)]


def generate_params(seed: int) -> Params:
    rng = random.Random(seed)
    n = rng.randint(4, 5)
    arrivals = [0]
    for _ in range(n - 1):
        arrivals.append(arrivals[-1] + rng.randint(1, 3))
    bursts = [rng.randint(2, 9) for _ in range(n)]
    names = ["P" + str(i + 1) for i in range(n)]
    return {"names": names, "arrivals": arrivals, "bursts": bursts}


def _simulate_sjf(arrivals: list[int], bursts: list[int]) -> Sim:
    """SJF không ưu tiên. Hòa burst: chọn tiến trình đến sớm hơn, rồi theo thứ tự trong bảng."""
    n = len(arrivals)
    done = [False] * n
    start = [-1] * n
    completion = [-1] * n
    order: list[int] = []
    t = 0
    idle_gaps = 0
    ties = 0
    for _ in range(n):
        ready = [i for i in range(n) if not done[i] and arrivals[i] <= t]
        if not ready:
            idle_gaps += 1
            t = min(arrivals[i] for i in range(n) if not done[i])
            ready = [i for i in range(n) if not done[i] and arrivals[i] <= t]
        if len({bursts[i] for i in ready}) < len(ready):
            ties += 1
        shortest = min(bursts[i] for i in ready)
        i = min((k for k in ready if bursts[k] == shortest), key=lambda k: (arrivals[k], k))
        start[i] = t
        t += bursts[i]
        completion[i] = t
        done[i] = True
        order.append(i)
    return order, start, completion, idle_gaps, ties


def _srtf_waits(arrivals: list[int], bursts: list[int]) -> list[int]:
    """Sai lầm SJF_COMPUTED_AS_SRTF: cho phép ngắt, mỗi ms chọn tiến trình còn lại ngắn nhất."""
    n = len(arrivals)
    remaining = list(bursts)
    completion = [-1] * n
    t = 0
    finished = 0
    while finished < n:
        ready = [i for i in range(n) if remaining[i] > 0 and arrivals[i] <= t]
        if not ready:
            t += 1
            continue
        i = min(ready, key=lambda k: (remaining[k], arrivals[k], k))
        remaining[i] -= 1
        t += 1
        if remaining[i] == 0:
            completion[i] = t
            finished += 1
    return [completion[i] - arrivals[i] - bursts[i] for i in range(n)]


def _ignore_arrival_waits(bursts: list[int]) -> list[int]:
    """Sai lầm SJF_IGNORES_ARRIVAL: sắp theo burst như thể mọi tiến trình đến lúc 0; chờ = thời điểm bắt đầu."""
    waits = [0] * len(bursts)
    t = 0
    for i in sorted(range(len(bursts)), key=lambda k: (bursts[k], k)):
        waits[i] = t
        t += bursts[i]
    return waits


def _metrics(params: Params) -> tuple[Sim, dict[str, list[int]], dict[str, Fraction]]:
    """Trả về (sim, các danh sách theo tiến trình, các giá trị trung bình)."""
    arrivals = _ints(params["arrivals"])
    bursts = _ints(params["bursts"])
    n = len(arrivals)
    sim = _simulate_sjf(arrivals, bursts)
    start, completion = sim[1], sim[2]
    waiting = [start[i] - arrivals[i] for i in range(n)]
    turnaround = [completion[i] - arrivals[i] for i in range(n)]
    srtf = _srtf_waits(arrivals, bursts)
    ignore = _ignore_arrival_waits(bursts)
    per_process = {"waiting": waiting, "turnaround": turnaround, "srtf": srtf, "ignore": ignore}
    averages = {
        "waiting": Fraction(sum(waiting), n),
        "turnaround": Fraction(sum(turnaround), n),
        "srtf": Fraction(sum(srtf), n),
        "ignore": Fraction(sum(ignore), n),
    }
    return sim, per_process, averages


def _rounded(x: Fraction) -> Fraction:
    return round(x, DECIMALS)


def _fmt(x: Fraction) -> str:
    return format(float(_rounded(x)), "." + str(DECIMALS) + "f") + " " + UNIT


def validate(params: Params) -> None:
    arrivals = _ints(params["arrivals"])
    bursts = _ints(params["bursts"])
    names = _strs(params["names"])
    n = len(arrivals)
    assert 4 <= n <= 5, "VALIDITY_PROCESS_COUNT"
    assert len(bursts) == n and len(names) == n, "VALIDITY_SHAPE"
    assert all(b >= 1 for b in bursts), "VALIDITY_POSITIVE_BURST"
    assert arrivals[0] == 0, "VALIDITY_START_AT_ZERO"
    sim, _per_process, averages = _metrics(params)
    order, completion, idle_gaps, ties = sim[0], sim[2], sim[3], sim[4]
    assert ties == 0, "TIE_EQUAL_BURST_READY"
    assert idle_gaps == 0, "NONDEG_CPU_IDLE"
    assert order != sorted(range(n), key=lambda k: (arrivals[k], k)), "NONDEG_NO_OVERTAKE"
    assert order != sorted(range(n), key=lambda k: (bursts[k], k)), "NONDEG_ARRIVAL_ORDER_MATTERS"
    assert max(completion) <= MAX_TIMELINE, "NONDEG_TIMELINE_TOO_LONG"
    values = [
        _rounded(averages["waiting"]),
        _rounded(averages["turnaround"]),
        _rounded(averages["srtf"]),
        _rounded(averages["ignore"]),
    ]
    assert len(set(values)) == 4, "DISTINCT_OPTION_VALUES"
    for a in range(4):
        for b in range(a + 1, 4):
            assert abs(values[a] - values[b]) >= MIN_OPTION_GAP, "DISTINCT_OPTION_GAP"


def solve(params: Params) -> dict[str, str | float | list[dict[str, str | int]]]:
    sim, per_process, averages = _metrics(params)
    names = _strs(params["names"])
    arrivals = _ints(params["arrivals"])
    order, start, completion = sim[0], sim[1], sim[2]
    waiting = per_process["waiting"]
    gantt: list[dict[str, str | int]] = [{"process": names[i], "start": start[i], "end": completion[i]} for i in order]
    gantt_text = " | ".join(names[i] + "[" + str(start[i]) + "-" + str(completion[i]) + "]" for i in order)
    steps = [
        names[i] + ": WT = " + str(start[i]) + " - " + str(arrivals[i]) + " = " + str(waiting[i])
        for i in range(len(names))
    ]
    return {
        "gantt": gantt,
        "value": float(_rounded(averages["waiting"])),
        "exact": str(averages["waiting"]),
        "display": _fmt(averages["waiting"]),
        "explanation_vi": "Gantt (SJF không ưu tiên): "
        + gantt_text
        + ". "
        + "; ".join(steps)
        + ". WT trung bình = "
        + str(sum(waiting))
        + "/"
        + str(len(names))
        + " = "
        + _fmt(averages["waiting"])
        + ".",
    }


def make_distractors(
    params: Params, solution: dict[str, str | float | list[dict[str, str | int]]]
) -> list[dict[str, str | float]]:
    _, per_process, averages = _metrics(params)
    return [
        {
            "misconception_code": "WAIT_CONFUSED_WITH_TURNAROUND",
            "value": float(_rounded(averages["turnaround"])),
            "display": _fmt(averages["turnaround"]),
            "rationale_vi": "Lấy completion − arrival (turnaround) mà quên trừ burst time: tổng = "
            + str(sum(per_process["turnaround"]))
            + ".",
        },
        {
            "misconception_code": "SJF_IGNORES_ARRIVAL",
            "value": float(_rounded(averages["ignore"])),
            "display": _fmt(averages["ignore"]),
            "rationale_vi": "Sắp các tiến trình theo burst tăng dần như thể tất cả đến lúc 0, bỏ qua thời điểm đến: "
            + "tổng thời điểm bắt đầu = "
            + str(sum(per_process["ignore"]))
            + ".",
        },
        {
            "misconception_code": "SJF_COMPUTED_AS_SRTF",
            "value": float(_rounded(averages["srtf"])),
            "display": _fmt(averages["srtf"]),
            "rationale_vi": "Cho phép ngắt tiến trình đang chạy khi có tiến trình ngắn hơn đến (SRTF) "
            + "trong khi đề là SJF không ưu tiên: tổng = "
            + str(sum(per_process["srtf"]))
            + ".",
        },
    ]


def render_stem(params: Params) -> str:
    names = _strs(params["names"])
    arrivals = _ints(params["arrivals"])
    bursts = _ints(params["bursts"])
    rows = []
    for i in range(len(names)):
        rows.append("| " + names[i] + " | " + str(arrivals[i]) + " | " + str(bursts[i]) + " |")
    table = "| Tiến trình | Thời điểm đến (ms) | Burst time (ms) |\n|---|---|---|\n" + "\n".join(rows)
    return (
        "Cho các tiến trình sau được lập lịch trên một CPU bằng thuật toán **Shortest Job First** "
        + "không ưu tiên (non-preemptive). "
        + "Bỏ qua chi phí chuyển ngữ cảnh.\n\n"
        + table
        + "\n\nThời gian chờ trung bình $\\overline{WT}$ của các tiến trình là bao nhiêu?"
    )
