"use client";

import Image from "next/image";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";

import styles from "./Blocks.module.css";

interface CropImageProps {
  documentId: string;
  cropRef: string;
  alt: string;
}

type CropState = { kind: "loading" } | { kind: "ready"; url: string } | { kind: "error" };

/**
 * Ảnh cắt trang gốc. Tải qua `lib/api.ts` (header token) thành blob rồi object URL; không dùng `<img src>` trỏ thẳng
 * endpoint và không đưa token vào URL. Object URL thu hồi khi đổi ảnh hoặc gỡ component.
 */
export function CropImage({ documentId, cropRef, alt }: CropImageProps) {
  const [state, setState] = useState<CropState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setState({ kind: "loading" });
    api
      .getCropImage(documentId, cropRef)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ kind: "ready", url: objectUrl });
      })
      .catch(() => {
        if (!cancelled) setState({ kind: "error" });
      });
    return () => {
      cancelled = true;
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
  }, [documentId, cropRef]);

  if (state.kind === "loading") return <p className={styles.cropNote}>Đang tải ảnh trang gốc…</p>;
  if (state.kind === "error") return <p className={styles.cropNote}>Chưa tải được ảnh trang gốc.</p>;
  return (
    // `unoptimized`: ảnh là object URL cục bộ; chiều cao tự theo tỉ lệ thật của ảnh.
    <Image className={styles.crop} src={state.url} alt={alt} width={800} height={600} unoptimized />
  );
}
