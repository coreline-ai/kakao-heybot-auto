// 베스포크 씬 POC — "SIDE BY SIDE": 세 에이전트가 각자의 워크트리에서 동시에 코딩하는 3분할 레이스.
// 카탈로그 위젯이 아니라 이 영상만을 위한 일회성 씬 컴포넌트 (아키텍처 피벗 증명, 2026-07-19).
// 인프라(토큰·hash01·플래시 커브)는 재사용하되, 안무는 전부 이 씬 전용으로 손으로 깎는다.
import React from "react";
import { AbsoluteFill, interpolate } from "remotion";
import { hash01 } from "../shared";
import { flashOpacity } from "../scene/transitions";
import { P, labelStyle, promoEase } from "../tokens";

type Agent = {
  name: string;
  worktree: string;
  accent: string;
  cadence: number; // 프레임당 진행 속도 배율 — 에이전트마다 다른 리듬
  lines: string[];
  doneAt: number; // "PR READY" 착지 프레임
};

const AGENTS: Agent[] = [
  {
    name: "CLAUDE CODE",
    worktree: "wt/auth-refactor",
    accent: "#4a7fff",
    cadence: 1.0,
    doneAt: 132,
    lines: [
      "export async function rotateKeys() {",
      "  const grace = cfg.graceWindow;",
      "  await vault.rotate({ grace });",
      "  audit.log('keys rotated');",
      "}",
      "✓ 14 tests passed",
    ],
  },
  {
    name: "CODEX",
    worktree: "wt/payment-webhooks",
    accent: "#8f7bff",
    cadence: 0.82,
    doneAt: 148,
    lines: [
      "def verify(sig, payload):",
      "    mac = hmac.new(SECRET,",
      "        payload, sha256)",
      "    return compare(mac, sig)",
      "",
      "✓ webhook replay guarded",
    ],
  },
  {
    name: "CURSOR CLI",
    worktree: "wt/i18n-extraction",
    accent: "#3fbf8f",
    cadence: 0.9,
    doneAt: 141,
    lines: [
      "const keys = extract(src, {",
      "  locales: ['ko', 'en', 'ja'],",
      "  fallback: 'en',",
      "});",
      "write('locales/ko.json');",
      "✓ 312 strings extracted",
    ],
  },
];

const CHARS_PER_FRAME = 1.6;

// 페인 하나 — 코드가 실제 타이핑 리듬으로 흐른다 (라인별 임계 프레임 + 라인 내 문자 타이핑)
const Pane: React.FC<{ agent: Agent; index: number; frame: number }> = ({ agent, index, frame }) => {
  const enter = interpolate(frame, [6 + index * 5, 18 + index * 5], [0, 1], promoEase);
  const lift = (1 - enter) * 26;
  const typedTotal = Math.max(0, (frame - 20) * CHARS_PER_FRAME * agent.cadence);
  let budget = typedTotal;
  const done = frame >= agent.doneAt;
  const stampScale = interpolate(frame, [agent.doneAt, agent.doneAt + 5, agent.doneAt + 9], [1.7, 0.94, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        borderRadius: P.radiusMd,
        border: `1px solid ${done ? agent.accent : P.line}`,
        background: "rgba(8,12,24,0.72)",
        overflow: "hidden",
        opacity: enter,
        transform: `translateY(${lift}px)`,
        boxShadow: done ? `0 0 26px ${agent.accent}33` : "0 18px 40px rgba(0,0,0,0.4)",
      }}
    >
      {/* 페인 헤더 — 에이전트명 + 워크트리 브랜치 */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderBottom: `1px solid ${P.line}` }}>
        <span style={{ ...labelStyle(11), color: agent.accent }}>● {agent.name}</span>
        <span style={{ ...labelStyle(9), color: P.faint }}>⎇ {agent.worktree}</span>
      </div>
      {/* 코드 스트림 */}
      <div style={{ flex: 1, padding: "14px 16px", fontFamily: P.fontLabel, fontSize: 15, lineHeight: 1.75, color: P.muted }}>
        {agent.lines.map((line, li) => {
          if (budget <= 0) return null;
          const shown = line.slice(0, Math.floor(budget));
          budget -= line.length + 6; // 줄 전환 호흡
          const isResult = line.startsWith("✓");
          const active = shown.length < line.length;
          return (
            <div key={li} style={{ whiteSpace: "pre", color: isResult ? agent.accent : P.muted, fontWeight: isResult ? 700 : 400 }}>
              {shown}
              {active && <span style={{ opacity: frame % 24 < 14 ? 1 : 0, color: agent.accent }}>▌</span>}
            </div>
          );
        })}
      </div>
      {/* 하단 상태줄 — 진행 점 애니메이션 → PR READY 스탬프 착지 */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 16px", borderTop: `1px solid ${P.line}`, minHeight: 44 }}>
        {!done ? (
          <span style={{ ...labelStyle(10), color: P.faint }}>
            WORKING{".".repeat(1 + (Math.floor(frame / 9 + index) % 3))}
          </span>
        ) : (
          <span
            style={{
              ...labelStyle(10),
              color: "#0a0e1a",
              background: agent.accent,
              borderRadius: P.radiusSm,
              padding: "4px 10px",
              transform: `scale(${stampScale})`,
              transformOrigin: "left center",
              display: "inline-block",
            }}
          >
            ✓ PR READY
          </span>
        )}
        <span style={{ ...labelStyle(9), color: P.faint }}>
          +{Math.min(99, Math.floor((frame - 14) * 0.55 * agent.cadence))} lines
        </span>
      </div>
    </div>
  );
};

// 씬 본체 — 20초(600f) 단독 데모: 도입 4s → 레이스 12s → 세 스탬프 착지 → 결구 4s
export const OrcaTriplePane: React.FC<{ frame: number }> = ({ frame }) => {
  // 마지막 스탬프(148f) 순간의 단 한 번의 플래시 — 절제 원칙
  const veil = flashOpacity(frame, 150);
  const settleZoom = interpolate(frame, [0, 600], [1, 1.05], promoEase); // 느린 단일 카메라
  return (
    <AbsoluteFill style={{ background: P.bg }}>
      <AbsoluteFill style={{ transform: `scale(${settleZoom})` }}>
        {/* 무대 — 미세 그리드 + 각 페인 아래 accent 워시 (씬 전용) */}
        <AbsoluteFill
          style={{
            backgroundImage: `linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)`,
            backgroundSize: "64px 64px",
          }}
        />
        {AGENTS.map((a, i) => (
          <div
            key={a.name}
            style={{
              position: "absolute",
              left: `${8 + i * 29.5}%`,
              top: "30%",
              width: "26%",
              height: "50%",
              background: `radial-gradient(ellipse at 50% 100%, ${a.accent}14, transparent 70%)`,
            }}
          />
        ))}
        {/* 배경 부유 파티클 (결정적) */}
        {Array.from({ length: 26 }, (_, i) => (
          <span
            key={i}
            style={{
              position: "absolute",
              left: `${hash01(i * 3) * 100}%`,
              top: `${((hash01(i * 3 + 1) * 120 - frame * (0.02 + hash01(i * 3 + 2) * 0.03)) % 120 + 120) % 120 - 10}%`,
              width: 2,
              height: 2,
              borderRadius: 999,
              background: P.data,
              opacity: 0.15 + hash01(i * 7) * 0.2,
            }}
          />
        ))}
        {/* 3분할 페인 */}
        <div style={{ position: "absolute", left: 110, right: 110, top: 200, bottom: 200, display: "flex", gap: 28 }}>
          {AGENTS.map((a, i) => (
            <Pane key={a.name} agent={a} index={i} frame={frame} />
          ))}
        </div>
        {/* 상단 타이틀 행 — 씬 전용 배치 */}
        <div style={{ position: "absolute", left: 110, top: 96, display: "flex", alignItems: "baseline", gap: 18 }}>
          <span style={{ fontFamily: P.fontHero, fontWeight: 900, fontSize: 54, color: P.text }}>SIDE BY SIDE</span>
          <span style={{ ...labelStyle(12), color: P.faint }}>ONE REPO · THREE WORKTREES</span>
        </div>
        {/* 결구 — 세 스탬프가 모두 착지한 뒤 우하단 요약 */}
        <div
          style={{
            position: "absolute",
            right: 110,
            top: 108,
            ...labelStyle(12),
            color: P.data,
            opacity: interpolate(frame, [156, 170], [0, 1], promoEase),
          }}
        >
          3 PRs · 0 CONFLICTS
        </div>
      </AbsoluteFill>
      {/* 자막 */}
      <div style={{ position: "absolute", left: 0, right: 0, bottom: "4.5%", textAlign: "center", fontFamily: P.fontValue, fontSize: 26, fontWeight: 700, color: P.text }}>
        {frame < 150 ? "세 에이전트가, 지금 같은 레포에서 일하고 있습니다" : "충돌 없이 — 세 개의 PR"}
      </div>
      <AbsoluteFill style={{ background: P.veil, opacity: veil, pointerEvents: "none" }} />
    </AbsoluteFill>
  );
};
