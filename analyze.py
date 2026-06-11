#!/usr/bin/env python3
"""
output/ 를 읽고 마감 임박/놓쳤을 만한 항목 + 첨부 현황을 정리한다(휴리스틱).

- 메뉴 목록표(tables.json)와 글 상세(posts/*/content.txt)까지 모두 훑어 날짜를 찾아
  오늘 기준 지남/D-3/D-7/이후로 분류한다.
- 첨부파일(attachments.json)을 모아, 특히 '아직 못 받은' 첨부를 따로 표시한다.

주의: 규칙 기반이라 100%는 아닙니다. 진짜 더블체크는 output/ 를 Claude Code 에게 읽혀서 함께.
"""

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

RE_YMD = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
RE_MD = re.compile(r"(?<!\d)(\d{1,2})\s*[.\-/월]\s*(\d{1,2})(?!\s*[.\-/일\d])")
# ★ 표시용(신뢰도 ↑ 신호)
KEYWORDS = ("마감", "제출", "기한", "까지", "deadline", "due", "종료", "마침",
            "기말", "중간", "시험", "과제")
# 연도 없는 MM-DD를 '마감'으로 인정할 때 요구하는 단어
# (강의계획서 주차표의 'MM-DD ~ MM-DD 주별학습목표' 같은 일정 노이즈를 걸러냄)
MD_KEYWORDS = ("마감", "제출", "기한", "까지", "due", "deadline", "오후", "오전",
               "시험", "퀴즈", "과제")


def find_dates(text, today):
    """YMD(연도 명시)는 항상, MM-DD는 마감류 키워드가 있을 때만(현재 학기=올해로 가정)."""
    out = []
    for m in RE_YMD.finditer(text):
        y, mo, d = (int(x) for x in m.groups())
        try:
            out.append(date(y, mo, d))
        except ValueError:
            pass
    if any(k in text for k in MD_KEYWORDS):
        for m in RE_MD.finditer(RE_YMD.sub(" ", text)):
            mo, d = int(m.group(1)), int(m.group(2))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                try:
                    out.append(date(today.year, mo, d))
                except ValueError:
                    pass
    return out


def iter_lines(out_dir):
    """out_dir 전체(메뉴 목록 + 글 상세)의 content.txt 줄과 tables.json 행을 흘린다."""
    for ctxt in sorted(out_dir.rglob("content.txt")):
        label = str(ctxt.parent.relative_to(out_dir))
        for line in ctxt.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("===== FRAME:"):
                yield label, line
    for tj in sorted(out_dir.rglob("tables.json")):
        label = str(tj.parent.relative_to(out_dir))
        try:
            data = json.loads(tj.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for tbl in data:
            for row in tbl.get("rows", []):
                joined = " | ".join(c for c in row if c)
                if joined:
                    yield label, joined


def attachment_report(out_dir):
    """(전체 첨부수, 받은 수, 못 받은 [(label, filename)]) 반환."""
    total = downloaded = 0
    missing = []
    for aj in sorted(out_dir.rglob("attachments.json")):
        label = str(aj.parent.relative_to(out_dir))
        try:
            items = json.loads(aj.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for it in items:
            total += 1
            if it.get("downloaded"):
                downloaded += 1
            else:
                missing.append((label, it.get("filename") or it.get("token") or "?"))
    return total, downloaded, missing


def course_dirs(root: Path):
    """과목별 하위폴더(예: output/객체지향프로그래밍/) 구조면 [(과목명, dir), …],
    아니면 [(None, root)]. '_' 로 시작하는 폴더(_archive_* 등)는 분석에서 제외."""
    if (root / "_index.json").exists():            # 레거시 평면 구조
        return [(None, root)]
    subs = [d for d in sorted(root.iterdir())
            if d.is_dir() and not d.name.startswith("_") and (d / "_index.json").exists()]
    return [(d.name, d) for d in subs] if subs else [(None, root)]


def analyze_one(out_dir, today):
    """out_dir(과목 1개) 안의 마감/첨부 현황을 출력."""
    buckets = {"overdue": [], "d3": [], "d7": [], "later": []}
    seen = set()

    for label, text in iter_lines(out_dir):
        dates = find_dates(text, today)
        if not dates:
            continue
        has_kw = any(k in text for k in KEYWORDS)
        for dt in dates:
            key = (dt.isoformat(), " ".join(text.split())[:50])
            if key in seen:
                continue
            seen.add(key)
            delta = (dt - today).days
            item = (dt, delta, label, " ".join(text.split())[:110], has_kw)
            if delta < 0:
                if delta >= -14:
                    buckets["overdue"].append(item)
            elif delta <= 3:
                buckets["d3"].append(item)
            elif delta <= 7:
                buckets["d7"].append(item)
            else:
                buckets["later"].append(item)

    buckets["overdue"].sort(key=lambda x: x[0], reverse=True)  # 최근 지난 것부터
    for k in ("d3", "d7", "later"):
        buckets[k].sort(key=lambda x: x[0])

    # 인벤토리
    idx = out_dir / "_index.json"
    print("\n[ 수집된 메뉴 ]")
    if idx.exists():
        try:
            for s in json.loads(idx.read_text(encoding="utf-8")):
                print(f"  - {s['label']}  (글 {s.get('posts', 0)}개, 첨부 {s.get('attachments', 0)}개)")
        except json.JSONDecodeError:
            pass
    else:
        for d in sorted(p for p in out_dir.iterdir() if p.is_dir()):
            n_posts = len(list((d / "posts").glob("*"))) if (d / "posts").exists() else 0
            print(f"  - {d.name}  (글 {n_posts}개)")

    def show(title, items, mark, limit=None):
        print(f"\n[ {title} ]  ({len(items)}건)")
        if not items:
            print("  (없음)")
            return
        for dt, delta, label, snippet, has_kw in (items if limit is None else items[:limit]):
            rel = f"D{delta:+d}" if delta != 0 else "D-DAY"
            star = "★" if has_kw else " "
            print(f"  {mark}{star} {dt.isoformat()} ({rel}) [{label}] {snippet}")
        if limit is not None and len(items) > limit:
            print(f"  … 외 {len(items) - limit}건 (output 직접 확인)")

    show("🔴 마감 지남(최근 2주)", buckets["overdue"], "!")
    show("🟠 임박 D-3 이내", buckets["d3"], ">")
    show("🟡 이번 주 D-7 이내", buckets["d7"], ">")
    show("🟢 그 이후 예정", buckets["later"], " ", limit=20)

    # 첨부 현황
    total, downloaded, missing = attachment_report(out_dir)
    print(f"\n[ 첨부파일 ]  총 {total}개 중 {downloaded}개 저장됨")
    if missing:
        print(f"  ⚠ 아직 못 받은 첨부 {len(missing)}개 — 직접 확인 필요:")
        for label, name in missing[:30]:
            print(f"     · [{label}] {name}")
        if len(missing) > 30:
            print(f"     · … 외 {len(missing) - 30}개")


def analyze(out_dir=Path("output")):
    if not out_dir.exists() or not any(out_dir.iterdir()):
        print(f"'{out_dir}/' 가 비어 있습니다. 먼저 수집하세요: python collect.py --auto")
        return

    today = datetime.now().date()
    targets = course_dirs(out_dir)

    print("═" * 72)
    print(f"  e-campus 수집 분석  (오늘: {today.isoformat()})")
    if len(targets) > 1 or targets[0][0]:
        print(f"  과목 {len(targets)}개: " + ", ".join(lbl for lbl, _ in targets))
    print("═" * 72)

    for lbl, cdir in targets:
        if lbl:
            print("\n" + "▓" * 72)
            print(f"  ■ 과목: {lbl}")
            print("▓" * 72)
        analyze_one(cdir, today)

    print("\n" + "─" * 72)
    print("★ = '마감/제출/기한' 등 키워드가 함께 있는 줄(신뢰도 ↑)")
    print("규칙 기반 추정입니다. 정확한 더블체크는 output/ 를 Claude Code 에게 읽혀 확인하세요.")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output")
    analyze(target)
