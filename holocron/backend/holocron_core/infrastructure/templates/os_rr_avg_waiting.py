import random
from fractions import Fraction

TOPIC_KEY = "os.cpu_sched.rr.avg_waiting_time"
UNIT = "ms"
DECIMALS = 2
MIN_OPTION_GAP = Fraction(1, 4)
MAX_TIMELINE = 40

# Chỉ dùng kiểu builtin: sandbox không cho import typing và cấm tên `object`.
Params = dict[str, list[int] | list[str] | int]
# (first_start, completion, gantt, preempt_times, idle_gaps); -1 = chưa gán
Sim = tuple[list[int], list[int], list[list[int]], list[int], int]


def _ints(value: list[int] | list[str] | int) -> list[int]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, int)]
    return []


def _strs(value: list[int] | list[str] | int) -> list[str]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, str)]
    return []


def _int(value: list[int] | list[str] | int) -> int:
    if isinstance(value, int):
        return value
    return 0


def generate_params(seed: int) -> Params:
    rng = random.Random(seed)
    n = rng.randint(4, 5)
    quantum = rng.choice([2, 3, 4])
    arrivals = [0]
    for _ in range(n - 1):
        arrivals.append(arrivals[-1] + rng.randint(1, 3))
    bursts = [rng.randint(2, 9) for _ in range(n)]
    names = ["P" + str(i + 1) for i in range(n)]
    return {"names": names, "arrivals": arrivals, "bursts": bursts, "quantum": quantum}


def _simulate_rr(arrivals: list[int], bursts: list[int], quantum: int) -> Sim:
    n = len(arrivals)
    order = sorted(range(n), key=lambda i: arrivals[i])
    remaining = list(bursts)
    first_start = [-1] * n
    completion = [-1] * n
    queue: list[int] = []
    gantt: list[list[int]] = []
    preempt_times: list[int] = []
    idle_gaps = 0
    t = 0
    nxt = 0
    done = 0
    while nxt < n and arrivals[order[nxt]] <= t:
        queue.append(order[nxt])
        nxt += 1
    while done < n:
        if not queue:
            idle_gaps += 1
            t = arrivals[order[nxt]]
            while nxt < n and arrivals[order[nxt]] <= t:
                queue.append(order[nxt])
                nxt += 1
            continue
        i = queue.pop(0)
        if first_start[i] < 0:
            first_start[i] = t
        run = min(quantum, remaining[i])
        gantt.append([i, t, t + run])
        t += run
        remaining[i] -= run
        while nxt < n and arrivals[order[nxt]] <= t:
            queue.append(order[nxt])
            nxt += 1
        if remaining[i] > 0:
            preempt_times.append(t)
            queue.append(i)
        else:
            completion[i] = t
            done += 1
    return first_start, completion, gantt, preempt_times, idle_gaps


def _fcfs_waits(arrivals: list[int], bursts: list[int]) -> list[int]:
    order = sorted(range(len(arrivals)), key=lambda i: arrivals[i])
    waits = [0] * len(arrivals)
    t = 0
    for i in order:
        t = max(t, arrivals[i])
        waits[i] = t - arrivals[i]
        t += bursts[i]
    return waits


def _metrics(params: Params) -> tuple[Sim, dict[str, list[int]], dict[str, Fraction]]:
    """Trả về (sim, các danh sách theo tiến trình, các giá trị trung bình)."""
    arrivals = _ints(params["arrivals"])
    bursts = _ints(params["bursts"])
    n = len(arrivals)
    sim = _simulate_rr(arrivals, bursts, _int(params["quantum"]))
    first_start, completion = sim[0], sim[1]
    turnaround = [completion[i] - arrivals[i] for i in range(n)]
    waiting = [turnaround[i] - bursts[i] for i in range(n)]
    response = [first_start[i] - arrivals[i] for i in range(n)]
    fcfs = _fcfs_waits(arrivals, bursts)
    per_process = {"turnaround": turnaround, "waiting": waiting, "response": response}
    averages = {
        "waiting": Fraction(sum(waiting), n),
        "turnaround": Fraction(sum(turnaround), n),
        "response": Fraction(sum(response), n),
        "fcfs_waiting": Fraction(sum(fcfs), n),
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
    q = _int(params["quantum"])
    n = len(arrivals)
    assert 4 <= n <= 5, "VALIDITY_PROCESS_COUNT"
    assert len(bursts) == n and len(names) == n, "VALIDITY_SHAPE"
    assert all(b >= 1 for b in bursts), "VALIDITY_POSITIVE_BURST"
    assert q in (2, 3, 4), "VALIDITY_QUANTUM"
    assert arrivals[0] == 0, "VALIDITY_START_AT_ZERO"
    assert len(set(arrivals)) == n, "TIE_SIMULTANEOUS_ARRIVAL"
    sim, per_process, averages = _metrics(params)
    completion, preempt_times, idle_gaps = sim[1], sim[3], sim[4]
    assert not (set(preempt_times) & set(arrivals)), "TIE_ARRIVAL_AT_PREEMPTION"
    assert idle_gaps == 0, "NONDEG_CPU_IDLE"
    assert len(preempt_times) >= 2, "NONDEG_TOO_FEW_PREEMPTIONS"
    waiting, response = per_process["waiting"], per_process["response"]
    assert any(waiting[i] != response[i] for i in range(n)), "NONDEG_NO_REWAIT"
    assert any(b > q for b in bursts) and any(b <= q for b in bursts), "NONDEG_QUANTUM_SPLIT"
    assert max(completion) <= MAX_TIMELINE, "NONDEG_TIMELINE_TOO_LONG"
    values = [
        _rounded(averages["waiting"]),
        _rounded(averages["fcfs_waiting"]),
        _rounded(averages["response"]),
        _rounded(averages["turnaround"]),
    ]
    assert len(set(values)) == 4, "DISTINCT_OPTION_VALUES"
    for a in range(4):
        for b in range(a + 1, 4):
            assert abs(values[a] - values[b]) >= MIN_OPTION_GAP, "DISTINCT_OPTION_GAP"


def solve(params: Params) -> dict[str, str | float | list[dict[str, str | int]]]:
    sim, per_process, averages = _metrics(params)
    names = _strs(params["names"])
    completion, gantt = sim[1], sim[2]
    turnaround, waiting = per_process["turnaround"], per_process["waiting"]
    steps = []
    for i in range(len(names)):
        steps.append(
            names[i] + ": CT=" + str(completion[i]) + ", TAT=" + str(turnaround[i]) + ", WT=" + str(waiting[i])
        )
    gantt_text = " | ".join(names[seg[0]] + "[" + str(seg[1]) + "-" + str(seg[2]) + "]" for seg in gantt)
    return {
        "gantt": [{"process": names[seg[0]], "start": seg[1], "end": seg[2]} for seg in gantt],
        "value": float(_rounded(averages["waiting"])),
        "exact": str(averages["waiting"]),
        "display": _fmt(averages["waiting"]),
        "explanation_vi": "Gantt: "
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
    n = len(_strs(params["names"]))
    return [
        {
            "misconception_code": "RR_TREATED_AS_FCFS",
            "value": float(_rounded(averages["fcfs_waiting"])),
            "display": _fmt(averages["fcfs_waiting"]),
            "rationale_vi": "Bỏ qua time quantum, cho mỗi tiến trình chạy hết burst theo thứ tự đến (FCFS): "
            + "tổng chờ = "
            + str(int(averages["fcfs_waiting"] * n))
            + ".",
        },
        {
            "misconception_code": "WAIT_EQUALS_FIRST_RESPONSE",
            "value": float(_rounded(averages["response"])),
            "display": _fmt(averages["response"]),
            "rationale_vi": "Chỉ tính thời gian chờ đến lần đầu được cấp CPU (response time), "
            + "bỏ qua các lần chờ lại sau khi bị ngắt: tổng = "
            + str(sum(per_process["response"]))
            + ".",
        },
        {
            "misconception_code": "WAIT_CONFUSED_WITH_TURNAROUND",
            "value": float(_rounded(averages["turnaround"])),
            "display": _fmt(averages["turnaround"]),
            "rationale_vi": "Lấy completion − arrival (turnaround) mà quên trừ burst time: tổng = "
            + str(sum(per_process["turnaround"]))
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
        "Cho các tiến trình sau được lập lịch trên một CPU bằng thuật toán **Round Robin** "
        + "với lượng tử thời gian $q = "
        + str(_int(params["quantum"]))
        + "$ ms. "
        + "Bỏ qua chi phí chuyển ngữ cảnh.\n\n"
        + table
        + "\n\n"
        + "Thời gian chờ trung bình $\\overline{WT} = \\frac{1}{n}\\sum_{i}(CT_i - AT_i - BT_i)$ là bao nhiêu?"
    )
