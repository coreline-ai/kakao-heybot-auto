// 무대(Stage) 레이어 — 원본의 환경 깊이 재현: 글로우 펄스·파티클 드리프트·그리드·구체 북엔드·스포트라이트.
// 전부 프레임 결정적 (hash01 + sin). 위젯 뒤(z 0)에 깔린다.
import React from "react";
import { AbsoluteFill } from "remotion";
import type { StageSpec } from "../schema";
import { hash01 } from "../shared";
import { P } from "../tokens";

// 앰비언트 컬러 워시 — 프레임 분석: 순수 다크가 아니라 씬별 컬러가 얹힘 (아레나 레드/블루)
const Tint: React.FC<{ tint: StageSpec["tint"]; frame: number }> = ({ tint, frame }) => {
  if (tint === "none") return null;
  const pulse = 0.85 + 0.15 * Math.sin(frame * 0.035);
  const wash =
    tint === "warm"
      ? `radial-gradient(ellipse 55% 60% at 12% 18%, ${P.washWarmA}, transparent 65%), radial-gradient(ellipse 55% 60% at 88% 20%, ${P.washWarmB}, transparent 65%)`
      : `radial-gradient(ellipse 62% 48% at 50% 38%, ${P.wash}, transparent 70%)`;
  return <AbsoluteFill style={{ background: wash, opacity: pulse }} />;
};

const Glow: React.FC<{ frame: number }> = ({ frame }) => (
  <AbsoluteFill
    style={{
      background: `radial-gradient(ellipse 46% 36% at 50% 44%, ${P.wash}, transparent 72%)`,
      opacity: 0.8 + 0.2 * Math.sin(frame * 0.05),
    }}
  />
);

// 상승 드리프트 파티클 더스트 — 결정적 (i 해시 위치·속도·트윙클)
const ParticleDust: React.FC<{ frame: number }> = ({ frame }) => (
  <AbsoluteFill>
    {Array.from({ length: 46 }, (_, i) => {
      const x = hash01(i * 3) * 100;
      const speed = 0.028 + hash01(i * 3 + 1) * 0.05;
      const y = ((hash01(i * 3 + 2) * 120 - frame * speed) % 120 + 120) % 120 - 10;
      const tw = 0.25 + 0.5 * Math.abs(Math.sin(frame * 0.06 + i));
      const size = 1.5 + hash01(i * 7) * 2.5;
      return (
        <span
          key={i}
          style={{
            position: "absolute",
            left: `${x}%`,
            top: `${y}%`,
            width: size,
            height: size,
            borderRadius: 999,
            background: P.data,
            opacity: tw * 0.5,
          }}
        />
      );
    })}
  </AbsoluteFill>
);

// 풀프레임 미세 그리드 + 저속 패럴럭스 드리프트
const Grid: React.FC<{ frame: number }> = ({ frame }) => {
  const drift = (frame * 0.12) % 72;
  return (
    <AbsoluteFill
      style={{
        backgroundImage: `linear-gradient(rgba(255,255,255,0.045) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.045) 1px, transparent 1px)`,
        backgroundSize: "72px 72px",
        backgroundPosition: `${-drift}px ${-drift * 0.6}px`,
        opacity: 0.5,
        maskImage: "radial-gradient(ellipse 75% 65% at 50% 45%, black 30%, transparent 100%)",
        WebkitMaskImage: "radial-gradient(ellipse 75% 65% at 50% 45%, black 30%, transparent 100%)",
      }}
    />
  );
};

// 발광 구체 + 하단 파티클 아크 — 오프닝/엔딩 북엔드 (원본 씬 1·30)
const Orb: React.FC<{ frame: number }> = ({ frame }) => {
  const rise = Math.min(1, frame / 40);
  const cy = 30 - rise * 4;
  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          left: "50%",
          top: `${cy}%`,
          width: 150,
          height: 150,
          transform: "translate(-50%, -50%)",
          borderRadius: 999,
          background: "radial-gradient(circle at 42% 36%, #ffffff, #cfd8ea 55%, #8b98b6 100%)",
          boxShadow: "0 0 90px 24px rgba(190,205,240,0.35)",
        }}
      />
      {/* 파티클 아크 */}
      {Array.from({ length: 26 }, (_, i) => {
        const a = (i / 25) * Math.PI;
        const r = 30 + 6 * Math.sin(frame * 0.03 + i);
        const x = 50 + Math.cos(a) * r * 0.9;
        const y = 74 + Math.sin(a) * 8;
        return (
          <span
            key={i}
            style={{
              position: "absolute",
              left: `${x}%`,
              top: `${y}%`,
              width: 2.5,
              height: 2.5,
              borderRadius: 999,
              background: P.text,
              opacity: 0.2 + 0.3 * hash01(i),
            }}
          />
        );
      })}
    </AbsoluteFill>
  );
};

// 상단 스포트라이트 콘 — 아레나 (원본 콜로세움·무대 씬)
const Spotlight: React.FC<{ frame: number }> = ({ frame }) => (
  <AbsoluteFill>
    {[24, 50, 76].map((x, i) => (
      <div
        key={x}
        style={{
          position: "absolute",
          left: `${x + Math.sin(frame * 0.02 + i * 2) * 1.6}%`,
          top: -40,
          width: 340,
          height: 760,
          transform: "translateX(-50%)",
          background: `linear-gradient(180deg, ${P.spot}, transparent 78%)`,
          clipPath: "polygon(42% 0, 58% 0, 100% 100%, 0 100%)",
        }}
      />
    ))}
  </AbsoluteFill>
);

// 한지(韓紙) 섬유 질감 — 정적 종이 무대 (P6-B 감지금니). 섬유 대시 + 미세 반점, 결정적.
const Hanji: React.FC = () => (
  <AbsoluteFill>
    {/* 섬유결 — 교차 사선 해치 */}
    <AbsoluteFill
      style={{
        backgroundImage: `repeating-linear-gradient(84deg, transparent 0 11px, ${P.line} 11px 12px), repeating-linear-gradient(-6deg, transparent 0 17px, ${P.panel} 17px 18px)`,
        opacity: 0.5,
      }}
    />
    {/* 긴 섬유 가닥 */}
    {Array.from({ length: 34 }, (_, i) => {
      const x = hash01(i * 5) * 100;
      const y = hash01(i * 5 + 1) * 100;
      const len = 20 + hash01(i * 5 + 2) * 70;
      const rot = (hash01(i * 5 + 3) - 0.5) * 24;
      return (
        <span
          key={i}
          style={{
            position: "absolute",
            left: `${x}%`,
            top: `${y}%`,
            width: len,
            height: 1,
            background: P.faint,
            opacity: 0.12 + hash01(i * 5 + 4) * 0.1,
            transform: `rotate(${rot}deg)`,
          }}
        />
      );
    })}
    {/* 가장자리 비네트 — 종이 눌림 */}
    <AbsoluteFill style={{ background: "radial-gradient(ellipse 80% 72% at 50% 48%, transparent 60%, rgba(0,0,0,0.28) 100%)" }} />
  </AbsoluteFill>
);

// 먹/금니 번짐(潑墨) — 저주파로 자라며 맥동하는 얼룩. 플래시의 우리식 리듬 (P6-B).
const InkWash: React.FC<{ frame: number }> = ({ frame }) => (
  <AbsoluteFill>
    {Array.from({ length: 4 }, (_, i) => {
      const cx = 14 + hash01(i * 11) * 72;
      const cy = 16 + hash01(i * 11 + 1) * 62;
      const grow = Math.min(1, frame / (70 + i * 26));
      const pulse = 0.7 + 0.3 * Math.sin(frame * 0.045 + i * 1.9);
      const rx = (16 + hash01(i * 11 + 2) * 18) * grow;
      const ry = rx * (0.6 + hash01(i * 11 + 3) * 0.5);
      return (
        <div
          key={i}
          style={{
            position: "absolute",
            left: `${cx}%`,
            top: `${cy}%`,
            width: `${rx * 2}%`,
            height: `${ry * 2}%`,
            transform: "translate(-50%, -50%)",
            borderRadius: "50%",
            background: `radial-gradient(ellipse at center, ${P.wash}, transparent 70%)`,
            opacity: pulse,
          }}
        />
      );
    })}
  </AbsoluteFill>
);

// 저투명 워터마크 글리프 (원본 % 워터마크)
const Glyph: React.FC<{ glyph: string }> = ({ glyph }) => (
  <div
    style={{
      position: "absolute",
      left: "6%",
      bottom: "12%",
      fontFamily: P.fontHero,
      fontWeight: 900,
      fontSize: 260,
      color: "rgba(255,255,255,0.04)",
      lineHeight: 1,
    }}
  >
    {glyph}
  </div>
);

export const StageLayer: React.FC<{ stage: StageSpec; frame: number }> = ({ stage, frame }) => {
  const layers = (
    <>
      <Tint tint={stage.tint} frame={frame} />
      {stage.preset === "glow" && <Glow frame={frame} />}
      {stage.preset === "particle-dust" && (
        <>
          <Glow frame={frame} />
          <ParticleDust frame={frame} />
        </>
      )}
      {stage.preset === "grid" && <Grid frame={frame} />}
      {stage.preset === "orb" && <Orb frame={frame} />}
      {stage.preset === "spotlight" && <Spotlight frame={frame} />}
      {stage.preset === "hanji" && <Hanji />}
      {stage.preset === "ink-wash" && <InkWash frame={frame} />}
      {stage.glyph && <Glyph glyph={stage.glyph} />}
    </>
  );
  // intensity (P7): ≤1은 전체 감쇠, >1은 초과분만큼 겹쳐 그려 증폭
  return (
    <AbsoluteFill style={{ zIndex: 0 }}>
      <AbsoluteFill style={{ opacity: Math.min(1, stage.intensity) }}>{layers}</AbsoluteFill>
      {stage.intensity > 1 && <AbsoluteFill style={{ opacity: stage.intensity - 1 }}>{layers}</AbsoluteFill>}
    </AbsoluteFill>
  );
};
