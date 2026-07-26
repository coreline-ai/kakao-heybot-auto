// 멀티씬 시퀀서 — scenes[]를 <Sequence>로 연결하고, 오디오는 씬 밖 공용 1트랙 (씬 경계에서 끊기지 않게).
// 씬별 값은 스키마 parse가 이미 기본값을 채웠으므로 여기서 fallback 캐스케이드를 두지 않는다.
import React from "react";
import { AbsoluteFill, Audio, Sequence, staticFile } from "remotion";
import type { RenderProps } from "../schema";
import { BrushScene } from "./BrushScene";

export const SceneSequence: React.FC<RenderProps> = ({ scenes, audio, paper, brush }) => {
  let cursor = 0;
  return (
    <AbsoluteFill style={{ backgroundColor: paper }}>
      {scenes.map((scene) => {
        const from = cursor;
        const duration = Math.max(1, Math.round(scene.durationInFrames));
        cursor += duration;
        return (
          <Sequence key={scene.id} from={from} durationInFrames={duration}>
            <BrushScene scene={scene} paper={paper} brush={brush} />
          </Sequence>
        );
      })}
      {audio && <Audio src={staticFile(audio)} />}
    </AbsoluteFill>
  );
};
