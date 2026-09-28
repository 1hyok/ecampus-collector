#!/usr/bin/env python
"""ilos 알림/할일 폴링 — 전체 수집(./ec) 없이 '새 게시물이 떴는지'만 싸게 확인한다.

ilos 는 좌측 상단 배지로 알림(notification)과 할일(todo)을 준다:
  - /ilos/mp/notification_list.acl  새 과제·공지·강의자료·온라인강의 알림 목록(HTML)
  - /ilos/mp/todo_list.acl          미제출/미학습 할일 목록(POST, HTML 조각)
폴더를 diff 하는 것보다 정확하고(교수가 올린 시각이 찍힌다) 훨씬 싸다.

collect.py 와 달리 headless 로 돈다 — 자동으로 주기 실행되는 용도라
창이 떴다 사라지면 쓰는 사람이 방해받는다.

주의: ilos 는 중복 로그인 시 기존 세션을 끊는다. 사람이 eCampus 를 보고 있는
동안 이걸 돌리면 그 사람이 로그아웃된다. 자주 돌리지 말 것.

    python notify.py            # 사람이 읽는 형태
    python notify.py --json     # JSON (자동화용)
    python notify.py --course 컴퓨터공학세미나
"""
import argparse, json, re, sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import collect, config

NOTI_URL = "http://ecampus.konkuk.ac.kr/ilos/mp/notification_list.acl"
TODO_URL = "http://ecampus.konkuk.ac.kr/ilos/mp/todo_list.acl"
TODO_FORM_URL = "http://ecampus.konkuk.ac.kr/ilos/mp/todo_list_form.acl"


def _text(el) -> str:
    return re.sub(r"\s+", " ", el or "").strip()


def parse_notifications(body: str) -> list:
    """알림 목록 본문 텍스트를 [{date, course, kind, title, when}] 로 자른다.

    화면은 '날짜 헤더 → (과목 / 내용 / 상대시각) 3줄' 반복 구조다.
    """
    out, cur_date = [], ""
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.fullmatch(r"\d{4}\.\d{2}\.\d{2} \S요일", line):
            cur_date = line
            i += 1
            continue
        m = re.match(r"\[(.+?)\]\s*(.+)", line)          # [서울] 컴퓨터공학세미나(001)
        if m and i + 1 < len(lines) and lines[i + 1].startswith("["):
            course = m.group(2).strip()
            m2 = re.match(r"\[(.+?)\]\s*(.*)", lines[i + 1])   # [과제] 새로운 과제가...
            kind, title = (m2.group(1), m2.group(2).strip()) if m2 else ("", lines[i + 1])
            when = lines[i + 2] if i + 2 < len(lines) and not lines[i + 2].startswith("[") \
                                 and not re.fullmatch(r"\d{4}\.\d{2}\.\d{2} \S요일", lines[i + 2]) else ""
            out.append({"date": cur_date, "course": course, "kind": kind,
                        "title": title, "when": when})
            i += 3 if when else 2
            continue
        i += 1
    return out



TODO_KIND = {"report": "과제", "lecture_weeks": "온라인강의", "project": "팀프로젝트",
             "discuss": "토론", "test": "시험", "survey": "설문", "clicker": "투표"}


def parse_todos(html: str) -> list:
    """todo_list.acl 응답에서 [{kind, title, course, dday, due}] 를 뽑는다.

    항목은 <div class="todo_wrap ..."> 블록이고 그 안에
    todo_title / todo_subjt / todo_d_day / todo_date 로 나뉜다.
    """
    out = []
    for blk in re.findall(r'<div class="todo_wrap[^"]*"(.*?)(?=<div class="todo_wrap|\Z)', html, re.S):
        def pick(cls):
            # 클래스가 'todo_d_day site-color' 처럼 붙어 오므로 뒤에 더 와도 매치시킨다
            m = re.search(r'class="%s(?:\s[^"]*)?"[^>]*>(.*?)</' % cls, blk, re.S)
            return _text(re.sub(r"<[^>]+>", " ", m.group(1))) if m else ""
        title = pick("todo_title")
        if not title:
            continue
        m = re.match(r"\[(.+?)\]\s*(.*)", title)
        kind, name = (m.group(1), m.group(2).strip()) if m else ("", title)
        dates = re.findall(r"(\d{4}\.\d{2}\.\d{2}[^<]*)", blk)
        out.append({
            "kind": kind or TODO_KIND.get(pick("gubun"), ""),
            "title": name,
            "course": pick("todo_subjt"),
            "dday": pick("todo_d_day"),
            "due": _text(dates[-1]) if dates else "",
        })
    return out


def fetch(course_filter: str = "") -> dict:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(Path(".auth-notify")), headless=True,
            accept_downloads=False)
        collect.force_korean(ctx)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("dialog", lambda d: d.dismiss())
        uid, pw = collect.keychain_creds()
        if not uid:
            ctx.close()
            sys.exit("키체인 미등록 — ./ec login 으로 등록하세요.")
        page.goto(config.LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(800)
        if not collect.is_logged_in(page):
            page.fill("#usr_id", uid)
            page.fill("#usr_pwd", pw)
            try:
                page.check("#s_campus")
            except Exception:
                pass
            page.evaluate("() => loginForm()")
            if not collect.wait_logged_in(page, 15000):
                ctx.close()
                sys.exit("로그인 실패 — ./ec login 으로 재등록하세요.")

        page.goto(NOTI_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        notis = parse_notifications(page.locator("body").inner_text(timeout=8000))

        # 할일 목록은 todo_list_form.acl 을 띄워도 #todo_list 가 비어 오는 경우가 있어
        # (주입 시점이 어긋난다) ajax 엔드포인트를 직접 때린다.
        todo_html = page.evaluate("""async (url) => {
            const res = await fetch(url, {method:'POST',
                headers:{'Content-Type':'application/x-www-form-urlencoded'},
                body:'todoKjList=&chk_cate=ALL&encoding=utf-8'});
            return await res.text();
        }""", TODO_URL)
        ctx.close()

    todos = parse_todos(todo_html)

    if course_filter:
        notis = [n for n in notis if course_filter in n["course"]]
        todos = [t for t in todos if course_filter in t["course"]]
    return {"notifications": notis, "todos": todos}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--course", default="", help="과목명 부분일치 필터")
    a = ap.parse_args()
    data = fetch(a.course)
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print("── 알림 ──")
    for n in data["notifications"]:
        print(f"  {n['date']:<18} {n['course']:<26} [{n['kind']}] {n['title']}  ({n['when']})")
    print("\n── 할일 ──")
    for t in data["todos"]:
        print(f"  {t['dday']:>6}  {t['course']:<18} [{t['kind']}] {t['title']}  (마감 {t['due']})")


if __name__ == "__main__":
    main()
