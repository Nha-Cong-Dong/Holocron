"use client";

import { HolocronCube } from "@/components/brand/HolocronCube";
import { Button } from "@/components/ui";

import styles from "./WelcomeScreen.module.css";

export interface WelcomeScreenProps {
  onAddSubject: () => void;
  /** Máy đang chạy hoàn toàn offline (không bật dịch vụ ngoài): mới được nói "không cần mạng". */
  offline: boolean;
}

/**
 * Màn chào (design-handoff/README.md màn 0), hiện khi chưa có môn nào: khối Holocron 3D, tiêu đề, đúng một nút chính
 * "Thêm môn đầu tiên". Không hiện chẩn đoán máy (đã chuyển sang Cài đặt › Máy của bạn).
 */
export function WelcomeScreen({ onAddSubject, offline }: WelcomeScreenProps) {
  return (
    <section className={`m-screen ${styles.welcome}`} aria-labelledby="welcome-title">
      <HolocronCube size={220} />
      <h1 id="welcome-title" className={styles.title}>
        Ôn thi bằng chính bài giảng của bạn
      </h1>
      <p className={styles.lead}>
        Thả file PDF vào, Holocron rút ra khái niệm và ra đề cho bạn luyện. Mỗi khái niệm bạn nắm được thành một ngôi
        sao, đáp án nào cũng chỉ rõ trang nguồn.
      </p>
      <Button variant="primary" className={styles.cta} onClick={onAddSubject}>
        Thêm môn đầu tiên
      </Button>
      {offline && <p className={styles.caption}>Chạy hoàn toàn trên máy bạn, không cần mạng.</p>}
    </section>
  );
}
