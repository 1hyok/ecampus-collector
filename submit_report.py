#!/usr/bin/env python
"""ilos 과제 제출 — 첨부 1개 + 짧은 본문.

제출 계약(3주차 과제 페이지에서 실측, 2026-09-12):
  첨부: POST /ilos/co/efile_upload_multiple2.acl  (plupload, path=K006, pf_st_flag=2)
        → FILE_SEQ 를 받아 화면의 [name=chk] 체크박스 id 로 꽂힌다
  제출: POST /ilos/st/course/report_insert.acl
        {ud, ky, RT_SEQ, JR_TXT, FILE_SEQS, EDITOR_SEQS, start, display, returnData:json}

업로드 응답 JSON 모양을 짐작해서 직접 POST 하지 않고, **실제 과제 페이지를 띄워
그 페이지의 JS(plupload · insertGo)를 그대로 태운다.** 사이트가 폼 필드를 바꿔도
같이 따라가고, FILE_SEQ 파싱을 내가 틀릴 여지가 없다.

제출 후에는 반드시 페이지를 다시 읽어 '제출일시' 와 첨부 파일명이 보이는지
확인한다 — 확인 못 하면 실패로 보고한다(조용히 성공했다고 하지 않는다).

    python submit_report.py --rt-seq 14428381 --file ~/school/.../week3_summary.docx \
        --text "3주차 요약서를 제출합니다." [--dry-run]
"""
import argparse, re, sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import collect, config

VIEW = "http://ecampus.konkuk.ac.kr/ilos/st/course/report_view_form.acl?RT_SEQ={seq}&SCH_KEY=&SCH_VALUE=&display=1&start=1"


def _login(page):
    uid, pw = collect.keychain_creds()
    if not uid:
        sys.exit("키체인 미등록 — ./ec login 으로 등록하세요.")
    page.goto(config.LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(800)
    if collect.is_logged_in(page):
        return uid
    page.fill("#usr_id", uid)
    page.fill("#usr_pwd", pw)
    try:
        page.check("#s_campus")
    except Exception:
        pass
    page.evaluate("() => loginForm()")
    if not collect.wait_logged_in(page, 15000):
        sys.exit("로그인 실패 — ./ec login 으로 재등록하세요.")
    return uid


def _course_frame(page):
    """과제 본문이 있는 frame(=insertGo 가 정의된 곳)을 찾는다."""
    for fr in page.frames:
        try:
            if fr.evaluate("() => typeof insertGo === 'function'"):
                return fr
        except Exception:
            continue
    return page.main_frame


def submit(rt_seq: str, file_path: Path, text: str, dry_run: bool,
           course: str = "") -> int:
    if not file_path.is_file():
        sys.exit(f"파일이 없습니다: {file_path}")
    # 교내 방화벽이 한글 파일명 업로드를 막는다 — 미리 걸러 조용한 실패를 막는다.
    if not re.fullmatch(r"[A-Za-z0-9._-]+", file_path.name):
        sys.exit(f"파일명에 영문·숫자 외 문자가 있습니다({file_path.name}). "
                 "교내 방화벽이 한글 파일명 업로드를 차단합니다.")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=".auth-submit", headless=True, accept_downloads=False)
        collect.force_korean(ctx)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        alerts = []
        page.on("dialog", lambda d: (alerts.append(d.message), d.accept()))
        _login(page)

        # 과목 강의실에 먼저 들어가야 report_view_form 이 열린다. 로그인만 하고 바로
        # 과제 URL 로 가면 ilos 가 main_form 으로 되돌려 보내고, 화면이 없으니 첨부
        # input 도 insertGo 도 없어 "input[type=file][multiple] 타임아웃" 으로 끝난다
        # (2026-09-16 3주차 제출에서 실측).
        if course and not collect.enter_course(page, course):
            ctx.close()
            sys.exit(f"과목 진입 실패: {course} — 과목명 키워드를 확인하세요.")

        page.goto(VIEW.format(seq=rt_seq), wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        if "report_view_form" not in (page.url or ""):
            ctx.close()
            sys.exit(f"과제 화면이 열리지 않았습니다(현재 {page.url}). "
                     "과목 진입이 풀렸는지 확인하세요.")
        fr = _course_frame(page)

        body = fr.locator("body").inner_text(timeout=8000)
        if "제출일시" in body:
            print("⚠ 이미 제출된 과제입니다. 덮어쓰지 않고 멈춥니다(수정은 화면에서).")
            ctx.close()
            return 2

        # 1) 첨부 — plupload 의 숨은 file input 에 직접 넣으면 페이지 JS 가 업로드한다
        finp = fr.locator("input[type=file][multiple]").first
        finp.set_input_files(str(file_path))
        seqs = ""
        for _ in range(60):                       # 업로드 완료 = [name=chk] 가 생김
            page.wait_for_timeout(500)
            seqs = fr.evaluate("() => typeof getFileSeqs === 'function' ? getFileSeqs() : ''")
            if seqs:
                break
        if not seqs:
            print("✗ 첨부 업로드가 끝나지 않았습니다(FILE_SEQS 비어 있음). 제출하지 않았습니다.")
            print("  alerts:", alerts)
            ctx.close()
            return 1
        print(f"✓ 첨부 업로드됨 — FILE_SEQS={seqs}")

        # 2) 본문 — insertGo 가 tinyMCE 에서 읽는다(비면 inputCheck 가 막는다)
        fr.evaluate("(t) => tinyMCE.get('JR_TXT').setContent('<p>' + t + '</p>')", text)

        if dry_run:
            print("--dry-run — 제출(insertGo)은 호출하지 않았습니다.")
            ctx.close()
            return 0

        # 3) 제출
        fr.evaluate("() => insertGo()")
        page.wait_for_timeout(4000)

        # 4) 검증 — 새로 읽어 '제출일시' 와 파일명이 보이는지 본다
        page.goto(VIEW.format(seq=rt_seq), wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        fr = _course_frame(page)
        body = fr.locator("body").inner_text(timeout=8000)
        ok_time = "제출일시" in body
        ok_file = file_path.name in body
        ctx.close()

    if ok_time and ok_file:
        m = re.search(r"제출일시\s*(\S+\s*\S*\s*\S*)", body)
        print(f"✓ 제출 확인됨 — {m.group(1) if m else ''} · {file_path.name}")
        return 0
    print(f"✗ 제출 확인 실패 (제출일시={ok_time}, 첨부={ok_file}). 화면에서 직접 확인하세요.")
    print("  alerts:", alerts)
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rt-seq", required=True, help="과제 RT_SEQ (과제 목록 링크에 있음)")
    ap.add_argument("--file", required=True, type=Path)
    ap.add_argument("--text", default="요약서를 제출합니다.")
    ap.add_argument("--dry-run", action="store_true",
                    help="첨부 업로드까지만 하고 제출은 안 함")
    ap.add_argument("--course", default=getattr(config, "AUTO_COURSE", "") or "",
                    help="먼저 들어갈 과목명 키워드(기본: config.AUTO_COURSE)")
    a = ap.parse_args()
    sys.exit(submit(a.rt_seq, a.file.expanduser(), a.text, a.dry_run, a.course))


if __name__ == "__main__":
    main()
