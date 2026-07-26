#!/usr/bin/env python3
"""공식 페이지에서 이미 내려받은 BGM과 라이선스 증빙을 로컬 등록/검증한다."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PY = REPO_ROOT / "pipeline" / ".venv" / "bin" / "python"
try:
    from brushvid.bgm import (BgmAssetError, REPO_ROOT, catalog_map, final_bgm_gate,
                              catalog_status, discover_downloads, import_asset, load_catalog,
                              record_listening_review, write_dashboard,
                              write_listening_review)
except ImportError:  # pragma: no cover
    if VENV_PY.is_file() and Path(sys.executable) != VENV_PY:
        import os
        os.execv(str(VENV_PY), [str(VENV_PY), *sys.argv])
    raise


def _print_status(rows: list[dict]) -> int:
    print(f"{'assetId':45} {'YouTube':8} {'audio':5} {'evidence':8} {'hash':5} {'meta':5} 상태")
    print("-" * 118)
    failed = 0
    for row in rows:
        failed += int(not row["ok"])
        checks = row.get("checks") or {}
        mark = lambda key: "PASS" if checks.get(key) else "FAIL"
        detail = "; ".join(row["errors"] + row["warnings"]) or "ready"
        youtube = "ALLOWED" if row.get("youtubeAllowed") else "BLOCKED"
        print(f"{row['id']:45} {youtube:8} {mark('audio'):5} {mark('evidence'):8} "
              f"{mark('hash'):5} {mark('metadata'):5} {detail}")
    youtube_allowed = sum(bool(row.get("youtubeAllowed")) for row in rows)
    print(f"\nready={len(rows) - failed}/{len(rows)} youtubeAllowed={youtube_allowed}/{len(rows)}")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="brushvid 로컬 BGM 에셋 관리")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="카탈로그 전체 후보의 다운로드·증빙·해시 상태")
    sub.add_parser("verify", help="status와 동일하게 검사하고 미완료면 exit 1")
    sources = sub.add_parser("sources", help="공식 청취·다운로드 페이지 목록")
    sources.add_argument("--json", action="store_true")
    dashboard = sub.add_parser("dashboard", help="진행도·공식 링크·로컬 청취 HTML 생성")
    dashboard.add_argument("--out", default=str(REPO_ROOT / "local-assets" / "bgm" / "index.html"))
    review = sub.add_parser("review", help="검증 영상·환경별 사람 청취 승인 HTML 생성")
    review.add_argument("--out", default=str(REPO_ROOT / "local-assets" / "bgm" / "listening-review.html"))
    review.add_argument("--import-result", help="페이지에서 내보낸 JSON을 최종 승인 근거로 검증·기록")
    gate = sub.add_parser("gate", help="카탈로그 전체·필수 E2E·라이선스·사람 청취 최종 완료 게이트")
    gate.add_argument("--out", default=str(REPO_ROOT / "local-assets" / "bgm" / "final-gate.json"))
    scan = sub.add_parser("scan", help="다운로드 폴더에서 공식 파일명과 일치하는 미등록 음원 탐색")
    scan.add_argument("--dir", action="append", dest="directories",
                      help="검색 폴더(반복 지정 가능, 기본: ~/Downloads)")
    scan.add_argument("--attach", action="store_true", help="정확히 1개 일치한 파일을 자동 등록")

    imp = sub.add_parser("import", help="이미 공식 다운로드한 MP3와 증빙 등록")
    imp.add_argument("--id", required=True, dest="asset_id")
    imp.add_argument("--file", required=True, dest="source_file")
    imp.add_argument("--artist", required=True)
    imp.add_argument("--content-id-status", required=True,
                     choices=("registered", "not-displayed", "unknown", "verified-not-registered"))
    imp.add_argument("--source-evidence", required=True)
    imp.add_argument("--license-evidence", required=True)
    imp.add_argument("--certificate")
    imp.add_argument("--downloaded-at")
    imp.add_argument("--checked-at")
    imp.add_argument("--replace", action="store_true")

    attach = sub.add_parser("attach", help="미리 캡처한 증빙에 공식 다운로드 MP3만 연결")
    attach.add_argument("--id", required=True, dest="asset_id")
    attach.add_argument("--file", required=True, dest="source_file")
    attach.add_argument("--replace", action="store_true")

    args = ap.parse_args()
    try:
        if args.command in ("status", "verify"):
            return _print_status(catalog_status())
        if args.command == "sources":
            assets = load_catalog()["assets"]
            if args.json:
                print(json.dumps([{"id": a["id"], "title": a["title"], "url": a["sourcePage"],
                                   "youtubeAllowed": a["youtubeAllowed"]}
                                  for a in assets], ensure_ascii=False, indent=2))
            else:
                for a in assets:
                    state = "downloaded" if a["downloaded"] else "pending"
                    youtube = "YT ALLOWED" if a["youtubeAllowed"] else "YT BLOCKED"
                    print(f"[{state:10}] [{youtube:10}] {a['id']}\n  {a['title']}\n  {a['sourcePage']}")
            return 0
        if args.command == "dashboard":
            out = write_dashboard(args.out)
            review_out = write_listening_review(Path(args.out).expanduser().resolve().parent / "listening-review.html")
            print(out)
            print(out.as_uri())
            print(f"review: {review_out.as_uri()}")
            return 0
        if args.command == "review":
            if args.import_result:
                approval = record_listening_review(args.import_result)
                print(json.dumps(approval, ensure_ascii=False, indent=2))
            out = write_listening_review(args.out)
            print(out)
            print(out.as_uri())
            return 0
        if args.command == "gate":
            result = final_bgm_gate(out_path=args.out)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["passed"] else 1
        if args.command == "scan":
            directories = args.directories or [str(Path.home() / "Downloads")]
            matches = discover_downloads(directories)
            by_id = catalog_map(load_catalog())
            attached = 0
            for item in matches:
                candidates = item["candidates"]
                state = "MATCH" if len(candidates) == 1 else ("AMBIGUOUS" if candidates else "WAIT")
                print(f"[{state:9}] {item['id']} ({item['sourceSlug']})")
                for candidate in candidates:
                    print(f"  {candidate}")
                if args.attach and len(candidates) == 1:
                    entry = by_id[item["id"]]
                    evidence_dir = REPO_ROOT / Path(entry["localPath"]).parent / "evidence"
                    import_asset(
                        item["id"], candidates[0], artist=entry["artist"] or "",
                        content_id_status=entry["license"]["contentIdStatus"],
                        source_evidence=evidence_dir / "source-page.png",
                        license_evidence=evidence_dir / "license.png",
                    )
                    attached += 1
            dashboard_path = write_dashboard()
            review_path = write_listening_review()
            print(f"\nmatched={sum(len(x['candidates']) == 1 for x in matches)} "
                  f"attached={attached} pending={len(matches) - attached}")
            print(f"dashboard: {dashboard_path.as_uri()}")
            print(f"review: {review_path.as_uri()}")
            return 0
        if args.command == "attach":
            entry = catalog_map(load_catalog()).get(args.asset_id)
            if entry is None:
                raise BgmAssetError(f"catalog에 없는 assetId: {args.asset_id}")
            evidence_dir = REPO_ROOT / Path(entry["localPath"]).parent / "evidence"
            page = evidence_dir / "source-page.png"
            license_file = evidence_dir / "license.png"
            result = import_asset(
                args.asset_id, args.source_file, artist=entry["artist"] or "",
                content_id_status=entry["license"]["contentIdStatus"],
                source_evidence=page, license_evidence=license_file, replace=args.replace,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print(f"dashboard: {write_dashboard().as_uri()}")
            print(f"review: {write_listening_review().as_uri()}")
            return 0
        result = import_asset(
            args.asset_id, args.source_file, artist=args.artist,
            content_id_status=args.content_id_status,
            source_evidence=args.source_evidence, license_evidence=args.license_evidence,
            certificate=args.certificate, downloaded_at=args.downloaded_at,
            checked_at=args.checked_at, replace=args.replace,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"dashboard: {write_dashboard().as_uri()}")
        print(f"review: {write_listening_review().as_uri()}")
        return 0
    except BgmAssetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
