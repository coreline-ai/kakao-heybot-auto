// 프로덕션 씬 시퀀서 — 가변 씬 길이 + 진입 전환 3종 + 무대 레이어 + 씬별 kicker/subtitle.
// 갤러리(시각 회귀용)와 분리된 P5 연출 계층. 전환 스펙은 원본 프레임 분석(transitions.ts) 기준.
import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { getPromoWidgetBody } from "../registry";
import type { PromoScene, PromoSceneProps } from "../schema";
import { PromoShell } from "../shared";
import { P, labelStyle, themeStyle } from "../tokens";
import { StageLayer } from "./Stages";
import { activeSceneIndex, cameraTransform, sceneFlash, sceneLayout, sweepX, transitionProgress } from "./transitions";

// 씬 콘텐츠 (무대 + 위젯 + 씬별 슬롯) — 전환 래퍼가 이걸 감싼다.
// 카메라(P7)는 무대+위젯에만 적용 — kicker/자막 크롬은 방송 그래픽처럼 고정.
const SceneContent: React.FC<{ scene: PromoScene; localFrame: number; kickerFallback: string }> = ({
  scene,
  localFrame,
  kickerFallback,
}) => {
  const kicker = scene.kicker ?? kickerFallback;
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{ transform: cameraTransform(scene.camera, localFrame, scene.durationInFrames) }}>
        <StageLayer stage={scene.stage} frame={localFrame} />
        {scene.widgets.map((w, i) => {
          const Body = getPromoWidgetBody(w.type);
          return (
            <PromoShell key={`${w.type}-${i}`} widget={w} frame={localFrame}>
              <Body widget={w} frame={localFrame} />
            </PromoShell>
          );
        })}
      </AbsoluteFill>
      {kicker && (
        <div style={{ position: "absolute", left: "4%", top: "4.5%", ...labelStyle(15), zIndex: 3 }}>{kicker}</div>
      )}
      {scene.subtitle && (
        <>
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              bottom: 0,
              height: "14%",
              background: "linear-gradient(180deg, transparent, rgba(0,0,0,0.55))",
              zIndex: 3,
            }}
          />
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              bottom: "4.5%",
              textAlign: "center",
              fontFamily: P.fontValue,
              fontSize: 26,
              fontWeight: 700,
              color: P.text,
              zIndex: 4,
            }}
          >
            {scene.subtitle}
          </div>
        </>
      )}
    </AbsoluteFill>
  );
};

export const PromoSceneSequence: React.FC<PromoSceneProps> = ({ theme, kicker, scenes }) => {
  const frame = useCurrentFrame();
  const layout = sceneLayout(scenes);
  const idx = activeSceneIndex(layout, frame);
  const scene = scenes[idx];
  const local = frame - layout[idx].start;
  const t = scene.transition;
  const inTransition = idx > 0 && t.type !== "none" && t.type !== "white-flash" && local < t.durationInFrames;
  const p = transitionProgress(layout[idx].start, frame, t.durationInFrames);
  const prev = idx > 0 ? scenes[idx - 1] : null;
  const prevLocal = prev ? frame - layout[idx - 1].start : 0; // 전환 중 이전 씬은 시간이 계속 흐른 상태로 물러난다

  return (
    <AbsoluteFill style={{ ...themeStyle(theme), background: P.bg }}>
      {/* light-sweep: 빔이 L→R 통과하며 새 씬이 빔 뒤에서 드러난다 (원본 와이프) */}
      {inTransition && t.type === "light-sweep" && prev && (
        <>
          <AbsoluteFill style={{ clipPath: `inset(0 0 0 ${sweepX(p) * 100}%)` }}>
            <SceneContent scene={prev} localFrame={prevLocal} kickerFallback={kicker} />
          </AbsoluteFill>
          <AbsoluteFill style={{ clipPath: `inset(0 ${(1 - sweepX(p)) * 100}% 0 0)` }}>
            <SceneContent scene={scene} localFrame={local} kickerFallback={kicker} />
          </AbsoluteFill>
          {/* 빔 본체 — 수직 발광 기둥 */}
          <div
            style={{
              position: "absolute",
              left: `${sweepX(p) * 100}%`,
              top: 0,
              bottom: 0,
              width: 8,
              transform: "translateX(-50%)",
              background: `linear-gradient(180deg, ${P.beamA}, ${P.beamB})`,
              boxShadow: `0 0 46px 16px ${P.glowSoft}`,
              zIndex: 10,
            }}
          />
        </>
      )}
      {/* push-in: 이전 씬 확대 퇴장 + 새 씬 축소 정착 */}
      {inTransition && t.type === "push-in" && prev && (
        <>
          <AbsoluteFill style={{ transform: `scale(${1 + p * 0.09})`, opacity: 1 - p }}>
            <SceneContent scene={prev} localFrame={prevLocal} kickerFallback={kicker} />
          </AbsoluteFill>
          <AbsoluteFill style={{ transform: `scale(${1.08 - p * 0.08})`, opacity: p }}>
            <SceneContent scene={scene} localFrame={local} kickerFallback={kicker} />
          </AbsoluteFill>
        </>
      )}
      {/* 하드컷 계열 (none / white-flash) 또는 전환 종료 후 */}
      {!inTransition && <SceneContent scene={scene} localFrame={local} kickerFallback={kicker} />}
      {/* 플래시 베일 — 진입 white-flash + 씬 내부 flashAt 비트 (전프레임 노출 리프트) */}
      <AbsoluteFill
        style={{
          background: P.veil,
          opacity: sceneFlash(scene, local),
          zIndex: 20,
          pointerEvents: "none",
        }}
      />
    </AbsoluteFill>
  );
};
