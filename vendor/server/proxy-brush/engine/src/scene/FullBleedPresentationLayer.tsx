// 원본 프레임 진행/풀블리드 동화 장면 전용 레이어.
// route를 재생성하지 않아 누적 드로잉의 시간 흐름과 캐릭터 외형을 보존한다.
import React from "react";
import { AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import type { FullBleedPresentation } from "../schema";

const clamp = { extrapolateLeft: "clamp" as const, extrapolateRight: "clamp" as const };

type Props = {
  presentation: FullBleedPresentation;
  image: string;
  previousImage?: string;
  durationInFrames: number;
};

export const getFullBleedPresentationState = (
  frame: number,
  presentation: FullBleedPresentation,
  hasPreviousImage: boolean,
  durationInFrames: number,
) => {
  const dissolveFrames = presentation === "progressive-frame-sequence" ? 30 : 24;
  const incomingOpacity = hasPreviousImage ? interpolate(frame, [0, dissolveFrames], [0, 1], clamp) : 1;
  const progress = interpolate(frame, [0, Math.max(1, durationInFrames - 1)], [0, 1], clamp);
  const direction = presentation === "progressive-frame-sequence" ? 1 : -1;
  const scaleAmount = presentation === "progressive-frame-sequence" ? 0.045 : 0.055;
  const translateXAmount = direction * 1.6;
  const translateYAmount = direction === 1 ? -0.9 : 1.1;
  const transformAt = (cameraProgress: number) => (
    `scale(${1 + scaleAmount * cameraProgress}) translate(${translateXAmount * cameraProgress}%, ${translateYAmount * cameraProgress}%)`
  );
  // SceneSequence는 장면을 겹치지 않고 이어 붙인다. 다음 장면의 local f0에서
  // previousImage를 raw scale(1)로 놓으면 직전 f299의 Ken Burns terminal 위치에서
  // 한 프레임에 되돌아가 audit가 hard cut으로 판단한다. Dissolve 동안 두 bitmap에
  // 같은 camera transform을 적용해 previous terminal → current f(dissolveFrames)를
  // 연속 보간한다. 마지막 dissolve 프레임은 이후의 정상 current transform과 같다.
  const transitionProgress = hasPreviousImage && frame < dissolveFrames
    ? interpolate(
      frame,
      [0, dissolveFrames],
      [1, Math.min(1, dissolveFrames / Math.max(1, durationInFrames - 1))],
      {...clamp, easing: Easing.inOut(Easing.quad)},
    )
    : progress;
  const transform = transformAt(transitionProgress);
  return {
    dissolveFrames,
    incomingOpacity,
    transform,
    previousTransform: transform,
  };
};

export const FullBleedPresentationLayer: React.FC<Props> = ({
  presentation,
  image,
  previousImage,
  durationInFrames,
}) => {
  const frame = useCurrentFrame();
  // 첫 장면은 검은 화면에서 페이드하지 않는다. 이후 장면만 이전 원본 프레임에서
  // cross-dissolve해 시작/장면 경계 모두에서 불필요한 밝기 점프를 없앤다.
  const {incomingOpacity, transform, previousTransform} = getFullBleedPresentationState(
    frame,
    presentation,
    Boolean(previousImage),
    durationInFrames,
  );
  // 10초 동안 4.5~5.5%의 미세한 Ken Burns 이동은 느리게 보이면서도 정지 프레임
  // 판정 임계값 아래로 내려가지 않는다.

  return (
    <AbsoluteFill style={{ backgroundColor: "#090b10", overflow: "hidden", zIndex: 1 }}>
      {previousImage && (
        <Img
          src={staticFile(previousImage)}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            objectFit: "cover",
            transform: previousTransform,
            transformOrigin: "50% 50%",
          }}
        />
      )}
      <Img
        src={staticFile(image)}
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          objectFit: "cover",
          opacity: incomingOpacity,
          transform,
          transformOrigin: "50% 50%",
        }}
      />
    </AbsoluteFill>
  );
};
