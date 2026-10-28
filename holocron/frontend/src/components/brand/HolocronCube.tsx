"use client";
// Khối Holocron 3D (màn chào). Bấm / Enter / Space: tầng trên xoay thêm 90°.
// Dùng kèm styles/tokens.css + styles/motion.css. Không bắn hạt sao khi bấm (người dùng bỏ 2026-10-29).
import { useState } from "react";
import styles from "./HolocronCube.module.css";

const TOP = ["pnp", "pnn", "nnp", "nnn"] as const;
const BOTTOM = ["ppp", "ppn", "npp", "npn"] as const;

type CornerKey = (typeof TOP)[number] | (typeof BOTTOM)[number];

function Corner({ k }: { k: CornerKey }) {
  return (
    <span className={`${styles.pos} ${styles[k]}`}>
      <span className={`${styles.open} ${styles[`o${k}`]}`}>
        <span className={styles.cube}>
          <span className={`${styles.face} ${styles.front}`} />
          <span className={`${styles.face} ${styles.back}`} />
          <span className={`${styles.face} ${styles.right}`} />
          <span className={`${styles.face} ${styles.left}`} />
          <span className={`${styles.face} ${styles.ft} ${styles.up}`} />
          <span className={`${styles.face} ${styles.fb} ${styles.down}`} />
        </span>
      </span>
    </span>
  );
}

export function HolocronCube({ size = 220, label = "Xoay khối Holocron" }: { size?: number; label?: string }) {
  const [turns, setTurns] = useState(0);
  const scale = size / 220;
  return (
    <button
      type="button"
      className={styles.button}
      aria-label={label}
      title="Bấm để xoay"
      onClick={() => setTurns((t) => t + 1)}
      style={{ width: size, height: size }}
    >
      <span
        className={styles.stage}
        style={{ position: "absolute", left: 0, top: 0, transform: `scale(${scale})`, transformOrigin: "0 0" }}
      >
        <span className={styles.scale}>
          <span className={styles.spin}>
            <span className={styles.core}>
              <span className={styles.glow} />
              <span className={styles.ring} />
              <span className={`${styles.ring} ${styles.r2}`} />
              <span className={`${styles.ring} ${styles.r3}`} />
            </span>
            <span className={styles.layer} style={{ transform: `rotateY(${turns * 90}deg)` }}>
              <span className={`${styles.layerAnim} ${styles.top}`}>
                {TOP.map((k) => (
                  <Corner key={k} k={k} />
                ))}
              </span>
            </span>
            <span className={styles.layer}>
              <span className={styles.layerAnim}>
                {BOTTOM.map((k) => (
                  <Corner key={k} k={k} />
                ))}
              </span>
            </span>
          </span>
        </span>
      </span>
    </button>
  );
}
