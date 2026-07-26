// 프로모 위젯 갤러리 — 등록 위젯 전량을 패밀리 페이지 단위로 렌더하는 시각 회귀 기준 컴포지션.
// 분석 문서의 고정 슬롯(키커 좌상 + 자막 중앙하단)을 함께 시연한다.
// 데모 값의 단일 근원은 assets/promo-widgets/catalog.json — 여기서 하드코딩하지 않는다.
// demo의 `page` 필드는 갤러리 배치 메타데이터로, PromoWidgetSchema parse 전에 분리한다.
import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import catalog from "../../assets/promo-widgets/catalog.json";
import { getPromoWidgetBody } from "./registry";
import { PAGE_FRAMES, PromoWidgetSchema, type PromoGalleryProps, type PromoWidget } from "./schema";
import { PromoShell } from "./shared";
import { P, labelStyle, themeStyle } from "./tokens";

// 카탈로그 demos 전량 → (page, widget) — 잘못된 demo는 여기서 즉시 실패
const demosWithPage = catalog.widgets.flatMap((entry) =>
  entry.demos.map((demo) => {
    const { page = 1, ...widget } = demo as { page?: number } & Record<string, unknown>;
    return { page, widget: PromoWidgetSchema.parse(widget) };
  }),
);

const pageCount = Math.max(...demosWithPage.map((d) => d.page));

// 페이지(1-기준) → 위젯 묶음. 갤러리 컴포지션 duration = pages.length × PAGE_FRAMES.
export const GALLERY_PAGES: PromoWidget[][] = Array.from({ length: pageCount }, (_, i) =>
  demosWithPage.filter((d) => d.page === i + 1).map((d) => d.widget),
);

// 전량 평탄 목록 (테스트·검증용)
export const GALLERY_DEMO_WIDGETS: PromoWidget[] = GALLERY_PAGES.flat();

export const PromoWidgetGallery: React.FC<PromoGalleryProps> = ({ theme, kicker, subtitle, pages }) => {
  const frame = useCurrentFrame();
  const pageIndex = Math.min(Math.floor(frame / PAGE_FRAMES), pages.length - 1);
  const localFrame = frame - pageIndex * PAGE_FRAMES;
  const widgets = pages[pageIndex] ?? [];
  return (
    <AbsoluteFill style={{ ...themeStyle(theme), background: P.bg }}>
      {/* 배경 레이디얼 글로우 — 히어로 깊이 레이어 */}
      <AbsoluteFill
        style={{ background: `radial-gradient(ellipse 60% 45% at 50% 40%, ${P.wash}, transparent 70%)` }}
      />
      {/* 고정 슬롯: 키커 좌상 + 페이지 표시 */}
      <div style={{ position: "absolute", left: "4%", top: "4.5%", ...labelStyle(15) }}>
        {kicker} · {pageIndex + 1}/{pages.length}
      </div>
      {/* 위젯 — 페이지 로컬 프레임으로 구동 */}
      {widgets.map((w, i) => {
        const Body = getPromoWidgetBody(w.type);
        return (
          <PromoShell key={`${w.type}-${i}`} widget={w} frame={localFrame}>
            <Body widget={w} frame={localFrame} />
          </PromoShell>
        );
      })}
      {/* 고정 슬롯: 자막 중앙하단 + 어둠 그라디언트 밴드 */}
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 0,
          height: "14%",
          background: "linear-gradient(180deg, transparent, rgba(0,0,0,0.55))",
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
        }}
      >
        {subtitle}
      </div>
    </AbsoluteFill>
  );
};
