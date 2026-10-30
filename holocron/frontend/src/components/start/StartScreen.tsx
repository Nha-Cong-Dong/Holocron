"use client";

import { useState } from "react";

import { AddSubjectDialog } from "@/components/home/AddSubjectDialog";
import { HomeScreen } from "@/components/home/HomeScreen";
import { WelcomeScreen } from "@/components/home/WelcomeScreen";
import { useShell } from "@/components/shell/ShellContext";
import { freshRuntime, selectDataEgress } from "@/lib/runtime";

import styles from "./StartScreen.module.css";

/**
 * Trang chủ `/`: danh sách môn rỗng ⇒ màn chào (màn 0); đang tải, lỗi hoặc có môn ⇒ "Bầu trời của bạn" (màn 1). Hộp
 * thoại Thêm môn (màn 2) dùng chung cho cả hai. Chẩn đoán máy đã chuyển sang Cài đặt › Máy của bạn.
 */
export function StartScreen() {
  const { subjects, runtime } = useShell();
  const [dialogOpen, setDialogOpen] = useState(false);
  // Mỗi lần mở là một hộp thoại mới (ô trống, không còn lỗi của lần trước).
  const [dialogKey, setDialogKey] = useState(0);

  function openDialog(): void {
    setDialogKey((key) => key + 1);
    setDialogOpen(true);
  }

  const existingCodes = subjects.kind === "ready" ? subjects.subjects.map((subject) => subject.subject_code) : [];
  const welcome = subjects.kind === "ready" && subjects.subjects.length === 0;

  return (
    <div className={styles.screen}>
      {welcome ? (
        <WelcomeScreen onAddSubject={openDialog} offline={selectDataEgress(freshRuntime(runtime)) === "local_only"} />
      ) : (
        <HomeScreen onAddSubject={openDialog} />
      )}
      <AddSubjectDialog
        key={dialogKey}
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        existingCodes={existingCodes}
      />
    </div>
  );
}
