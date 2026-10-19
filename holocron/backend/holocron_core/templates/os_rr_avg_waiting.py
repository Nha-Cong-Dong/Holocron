import random
from fractions import Fraction

TOPIC_KEY = "os.cpu_sched.rr.avg_waiting_time"
UNIT = "ms"
DECIMALS = 2
MIN_OPTION_GAP = Fraction(1, 4)
MAX_TIMELINE = 40


def generate_params(seed):
    rng = random.Random(seed)
    n = rng.randint(4, 5)
    quantum = rng.choice([2, 3, 4])
    arrivals = [0]
    for _ in range(n - 1):
        arrivals.append(arrivals[-1] + rng.randint(1, 3))
    bursts = [rng.randint(2, 9) for _ in range(n)]
    names = ["P" + str(i + 1) for i in range(n)]
    return {"names": names, "arrivals": arrivals, "bursts": bursts, "quantum": quantum}


def _simulate_rr(arrivals, bursts, quantum):
    n = len(arrivals)
    order = sorted(range(n), key=lambda i: arrivals[i])
    remaining = list(bursts)
    first_start = [None] * n
    completion = [None] * n
    queue = []
    gantt = []
    preempt_times = []
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
        if first_start[i] is None:
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
    return {
        "first_start": first_start,
        "completion": completion,
        "gantt": gantt,
        "preempt_times": preempt_times,
        "idle_gaps": idle_gaps,
    }


def _fcfs_waits(arrivals, bursts):
    order = sorted(range(len(arrivals)), key=lambda i: arrivals[i])
    waits = [0] * len(arrivals)
    t = 0
    for i in order:
        t = max(t, arrivals[i])
        waits[i] = t - arrivals[i]
        t += bursts[i]
    return waits


def _metrics(params):
    arrivals = params["arrivals"]
    bursts = params["bursts"]
    n = len(arrivals)
    sim = _simulate_rr(arrivals, bursts, params["quantum"])
    turnaround = [sim["completion"][i] - arrivals[i] for i in range(n)]
    waiting = [turnaround[i] - bursts[i] for i in range(n)]
    response = [sim["first_start"][i] - arrivals[i] for i in range(n)]
    fcfs = _fcfs_waits(arrivals, bursts)
    return {
        "sim": sim,
        "turnaround": turnaround,
        "waiting": waiting,
        "response": response,
        "avg_waiting": Fraction(sum(waiting), n),
        "avg_turnaround": Fraction(sum(turnaround), n),
        "avg_response": Fraction(sum(response), n),
        "avg_fcfs_waiting": Fraction(sum(fcfs), n),
    }


def _rounded(x):
    return round(x, DECIMALS)


def _fmt(x):
    return format(float(_rounded(x)), "." + str(DECIMALS) + "f") + " " + UNIT


def validate(params):
    arrivals = params["arrivals"]
    bursts = params["bursts"]
    q = params["quantum"]
    n = len(arrivals)
    assert 4 <= n <= 5, "VALIDITY_PROCESS_COUNT"
    assert len(bursts) == n and len(params["names"]) == n, "VALIDITY_SHAPE"
    assert all(b >= 1 for b in bursts), "VALIDITY_POSITIVE_BURST"
    assert q in (2, 3, 4), "VALIDITY_QUANTUM"
    assert arrivals[0] == 0, "VALIDITY_START_AT_ZERO"
    assert len(set(arrivals)) == n, "TIE_SIMULTANEOUS_ARRIVAL"
    m = _metrics(params)
    sim = m["sim"]
    assert not (set(sim["preempt_times"]) & set(arrivals)), "TIE_ARRIVAL_AT_PREEMPTION"
    assert sim["idle_gaps"] == 0, "NONDEG_CPU_IDLE"
    assert len(sim["preempt_times"]) >= 2, "NONDEG_TOO_FEW_PREEMPTIONS"
    assert any(m["waiting"][i] != m["response"][i] for i in range(n)), "NONDEG_NO_REWAIT"
    assert any(b > q for b in bursts) and any(b <= q for b in bursts), "NONDEG_QUANTUM_SPLIT"
    assert max(sim["completion"]) <= MAX_TIMELINE, "NONDEG_TIMELINE_TOO_LONG"
    values = [
        _rounded(m["avg_waiting"]),
        _rounded(m["avg_fcfs_waiting"]),
        _rounded(m["avg_response"]),
        _rounded(m["avg_turnaround"]),
    ]
    assert len(set(values)) == 4, "DISTINCT_OPTION_VALUES"
    for a in range(4):
        for b in range(a + 1, 4):
            assert abs(values[a] - values[b]) >= MIN_OPTION_GAP, "DISTINCT_OPTION_GAP"


def solve(params):
    m = _metrics(params)
    names = params["names"]
    steps = []
    for i in range(len(names)):
        steps.append(
            names[i] + ": CT=" + str(m["sim"]["completion"][i])
            + ", TAT=" + str(m["turnaround"][i])
            + ", WT=" + str(m["waiting"][i])
        )
    gantt_text = " | ".join(
        names[seg[0]] + "[" + str(seg[1]) + "-" + str(seg[2]) + "]" for seg in m["sim"]["gantt"]
    )
    return {
        "value": float(_rounded(m["avg_waiting"])),
        "exact": str(m["avg_waiting"]),
        "display": _fmt(m["avg_waiting"]),
        "explanation_vi": "Gantt: " + gantt_text + ". " + "; ".join(steps)
        + ". WT trung bình = " + str(sum(m["waiting"])) + "/" + str(len(names))
        + " = " + _fmt(m["avg_waiting"]) + ".",
    }


def make_distractors(params, solution):
    m = _metrics(params)
    n = len(params["names"])
    return [
        {
            "misconception_code": "RR_TREATED_AS_FCFS",
            "value": float(_rounded(m["avg_fcfs_waiting"])),
            "display": _fmt(m["avg_fcfs_waiting"]),
            "rationale_vi": "Bỏ qua time quantum, cho mỗi tiến trình chạy hết burst theo thứ tự đến (FCFS): "
            + "tổng chờ = " + str(int(m["avg_fcfs_waiting"] * n)) + ".",
        },
        {
            "misconception_code": "WAIT_EQUALS_FIRST_RESPONSE",
            "value": float(_rounded(m["avg_response"])),
            "display": _fmt(m["avg_response"]),
            "rationale_vi": "Chỉ tính thời gian chờ đến lần đầu được cấp CPU (response time), "
            + "bỏ qua các lần chờ lại sau khi bị ngắt: tổng = " + str(sum(m["response"])) + ".",
        },
        {
            "misconception_code": "WAIT_CONFUSED_WITH_TURNAROUND",
            "value": float(_rounded(m["avg_turnaround"])),
            "display": _fmt(m["avg_turnaround"]),
            "rationale_vi": "Lấy completion − arrival (turnaround) mà quên trừ burst time: tổng = "
            + str(sum(m["turnaround"])) + ".",
        },
    ]


def render_stem(params):
    rows = []
    for i in range(len(params["names"])):
        rows.append(
            "| " + params["names"][i] + " | " + str(params["arrivals"][i]) + " | " + str(params["bursts"][i]) + " |"
        )
    table = "| Tiến trình | Thời điểm đến (ms) | Burst time (ms) |\n|---|---|---|\n" + "\n".join(rows)
    return (
        "Cho các tiến trình sau được lập lịch trên một CPU bằng thuật toán **Round Robin** "
        + "với lượng tử thời gian $q = " + str(params["quantum"]) + "$ ms. "
        + "Bỏ qua chi phí chuyển ngữ cảnh.\n\n" + table + "\n\n"
        + "Thời gian chờ trung bình $\\overline{WT} = \\frac{1}{n}\\sum_{i}(CT_i - AT_i - BT_i)$ là bao nhiêu?"
    )
