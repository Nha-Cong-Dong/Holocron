"use client";

import { Modal } from "@/components/common/Modal";
import { SHORTCUT_HELP } from "@/lib/shortcuts";

import styles from "./ShortcutHelp.module.css";

interface ShortcutHelpProps {
  open: boolean;
  onClose: () => void;
}

/** Bảng phím tắt (phím `?`), đọc từ cùng bảng với `resolveShortcut`; `Esc` hoặc nút "Đóng" để đóng. */
export function ShortcutHelp({ open, onClose }: ShortcutHelpProps) {
  return (
    <Modal open={open} onClose={onClose} labelledBy="shortcut-help-title" width="wide">
      <div className={styles.help}>
        <div className={styles.header}>
          <h2 id="shortcut-help-title">Phím tắt</h2>
          <button type="button" className={styles.close} onClick={onClose}>
            Đóng <kbd>Esc</kbd>
          </button>
        </div>
        <p className={styles.note}>
          Phím một ký tự không có tác dụng khi đang gõ trong ô nhập. Phím của màn luyện tập chỉ dùng được trên màn đó.
        </p>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">Phím</th>
              <th scope="col">Khi nào</th>
              <th scope="col">Tác dụng</th>
            </tr>
          </thead>
          <tbody>
            {SHORTCUT_HELP.map((entry) => (
              <tr key={`${entry.keys.join("+")}:${entry.context}`}>
                <td className={styles.keys}>
                  {entry.keys.map((key, index) => (
                    <span key={key}>
                      {index > 0 && <span className={styles.or}> hoặc </span>}
                      <kbd>{key}</kbd>
                    </span>
                  ))}
                </td>
                <td>{entry.context}</td>
                <td>{entry.action}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Modal>
  );
}
