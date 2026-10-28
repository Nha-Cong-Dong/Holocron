"use client";
// Khối Holocron 3D (màn chào). Bấm / Enter / Space: tầng trên xoay thêm 90° và bắn hạt sao.
// Dùng kèm styles/tokens.css + styles/motion.css (class .m-burst).
import { useState } from "react";
import styles from "./HolocronCube.module.css";

const TOP = ["pnp", "pnn", "nnp", "nnn"] as const;
const BOTTOM = ["ppp", "ppn", "npp", "npn"] as const;
const RAYS = [0, 36, 72, 108, 144, 180, 216, 252, 288, 324];

type CornerKey = (typeof TOP)[number] | (typeof BOTTOM)[number];

function Corner({ k }: { k: CornerKey }) {
  return (
    <div className={`${styles.pos} ${styles[k]}`}>
      <div className={`${styles.open} ${styles[`o${k}`]}`}>
        <div className={styles.cube}>
          <div className={`${styles.face} ${styles.front}`} />
          <div className={`${styles.face} ${styles.back}`} />
          <div className={`${styles.face} ${styles.right}`} />
          <div className={`${styles.face} ${styles.left}`} />
          <div className={`${styles.face} ${styles.ft} ${styles.up}`} />
          <div className={`${styles.face} ${styles.fb} ${styles.down}`} />
        </div>
      </div>
    </div>
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
      <div
        className={styles.stage}
        style={{ position: "absolute", left: 0, top: 0, transform: `scale(${scale})`, transformOrigin: "0 0" }}
      >
        <div className={styles.scale}>
          <div className={styles.spin}>
            <div className={styles.core}>
              <div className={styles.glow} />
              <div className={styles.ring} />
              <div className={`${styles.ring} ${styles.r2}`} />
              <div className={`${styles.ring} ${styles.r3}`} />
            </div>
            <div className={styles.layer} style={{ transform: `rotateY(${turns * 90}deg)` }}>
              <div className={`${styles.layerAnim} ${styles.top}`}>
                {TOP.map((k) => (
                  <Corner key={k} k={k} />
                ))}
              </div>
            </div>
            <div className={styles.layer}>
              <div className={styles.layerAnim}>
                {BOTTOM.map((k) => (
                  <Corner key={k} k={k} />
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
      {turns > 0 && (
        <span key={turns} className="m-burst" aria-hidden="true">
          {RAYS.map((a) => (
            <span key={a} style={{ transform: `rotate(${a}deg)` }}>
              <i />
            </span>
          ))}
        </span>
      )}
    </button>
  );
}
