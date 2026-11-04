"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useState } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import {
  aiRereadErrorInfo,
  blockTypeLabel,
  eligibleBlocks,
  gateReasonMessage,
  mismatchCellKey,
  mismatchCells,
  missedTokens,
  type PageBlockView,
  rereadReasons,
  SEND_DISCLOSURE_VI,
} from "@/lib/ai-reread";
import { api, toUiError } from "@/lib/api";
import type { PageView } from "@/lib/documents-data";
import type { UiError } from "@/lib/errors";
import type { AiRereadBlock, AiRereadProposal } from "@/types/holocron";

import styles from "./AiReread.module.css";
import vault from "./Vault.module.css";

interface AiRereadSectionProps {
  documentId: string;
  /** Trang đang hiển thị: gửi làm `expected_version` khi nhận chữ AI. */
  page: PageView;
  /** Có thao tác ghi khác đang chạy ở màn xem lại. */
  busy: boolean;
  /** Đã nhận chữ AI: backend trả trang mới (khối `vlm`, trang không tự xác nhận). */
  onAccepted: (updated: PageView) => void;
  onReload: () => void;
}

type Loadable<T> = { kind: "loading" } | { kind: "error" } | { kind: "ready"; value: T };

/**
 * "Đọc lại bằng AI" (SPEC §6.10a, ADR-009 mục 15): liệt kê khối của trang có cờ `table_unresolved`,
 * `code_layout_uncertain` hoặc `low_text_contrast`. Chỉ hiện khi có ít nhất một khối như vậy. Chữ AI chỉ là đề xuất đã
 * qua cổng của backend; giao diện không so lại, không tính lại, chỉ tô chỗ lệch backend chỉ ra. Ghi vào trang chỉ khi
 * người dùng bấm Nhận.
 */
export function AiRereadSection(props: AiRereadSectionProps) {
  // Đổi `key` để "Thử lại" tải lại danh sách khối từ đầu.
  const [attempt, setAttempt] = useState(0);
  return <AiRereadLoaded key={attempt} {...props} onRetry={() => setAttempt((value) => value + 1)} />;
}

function AiRereadLoaded({
  documentId,
  page,
  busy,
  onAccepted,
  onReload,
  onRetry,
}: AiRereadSectionProps & { onRetry: () => void }) {
  const [blocks, setBlocks] = useState<Loadable<PageBlockView[]>>({ kind: "loading" });
  const pageNumber = page.pageNumber;

  useEffect(() => {
    let current = true;
    api
      .getPageBlocks(documentId, pageNumber)
      .then((list) => {
        if (current) setBlocks({ kind: "ready", value: eligibleBlocks(list) });
      })
      .catch(() => {
        if (current) setBlocks({ kind: "error" });
      });
    return () => {
      current = false;
    };
  }, [documentId, pageNumber]);

  if (blocks.kind === "loading") return null;
  if (blocks.kind === "error") {
    return (
      <section className={vault.panel} aria-label="Đọc lại bằng AI">
        <p className={vault.muted}>Chưa tải được danh sách khối để đọc lại bằng AI.</p>
        <div className={vault.actions}>
          <button type="button" className={`${vault.secondary} ${vault.small}`} onClick={onRetry}>
            Thử lại
          </button>
        </div>
      </section>
    );
  }
  if (blocks.value.length === 0) return null;

  return (
    <section className={vault.panel} aria-labelledby="ai-reread-title">
      <h3 id="ai-reread-title">Đọc lại bằng AI</h3>
      <p className={styles.disclosure}>{SEND_DISCLOSURE_VI}</p>
      <ul className={styles.list}>
        {blocks.value.map((block) => (
          <RereadItem
            key={block.blockId}
            documentId={documentId}
            page={page}
            block={block}
            disabled={busy}
            onAccepted={onAccepted}
            onReload={onReload}
          />
        ))}
      </ul>
    </section>
  );
}

type Phase =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "proposal"; value: AiRereadProposal }
  | { kind: "accepting"; value: AiRereadProposal };

interface RereadItemProps {
  documentId: string;
  page: PageView;
  block: PageBlockView;
  disabled: boolean;
  onAccepted: (updated: PageView) => void;
  onReload: () => void;
}

function RereadItem({ documentId, page, block, disabled, onAccepted, onReload }: RereadItemProps) {
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [error, setError] = useState<UiError | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const pageNumber = page.pageNumber;

  async function reread(): Promise<void> {
    setPhase({ kind: "sending" });
    setError(null);
    setNote(null);
    try {
      const proposal = await api.requestAiReread(documentId, pageNumber, block.blockId);
      setPhase({ kind: "proposal", value: proposal });
    } catch (caught) {
      setError(toUiError(caught));
      setPhase({ kind: "idle" });
    }
  }

  async function accept(proposal: AiRereadProposal): Promise<void> {
    setPhase({ kind: "accepting", value: proposal });
    setError(null);
    try {
      const updated = await api.acceptAiReread(
        documentId,
        pageNumber,
        block.blockId,
        proposal.proposal_id,
        page.version,
      );
      onAccepted(updated);
    } catch (caught) {
      setError(toUiError(caught));
      setPhase({ kind: "idle" });
    }
  }

  async function discard(proposal: AiRereadProposal): Promise<void> {
    setError(null);
    try {
      await api.rejectAiReread(documentId, pageNumber, block.blockId, proposal.proposal_id);
      setNote("Đã bỏ đề xuất; trang giữ nguyên.");
      setPhase({ kind: "idle" });
    } catch (caught) {
      setError(toUiError(caught));
    }
  }

  const working = phase.kind === "sending" || phase.kind === "accepting";
  const proposal = phase.kind === "proposal" || phase.kind === "accepting" ? phase.value : null;
  const reasons = rereadReasons(block).join("; ");

  return (
    <li className={styles.item}>
      <div className={styles.itemHeader}>
        <div>
          <strong>{blockTypeLabel(block.type)}</strong>
          {reasons !== "" && <span className={styles.reasons}> — {reasons}</span>}
        </div>
        {proposal === null && (
          <button
            type="button"
            className={`${vault.secondary} ${vault.small}`}
            onClick={() => void reread()}
            disabled={disabled || working}
          >
            {phase.kind === "sending" ? "Đang gửi ảnh và chờ AI…" : "Đọc lại bằng AI"}
          </button>
        )}
      </div>
      {note !== null && <output className={vault.successNote}>{note}</output>}
      {error !== null && <RereadError error={error} onReread={() => void reread()} onReload={onReload} />}
      {proposal !== null && (
        <ProposalPreview
          documentId={documentId}
          block={block}
          proposal={proposal}
          busy={disabled || working}
          onAccept={() => void accept(proposal)}
          onDiscard={() => void discard(proposal)}
          onReread={() => void reread()}
        />
      )}
    </li>
  );
}

interface RereadErrorProps {
  error: UiError;
  onReread: () => void;
  onReload: () => void;
}

/** Lỗi 409/503 có mã đã biết: thông điệp cố định + đúng một việc làm tiếp; lỗi khác dùng thông báo lỗi chung. */
function RereadError({ error, onReread, onReload }: RereadErrorProps) {
  const info = aiRereadErrorInfo(error.code);
  if (info === null) return <ErrorNotice error={error} onRetry={onReread} onReload={onReload} />;
  return (
    <div className={vault.warningNote} role="alert">
      <p>{info.message}</p>
      <div className={vault.actions}>
        {info.next === "settings" && (
          <Link href="/settings" className={`${vault.primary} ${vault.small}`}>
            Mở Cài đặt
          </Link>
        )}
        {info.next === "reread" && (
          <button type="button" className={`${vault.secondary} ${vault.small}`} onClick={onReread}>
            Đọc lại bằng AI
          </button>
        )}
        {info.next === "reload" && (
          <button type="button" className={`${vault.secondary} ${vault.small}`} onClick={onReload}>
            Tải lại trang
          </button>
        )}
      </div>
    </div>
  );
}

interface ProposalPreviewProps {
  documentId: string;
  block: PageBlockView;
  proposal: AiRereadProposal;
  busy: boolean;
  onAccept: () => void;
  onDiscard: () => void;
  onReread: () => void;
}

function ProposalPreview({ documentId, block, proposal, busy, onAccept, onDiscard, onReread }: ProposalPreviewProps) {
  const proposed = proposal.status === "accepted_text" ? (proposal.blocks[0] ?? null) : null;
  // Chữ OCR hiện từ phản hồi của backend (độc lập, đã dùng để kiểm); nếu rỗng thì dùng chữ OCR sẵn có của khối.
  const ocrText = proposal.ocr_text !== "" ? proposal.ocr_text : (block.ocrText ?? "");

  if (proposed === null) {
    return (
      <div className={vault.form}>
        <p className={vault.warningNote} role="alert">
          Chưa nhận chữ AI: {gateReasonMessage(proposal.reason)}
        </p>
        <div className={styles.preview}>
          <Column title="Ảnh cắt">
            <CropImage documentId={documentId} cropRef={block.cropRef} />
          </Column>
          <Column title="Chữ OCR">
            <OcrText text={ocrText} />
          </Column>
        </div>
        <div className={vault.actions}>
          <button type="button" className={`${vault.secondary} ${vault.small}`} onClick={onReread} disabled={busy}>
            Đọc lại bằng AI
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={vault.form}>
      <p className={styles.disclosure}>
        Đề xuất của AI ({proposal.recipient_label}), đã qua bước kiểm của Holocron. Chưa ghi vào trang cho tới khi bạn
        bấm Nhận.
        {proposal.from_cache ? " Kết quả lấy từ lần đọc trước, không gửi ảnh lại." : ""}
      </p>
      <div className={styles.preview}>
        <Column title="Ảnh cắt">
          <CropImage documentId={documentId} cropRef={block.cropRef} />
        </Column>
        <Column title="Chữ OCR">
          <OcrText text={ocrText} />
        </Column>
        <Column title="Chữ AI đề xuất">
          <ProposedContent proposed={proposed} proposal={proposal} />
        </Column>
      </div>
      <div className={vault.actions}>
        <button type="button" className={`${vault.primary} ${vault.small}`} onClick={onAccept} disabled={busy}>
          Nhận chữ AI
        </button>
        <button type="button" className={`${vault.secondary} ${vault.small}`} onClick={onDiscard} disabled={busy}>
          Bỏ
        </button>
      </div>
    </div>
  );
}

function Column({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className={styles.column}>
      <h4 className={styles.columnTitle}>{title}</h4>
      {children}
    </div>
  );
}

function OcrText({ text }: { text: string }) {
  if (text === "") return <p className={vault.muted}>Không có chữ OCR của vùng này.</p>;
  return <pre className={styles.text}>{text}</pre>;
}

/** Chữ AI đã qua cổng: bảng theo `rows` (ô lệch do backend chỉ ra được tô), còn lại là chữ thuần. */
function ProposedContent({ proposed, proposal }: { proposed: AiRereadBlock; proposal: AiRereadProposal }) {
  const missed = missedTokens(proposal.mismatches);
  if (proposed.rows === null) {
    return <pre className={styles.text}>{proposed.text ?? ""}</pre>;
  }
  const cells = mismatchCells(proposal.mismatches);
  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <tbody>
            {proposed.rows.map((row, rowIndex) => (
              // Hàng và ô của bảng đề xuất không có id riêng; vị trí là danh tính ổn định của bản xem trước chỉ đọc.
              <tr key={`row-${rowIndex.toString()}`}>
                {row.map((cell, colIndex) => (
                  <td
                    key={`cell-${rowIndex.toString()}-${colIndex.toString()}`}
                    className={cells.has(mismatchCellKey(rowIndex, colIndex)) ? styles.mismatch : undefined}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {cells.size > 0 && (
        <p className={vault.warningNote}>Ô gạch chân đỏ có số hoặc mã không khớp chữ OCR. Kiểm lại với ảnh cắt.</p>
      )}
      {missed.length > 0 && (
        <>
          <p className={vault.warningNote}>AI bỏ sót các số hoặc mã OCR đọc được:</p>
          <ul className={styles.missed}>
            {missed.map((token, index) => (
              <li key={`${index.toString()}-${token}`}>{token}</li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

/** Ảnh cắt tải qua `lib/api.ts` (header xác thực) thành object URL, thu hồi khi gỡ. Thiếu hoặc lỗi thì nói rõ. */
function CropImage({ documentId, cropRef }: { documentId: string; cropRef: string | null }) {
  const [state, setState] = useState<{ kind: "loading" } | { kind: "error" } | { kind: "ready"; url: string }>({
    kind: "loading",
  });

  useEffect(() => {
    if (cropRef === null) return;
    let current = true;
    let created: string | null = null;
    api
      .getCropImage(documentId, cropRef)
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        if (current) {
          created = url;
          setState({ kind: "ready", url });
        } else {
          URL.revokeObjectURL(url);
        }
      })
      .catch(() => {
        if (current) setState({ kind: "error" });
      });
    return () => {
      current = false;
      if (created !== null) URL.revokeObjectURL(created);
    };
  }, [documentId, cropRef]);

  if (cropRef === null) return <p className={vault.muted}>Khối này chưa có ảnh cắt.</p>;
  if (state.kind === "loading") return <p className={vault.muted}>Đang tải ảnh cắt…</p>;
  if (state.kind === "error") return <p className={vault.warningNote}>Chưa tải được ảnh cắt.</p>;
  return <img className={styles.crop} src={state.url} alt="Ảnh cắt của khối đang đọc lại" />;
}
