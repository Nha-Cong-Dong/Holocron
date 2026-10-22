"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { type FormEvent, useState } from "react";

import { TwinViewer } from "@/components/twins/TwinViewer";
import { createTwinBatch } from "@/lib/api";
import { clampTwinCount, DEFAULT_TWIN_COUNT, MAX_TWIN_COUNT, MIN_TWIN_COUNT } from "@/lib/twin";
import type { TwinBatch } from "@/types/holocron";

type BatchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; batch: TwinBatch };

export default function TwinBatchPage() {
  const params = useParams<{ blueprintId: string }>();
  const blueprintId = params.blueprintId;
  const [count, setCount] = useState(DEFAULT_TWIN_COUNT);
  // Một khoá cho mỗi lần mở trang: bấm lặp hoặc gửi lại cùng body trong 24 h nhận đúng batch cũ, không chạy lại sandbox.
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const [state, setState] = useState<BatchState>({ kind: "idle" });

  const generate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setState({ kind: "loading" });
    createTwinBatch(blueprintId, { count }, idempotencyKey)
      .then((batch) => setState({ kind: "ready", batch }))
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "Không sinh được Twin Questions." }),
      );
  };

  return (
    <main>
      <h1>Twin Questions cho blueprint {blueprintId}</h1>
      <form onSubmit={generate}>
        <label htmlFor="twin-count">Số Twin cho mỗi slot </label>
        <input
          id="twin-count"
          type="number"
          min={MIN_TWIN_COUNT}
          max={MAX_TWIN_COUNT}
          value={count}
          onChange={(event) => setCount(clampTwinCount(event.target.valueAsNumber))}
        />{" "}
        <button type="submit" disabled={state.kind === "loading"}>
          {state.kind === "loading" ? "Đang sinh…" : "Sinh Twin Questions"}
        </button>
      </form>

      {state.kind === "error" && <p role="alert">{state.message}</p>}
      {state.kind === "ready" && <TwinViewer batch={state.batch} />}

      <p>
        <Link href="/">Quay lại trang tải lên</Link>
      </p>
    </main>
  );
}
