#!/usr/bin/env python3
"""
Konkuk e-campus(ilos) 수집기.

설계 원칙
  - 로그인은 사람이, 수집은 코드가: headful 브라우저를 띄우고 사용자가 직접 로그인.
    비밀번호는 코드가 절대 다루지 않는다.
  - 로그인 세션은 Playwright persistent context(.auth/)에 저장 → 다음 실행부터 로그인 생략.
  - 한 페이지를 캡처하면 그 폴더 아래에:
        content.txt  화면 텍스트(모든 frame)
        tables.json  표 데이터(모든 frame)
        links.json   링크/onclick(모든 frame)
        page.html    원본 HTML(top), frames/ 아래에 각 frame 원본
        network.json 그 페이지가 호출한 ilos .acl 엔드포인트
        meta.json    캡처 메타정보
  - ilos는 frame/iframe/frameset을 쓸 수 있어 top page뿐 아니라 모든 frame을 긁는다.

--auto 동작
  과목 좌측 메뉴(LNB)를 DOM에서 직접 읽어(/ilos/st/course/..._form.acl) 각 메뉴로 이동·저장하고,
  목록형 메뉴는 게시물 상세까지 들어가 글마다 본문 + 첨부파일을 저장한다.
  저장 위치는 화면에서 과목명을 자동 인식해 output/<과목명>/ 아래로 분리한다(여러 과목이 안 섞임).
  재실행해도 중복이 안 생긴다:
    - 게시물 폴더명은 URL의 글 식별자(ARTL_NUM 등) 기반이라 조회수가 바뀌어도 그대로.
    - 이미 받은 첨부(FILE_SEQ/token 기준, 파일이 실제로 있을 때)는 다시 안 받고 스킵.

사용법
  python collect.py            # 수동: 브라우저에서 이동 → Enter (자동 라벨, 1페이지)
  python collect.py --auto     # 과목 진입 후 메뉴 자동 수집 + 글별 첨부까지
  python collect.py --analyze  # output/ 분석(마감 임박/놓친 항목)

--auto 옵션
  --no-download     첨부 '기록'만 하고 실제 파일은 받지 않음
  --menus 공지,과제  특정 메뉴만(라벨 부분일치, 콤마 구분)
  --max-posts N     메뉴당 게시물 상한(기본 40)
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, unquote, urlencode

from bs4 import BeautifulSoup

import config
import parse

AUTH_DIR = Path(".auth")
OUTPUT_DIR = Path("output")


# ──────────────────────────────────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────────────────────────────────
def sanitize_label(label: str) -> str:
    """라벨을 안전한 폴더/파일명으로. 한글은 유지, 경로/제어문자만 치환."""
    label = (label or "").strip()
    label = re.sub(r'[\\/:*?"<>|\t\n\r]+', "_", label)
    label = re.sub(r"\s+", " ", label).strip(" .")
    return label or "untitled"


def is_ilos_endpoint(url: str) -> bool:
    return ".acl" in url and "ecampus.konkuk.ac.kr" in url


def clean_filename(name: str) -> str:
    """첨부 링크 텍스트에서 실제 파일명만. 예: '- DFT.pdf (162KB)' → 'DFT.pdf'."""
    name = re.sub(r"\s*\(\s*[\d.,]+\s*[KMGT]?B\s*\)\s*$", "", name or "", flags=re.I)
    name = name.strip().lstrip("-•·∙*  ").strip()
    return sanitize_label(name) if name else ""


def cd_filename(headers: dict) -> str:
    """Content-Disposition 헤더에서 파일명 추출(폴백용)."""
    cd = (headers or {}).get("content-disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd, re.I)
    return sanitize_label(unquote(m.group(1))) if m else ""


def auto_label(page) -> str:
    """수동 모드에서 라벨 입력 없이 현재 페이지에 라벨을 붙인다."""
    label = parse.label_from_url(page.url)
    if label and label != "page":
        return sanitize_label(label)
    try:
        t = (page.title() or "").strip()
    except Exception:
        t = ""
    if t and t.lower() not in ("ilos", "e-campus", "ecampus", "konkuk",
                               "건국대학교", "건국대학교 ecampus"):
        return sanitize_label(t)
    return "capture_" + datetime.now().strftime("%m%d_%H%M%S")


# ──────────────────────────────────────────────────────────────────────────
# frame 1개에서 텍스트/표/링크/HTML 추출
# ──────────────────────────────────────────────────────────────────────────
def extract_frame(frame):
    try:
        html = frame.content()
    except Exception:
        return None

    base = frame.url or ""
    try:
        text = frame.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        text = ""

    soup = BeautifulSoup(html, "lxml")
    if not text.strip():
        text = soup.get_text(separator="\n", strip=True)

    tables = []
    for t in soup.find_all("table"):
        rows = []
        for tr in t.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            row = [c.get_text(separator=" ", strip=True) for c in cells]
            if any(row):
                rows.append(row)
        if rows:
            tables.append(rows)

    links = []
    for a in soup.find_all("a"):
        href = a.get("href")
        onclick = a.get("onclick")
        if not href and not onclick:
            continue
        links.append({
            "text": a.get_text(separator=" ", strip=True),
            "href": href,
            "abs": urljoin(base, href) if href else None,
            "onclick": onclick,
        })

    return {"url": base, "html": html, "text": text, "tables": tables, "links": links}


# ──────────────────────────────────────────────────────────────────────────
# 현재 페이지(모든 frame) 캡처 → out_dir
# ──────────────────────────────────────────────────────────────────────────
def capture(page, out_dir: Path, net_sink: list, label: str = "") -> dict:
    try:
        page.wait_for_timeout(200)  # 이벤트 dispatch 펌프
    except Exception:
        pass

    out_dir.mkdir(parents=True, exist_ok=True)

    extracted = [d for d in (extract_frame(fr) for fr in page.frames) if d]

    text_parts = [f"===== FRAME: {d['url']} =====\n{d['text']}"
                  for d in extracted if d["text"].strip()]
    (out_dir / "content.txt").write_text("\n\n".join(text_parts), encoding="utf-8")

    tables = [{"frame": d["url"], "rows": tbl} for d in extracted for tbl in d["tables"]]
    (out_dir / "tables.json").write_text(
        json.dumps(tables, ensure_ascii=False, indent=2), encoding="utf-8")

    links = [{**ln, "frame": d["url"]} for d in extracted for ln in d["links"]]
    (out_dir / "links.json").write_text(
        json.dumps(links, ensure_ascii=False, indent=2), encoding="utf-8")

    main_html = extracted[0]["html"] if extracted else ""
    (out_dir / "page.html").write_text(main_html, encoding="utf-8")
    if len(extracted) > 1:
        fdir = out_dir / "frames"
        fdir.mkdir(exist_ok=True)
        for i, d in enumerate(extracted):
            (fdir / f"frame_{i:02d}.html").write_text(d["html"], encoding="utf-8")

    seen, net = set(), []
    for u in net_sink:
        if is_ilos_endpoint(u) and u not in seen:
            seen.add(u)
            net.append(u)
    (out_dir / "network.json").write_text(
        json.dumps(net, ensure_ascii=False, indent=2), encoding="utf-8")
    net_sink.clear()

    try:
        title = page.title()
    except Exception:
        title = ""

    meta = {
        "label": label,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "top_url": page.url,
        "title": title,
        "frame_count": len(extracted),
        "table_count": len(tables),
        "link_count": len(links),
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


# ──────────────────────────────────────────────────────────────────────────
# 브라우저
# ──────────────────────────────────────────────────────────────────────────
def open_context(p, auth_dir: Path):
    return p.chromium.launch_persistent_context(
        user_data_dir=str(auth_dir),
        headless=False,
        no_viewport=True,
        accept_downloads=True,
        args=["--start-maximized"],
    )


def active_page(context):
    pages = [pg for pg in context.pages if not pg.is_closed()]
    return pages[-1] if pages else context.new_page()


def detect_course(page) -> str:
    """현재 화면(모든 frame)에서 과목명을 읽는다. 없으면 ''.
    과목별로 output/<과목명>/ 에 저장해 여러 과목이 안 섞이게 하는 용도."""
    htmls = []
    try:
        htmls.append(page.content())
    except Exception:
        pass
    for fr in page.frames:
        try:
            htmls.append(fr.content())
        except Exception:
            pass
    for h in htmls:
        c = parse.extract_course_name(h)
        if c:
            return c
    return ""


def attach_net_logging(context, sink: list):
    def on_page(pg):
        try:
            pg.on("request", lambda req: sink.append(req.url))
        except Exception:
            pass
    for pg in context.pages:
        on_page(pg)
    context.on("page", on_page)


def goto_base_if_blank(page, base_url):
    try:
        if page.url in ("", "about:blank"):
            page.goto(base_url, wait_until="domcontentloaded")
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────
# 자동 로그인 + 과목 자동 진입
#   ilos 세션 쿠키는 브라우저 종료와 함께 사라져 매 실행 로그인이 필요하다.
#   비밀번호는 macOS 키체인에만 둔다(파일/레포에 절대 안 남음): ./ec login 으로 1회 등록.
#   키체인이 없거나 실패하면 종전처럼 사람이 직접 로그인하는 흐름으로 자연스럽게 폴백.
# ──────────────────────────────────────────────────────────────────────────
KEYCHAIN_SERVICE = "ecampus-collector"


def keychain_creds():
    """macOS 키체인에서 (아이디, 비밀번호). 미등록/비macOS면 (None, None)."""
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return None, None
        m = re.search(r'"acct"<blob>="([^"]*)"', r.stdout)
        acct = m.group(1) if m else None
        r2 = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                            capture_output=True, text=True)
        pw = r2.stdout.rstrip("\n") if r2.returncode == 0 else None
        return (acct, pw) if acct and pw else (None, None)
    except FileNotFoundError:
        return None, None


def save_login():
    """아이디/비밀번호를 macOS 키체인에 저장. 삭제는
    security delete-generic-password -s ecampus-collector"""
    import getpass
    uid = input("eCampus 아이디(학번): ").strip()
    pw = getpass.getpass("eCampus 비밀번호(입력 안 보임): ")
    if not uid or not pw:
        sys.exit("아이디/비밀번호가 비었습니다.")
    subprocess.run(["security", "add-generic-password", "-U",
                    "-s", KEYCHAIN_SERVICE, "-a", uid, "-w", pw], check=True)
    print(f"✓ 키체인에 저장했습니다 (service={KEYCHAIN_SERVICE}, account={uid})")
    print("  이제 ./ec 실행 시 자동 로그인합니다.")


def is_logged_in(page) -> bool:
    try:
        return "로그아웃" in (page.locator("body").inner_text(timeout=4000) or "")
    except Exception:
        return False


def auto_login(page) -> bool:
    """키체인 자격증명으로 로그인 폼(usr_id/usr_pwd → loginForm())을 채워 로그인.
    성공 여부 반환. 자격증명이 없으면 시도하지 않고 False."""
    uid, pw = keychain_creds()
    if not uid:
        return False
    try:
        page.goto(config.LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(800)
        if is_logged_in(page):   # 이미 로그인 상태면 폼 없이 메인으로 튕긴다
            return True
        page.fill("#usr_id", uid)
        page.fill("#usr_pwd", pw)
        try:   # 캠퍼스 라디오(서울 s_campus / 글로컬 g_campus)
            page.check("#g_campus" if getattr(config, "CAMPUS", "서울") == "글로컬"
                       else "#s_campus")
        except Exception:
            pass
        page.evaluate("() => loginForm()")   # 실패 alert 는 Playwright 가 자동 dismiss
        page.wait_for_timeout(2500)
        ok = is_logged_in(page)
        print("✓ 자동 로그인 성공" if ok
              else "✗ 자동 로그인 실패 — 아이디/비밀번호 확인 후 ./ec login 으로 재등록하거나 직접 로그인하세요.")
        return ok
    except Exception as e:
        print(f"✗ 자동 로그인 오류: {e}")
        return False


def enter_course(page, keyword: str) -> bool:
    """메인 화면의 과목 카드(kj 속성, eclassRoom)를 찾아 강의실로 들어간다.
    '강의실 들어가기' 요소는 hover 전엔 숨김이라 JS click 으로 직접 발화."""
    try:
        if "main_form" not in (page.url or ""):
            page.goto(urljoin(config.BASE_URL, "/ilos/main/main_form.acl"),
                      wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
        clicked = page.evaluate(
            """(kw) => {
                const els = [...document.querySelectorAll('[kj]')];
                const t = els.find(e => ((e.getAttribute('title')||'') + (e.textContent||''))
                                        .includes(kw));
                if (!t) return false;
                t.click();
                return true;
            }""", keyword)
        if not clicked:
            return False
        page.wait_for_timeout(3500)
        return "/st/course/" in (page.url or "")
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────
# 첨부 다운로드
# ──────────────────────────────────────────────────────────────────────────
def _unique(path: Path) -> Path:
    """같은 이름 파일이 있으면 _2, _3 … 붙여 덮어쓰기 방지."""
    if not path.exists():
        return path
    stem, suf, i = path.stem, path.suffix, 2
    while (cand := path.with_name(f"{stem}_{i}{suf}")).exists():
        i += 1
    return cand


def _is_session_dead(body) -> bool:
    """응답이 '다시 로그인 하세요' 같은 세션 만료 페이지인가 (str/bytes 모두 허용)."""
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", "ignore")
        except Exception:
            return False
    return ("다시 로그인" in body) or ("접속이 종료" in body) or ("로그인 하세요" in body)


def try_download(page, dl_page, post_url: str, att: dict, files_dir: Path, state: dict):
    """첨부 1개 저장. 반환: (ok, 경로, status).  status='session' 이면 세션 만료 감지."""
    url = att.get("url")

    if url:
        # 전용 탭에서 '진짜 브라우저 네비게이션'으로 다운로드 캡처(실제 클릭과 동일한 세션).
        # 서버가 Content-Disposition: attachment 를 주면 download 이벤트로 잡힌다.
        if dl_page is None:
            return False, None, None
        state["dialog_dead"] = False  # dl_page dialog 핸들러가 세션만료 alert를 만나면 True
        try:
            with dl_page.expect_download(timeout=12000) as di:
                try:
                    dl_page.goto(url, referer=post_url, timeout=10000)
                except Exception:
                    pass  # 다운로드로 전환되며 goto가 abort되는 건 정상
            dl = di.value
            fname = clean_filename(att.get("filename") or "") or dl.suggested_filename or "file"
            files_dir.mkdir(parents=True, exist_ok=True)
            dest = _unique(files_dir / fname)
            dl.save_as(str(dest))
            return True, str(dest), None
        except Exception:
            pass
        # 다운로드가 안 떴다 → 세션 만료인지 판정
        #   세션 죽으면 efile_download 가 alert("…다시 로그인…") 후 main_form 으로 redirect.
        cur = ""
        try:
            cur = dl_page.url or ""
        except Exception:
            pass
        if state.get("dialog_dead") or "main_form.acl" in cur or "login" in cur.lower():
            return False, None, "session"
        try:
            if _is_session_dead(dl_page.content()):
                return False, None, "session"
        except Exception:
            pass
        return False, None, None

    # downloadClick('TOKEN') — JS가 트리거하는 다운로드를 캡처
    token = att.get("token")
    if not token:
        return False, None, None
    try:
        if post_url.split("?")[0] not in page.url:
            page.goto(post_url, wait_until="domcontentloaded")
            page.wait_for_timeout(400)
        with page.expect_download(timeout=15000) as di:
            page.locator(f'[onclick*="{token}"]').first.click(timeout=6000)
        dl = di.value
        fname = clean_filename(dl.suggested_filename or att.get("filename") or "") or token
        files_dir.mkdir(parents=True, exist_ok=True)
        dest = _unique(files_dir / fname)
        dl.save_as(str(dest))
        return True, str(dest), None
    except Exception:
        return False, None, None


# ──────────────────────────────────────────────────────────────────────────
# 팀프로젝트: 팀룸 + 제출 레코드 드릴다운
# ──────────────────────────────────────────────────────────────────────────
def fetch_acl(page, path: str, data: dict) -> str:
    """현재 페이지 컨텍스트에서 ilos .acl 을 POST(fetch)로 호출해 응답 본문을 받는다.
    조회 전용 엔드포인트에만 쓸 것. 과목 세션이 있어야 동작(과목방에 들어간 page 필요) —
    아니면 ilos 가 '접근 권한이 없습니다' 스크립트를 돌려준다."""
    body = urlencode({**data, "encoding": "utf-8"})
    return page.evaluate(
        """async ([path, body]) => {
            const r = await fetch(path, {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body,
            });
            return await r.text();
        }""", [path, body])


def collect_team_project(page, post_dir: Path, net_sink, download,
                         dl_page, state, dl_index: dict):
    """팀프로젝트 게시물에서 '실제 제출 내용'까지 내려간다.
      - submission.json     제출 여부/일시/본문 + 제출 첨부 목록(+다운로드 결과)
      - submission_pop.html 제출 팝업 원본
      - teamroom/           팀룸(팀진행내용) 페이지 캡처
    게시물 목록의 '제출여부' 플래그는 기존 제출물이 있으면 늘 '제출'이라,
    무엇이 제출됐는지는 여기 제출 레코드를 봐야 안다. 팀 미배정이면 조용히 건너뛴다."""
    post_url = page.url
    html = page.content()
    params = parse.extract_submit_params(html)
    room_url = parse.extract_team_room_url(html, post_url)
    if not params and not room_url:
        return

    sub = {"post_url": post_url,
           "project_seq": (params or {}).get("PROJECT_SEQ", ""),
           "team_cd": (params or {}).get("TEAM_CD", ""),
           "submitted": False, "submitted_at": "", "body_text": "",
           "attachments": []}

    if params:
        try:
            pop = fetch_acl(page, parse.SUBMIT_POP_ACL, params)
            (post_dir / "submission_pop.html").write_text(pop or "", encoding="utf-8")
            if "권한이 없습니다" in (pop or ""):
                print("     ⚠ 제출 레코드 조회 권한 없음(과목 세션 만료?) — 건너뜀")
                pop = ""
            parsed = parse.parse_submit_popup(pop)
            sub.update({k: parsed[k] for k in ("submitted", "submitted_at", "body_text")})
            if parsed["file_content_seq"]:
                fl = fetch_acl(page, parse.EFILE_LIST_ACL,
                               {"CONTENT_SEQ": parsed["file_content_seq"]})
                for att in parse.extract_attachments(fl or "", post_url):
                    rec = {**att, "downloaded": False, "path": None}
                    key = parse.attachment_key(att)
                    prev = dl_index.get(key) if key else None
                    if prev:
                        rec["downloaded"], rec["path"], rec["skipped"] = True, prev, True
                    elif download and not state.get("session_dead"):
                        ok, path, status = try_download(
                            page, dl_page, post_url, att,
                            post_dir / "submission_files", state)
                        if status == "session":
                            state["session_dead"] = True
                            print("     ⚠ 세션 만료 감지 — 이후 첨부는 '기록만'.")
                        rec["downloaded"], rec["path"] = ok, path
                        if ok and path and key:
                            dl_index[key] = path
                    sub["attachments"].append(rec)
        except Exception as e:
            print(f"     ⚠ 제출 레코드 조회 실패: {e}")

    (post_dir / "submission.json").write_text(
        json.dumps(sub, ensure_ascii=False, indent=2), encoding="utf-8")

    if room_url:
        try:
            page.goto(room_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            capture(page, post_dir / "teamroom", net_sink, "팀룸")
        except Exception as e:
            print(f"     ⚠ 팀룸 캡처 실패: {e}")

    status = (f"제출 {sub['submitted_at']}" if sub["submitted"] else "미제출")
    print(f"     · 팀프로젝트: {status} · 제출첨부 {len(sub['attachments'])}개"
          f" · 팀룸 {'저장' if room_url else '링크없음'}")


# ──────────────────────────────────────────────────────────────────────────
# 게시물 상세 1개 드릴다운
# ──────────────────────────────────────────────────────────────────────────
def post_dir_name(idx: int, post: dict) -> str:
    """게시물 폴더명. URL의 글 식별자(ARTL_NUM/RT_SEQ/PROJECT_SEQ …) 기반이라
    재실행해도 같은 글은 같은 폴더가 된다. 식별자가 없으면 종전대로 목록 순번."""
    title = sanitize_label(post["title"])[:40] or "post"
    pid = sanitize_label(parse.post_id_from_url(post["url"]))
    return f"{pid}_{title}" if pid and pid != "untitled" else f"{idx:02d}_{title}"


def load_downloaded_index(menu_dir: Path) -> dict:
    """이전 실행이 남긴 posts/*/attachments.json에서 이미 받은 첨부를 모은다.
    {parse.attachment_key(...): 파일 경로}. 게시물 폴더명이 바뀌었어도(조회수/순번)
    FILE_SEQ·token이 같고 파일이 실제로 있으면 잡힌다 → 재다운로드 방지."""
    index = {}
    for aj in sorted((menu_dir / "posts").glob("*/attachments.json")):
        try:
            records = json.loads(aj.read_text(encoding="utf-8"))
        except Exception:
            continue
        for rec in records:
            if not rec.get("downloaded") or not rec.get("path"):
                continue
            key = parse.attachment_key(rec)
            if not key or key in index:
                continue
            p = Path(rec["path"])
            if not p.exists():  # 기록은 실행 당시 CWD 기준 — 폴더 위치로 한 번 더 시도
                p = aj.parent / "files" / Path(rec["path"]).name
            if p.exists():
                index[key] = str(p)
    return index


def drill_post(page, menu_dir: Path, idx: int, post: dict, net_sink,
               download: bool, dl_page, state, dl_index: dict) -> int:
    post_dir = menu_dir / "posts" / post_dir_name(idx, post)
    try:
        page.goto(post["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
    except Exception as e:
        print(f"     ✗ 게시물 {idx} 이동 실패: {e}")
        return 0

    capture(page, post_dir, net_sink, post["title"])

    # 팀프로젝트 게시물이면 팀룸 + 제출 레코드(제출일시/본문/첨부)까지 수집
    if "project_view_form" in post["url"]:
        collect_team_project(page, post_dir, net_sink, download, dl_page, state, dl_index)
        try:   # 팀룸 이동으로 바뀐 페이지를 게시물로 복귀(첨부 파싱은 게시물 기준)
            if post["url"].split("?")[0] not in page.url:
                page.goto(post["url"], wait_until="domcontentloaded")
                page.wait_for_timeout(600)
        except Exception:
            pass

    atts = parse.extract_attachments(page.content(), page.url)
    records = []
    got = skipped = 0
    for att in atts:
        rec = {**att, "downloaded": False, "path": None}
        key = parse.attachment_key(att)
        prev = dl_index.get(key) if key else None
        if prev:
            # 이전 실행(또는 이번 실행의 다른 글)에서 이미 받은 첨부 — 다시 안 받음
            rec["downloaded"], rec["path"], rec["skipped"] = True, prev, True
            try:
                rec["bytes"] = Path(prev).stat().st_size
            except Exception:
                pass
            skipped += 1
        elif download and not state.get("session_dead"):
            ok, path, status = try_download(page, dl_page, post["url"], att, post_dir / "files", state)
            if status == "session":
                state["session_dead"] = True
                print("     ⚠ 세션 만료 감지 — 이후 첨부는 '기록만'. 로그인 직후 다시 --auto 하세요.")
            rec["downloaded"], rec["path"] = ok, path
            if ok and path:
                try:
                    rec["bytes"] = Path(path).stat().st_size
                except Exception:
                    pass
                if key:
                    dl_index[key] = path
            got += int(ok)
        records.append(rec)
    (post_dir / "attachments.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    if atts:
        if not download:
            tail = " (기록만)"
        elif state.get("session_dead"):
            tail = f", 받음 {got}/{len(atts)} (세션만료→기록만)"
        else:
            tail = f", 받음 {got}/{len(atts)}"
        if skipped:
            tail += f" · 이미 있음 {skipped}"
        print(f"     · 글{idx:02d} 첨부 {len(atts)}개{tail}  [{post['title'][:30]}]")
    return len(atts)


# ──────────────────────────────────────────────────────────────────────────
# 메뉴 1개 수집 (목록 + 게시물 드릴다운)
# ──────────────────────────────────────────────────────────────────────────
def collect_menu(page, label, url, net_sink, output_dir, download, max_posts, dl_page, state) -> dict:
    menu_dir = output_dir / sanitize_label(label)
    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(1400)
    except Exception as e:
        print(f"  ✗ {label}: 이동 실패 {e}")
        return {"label": label, "url": url, "posts": 0, "attachments": 0}

    meta = capture(page, menu_dir, net_sink, label)
    posts = parse.extract_post_links(page.content(), page.url)
    print(f"  ✓ {label}  (게시물 {len(posts)}개, 표 {meta['table_count']})")

    if len(posts) > max_posts:
        print(f"     · 게시물 {len(posts)}개 중 {max_posts}개만 수집 (--max-posts 로 조정)")
        posts = posts[:max_posts]

    dl_index = load_downloaded_index(menu_dir)   # 이전 실행에서 이미 받은 첨부
    n_att = 0
    for i, post in enumerate(posts, 1):
        n_att += drill_post(page, menu_dir, i, post, net_sink, download, dl_page, state, dl_index)
    return {"label": label, "url": url, "posts": len(posts), "attachments": n_att}


# ──────────────────────────────────────────────────────────────────────────
# 모드: 수동 캡처 (라벨 입력 불필요, 1페이지)
# ──────────────────────────────────────────────────────────────────────────
def run_manual(context, net_sink, base_url, output_dir):
    page = active_page(context)
    goto_base_if_blank(page, base_url)
    if not is_logged_in(page):
        auto_login(page)   # 키체인 없으면 그냥 False — 직접 로그인하면 됨

    print("─" * 70)
    print("수동 캡처 모드 (라벨 입력 불필요, 현재 페이지 1장)")
    print("  · 그냥 Enter      → 현재 탭을 자동 라벨로 저장")
    print("  · 텍스트 + Enter  → 라벨 직접 지정")
    print("  · auto + Enter    → 과목 메뉴 자동 수집(+글별 첨부)")
    print("  · q + Enter       → 종료")
    print("─" * 70)

    while True:
        try:
            s = input("\nEnter=자동저장 · 텍스트=라벨 · auto=자동수집 · q=종료 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if s.lower() in {"q", "quit", "exit"}:
            break
        if s.lower() == "auto":
            run_auto(context, net_sink, base_url, output_dir)
            continue

        pg = active_page(context)
        label = s if s else auto_label(pg)
        course = detect_course(pg)                       # 과목별 폴더로 분리(섞임 방지)
        base = output_dir / sanitize_label(course) if course else output_dir
        dest = base / sanitize_label(label)
        try:
            meta = capture(pg, dest, net_sink, label)
        except Exception as e:
            print(f"  ✗ 캡처 실패: {e}")
            continue
        tag = f"[{course}] " if course else ""
        print(f"  ✓ {tag}{dest}  ← {meta['top_url']}")
        print(f"    표 {meta['table_count']}, 링크 {meta['link_count']}")


# ──────────────────────────────────────────────────────────────────────────
# 모드: 메뉴 자동 수집 (+ 글별 첨부)
# ──────────────────────────────────────────────────────────────────────────
def run_auto(context, net_sink, base_url, output_dir,
             download=True, only=None, max_posts=40):
    page = active_page(context)
    goto_base_if_blank(page, base_url)

    # 키체인 자격증명이 있으면 로그인 → 과목 진입까지 무인 진행.
    # 하나라도 안 되면 종전대로 사람이 하는 흐름(안내 + Enter)으로 폴백.
    hands_free = False
    if not is_logged_in(page):
        auto_login(page)
    auto_course = getattr(config, "AUTO_COURSE", None)
    if auto_course and is_logged_in(page):
        hands_free = enter_course(page, auto_course)
        if hands_free:
            print(f"✓ 과목 자동 진입: {auto_course}")
        else:
            print(f"⚠ '{auto_course}' 과목 카드를 못 찾았습니다 — 직접 들어가 주세요.")

    if not hands_free:
        print("─" * 70)
        print("자동 수집 모드")
        print("  - 브라우저에서 로그인하고 '원하는 과목'에 들어가세요(좌측 메뉴가 보이는 화면).")
        print("  - 준비되면 Enter → 메뉴를 자동으로 찾아 이동·저장하고,")
        print("    공지/자료/과제 같은 목록은 글 하나하나 들어가 본문 + 첨부까지 받습니다.")
        print("─" * 70)
        try:
            input("준비되면 Enter > ")
        except (EOFError, KeyboardInterrupt):
            return

    page = active_page(context)
    net_sink.clear()

    course = detect_course(page)                       # 과목별 폴더로 분리(섞임 방지)
    if course:
        output_dir = output_dir / sanitize_label(course)
        print(f"📚 과목 인식: {course}  →  {output_dir}/ 에 저장 (과목별 분리)")
    else:
        print("⚠ 과목명을 못 읽었습니다 → output 바로 아래에 저장.\n"
              "   과목 좌측 메뉴가 보이는 화면인지 확인하세요(다른 과목과 섞일 수 있음).")
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        menus = parse.extract_menu_links(page.content(), page.url)
    except Exception as e:
        print(f"메뉴 읽기 실패: {e}")
        return
    if not menus:
        print("좌측 과목 메뉴를 못 찾았습니다. 과목 홈(메뉴가 보이는 화면)에서 다시 시도하세요.")
        return
    if only:
        keys = [k.strip() for k in only.split(",") if k.strip()]
        menus = [(l, u) for (l, u) in menus if any(k in l for k in keys)]
        if not menus:
            print(f"'{only}' 와 일치하는 메뉴가 없습니다. 발견된 메뉴를 확인하세요.")
            return

    print(f"\n발견된 메뉴 {len(menus)}개: " + ", ".join(l for l, _ in menus))
    print(f"첨부 다운로드: {'ON' if download else 'OFF(기록만)'} · 메뉴당 최대 글 {max_posts}\n")

    dl_page = context.new_page() if download else None   # 첨부 다운로드 전용 탭
    state = {"session_dead": False, "dialog_dead": False}
    if dl_page is not None:
        # 세션 만료 시 efile_download 가 띄우는 alert("…다시 로그인…")를 잡아 신호로 사용
        def _on_dialog(d):
            try:
                if _is_session_dead(d.message):
                    state["dialog_dead"] = True
            finally:
                try:
                    d.dismiss()
                except Exception:
                    pass
        dl_page.on("dialog", _on_dialog)

    discovered, summary = {}, []
    for label, url in menus:
        info = collect_menu(page, label, url, net_sink, output_dir,
                            download, max_posts, dl_page, state)
        discovered[label] = url
        summary.append(info)

    # config.KNOWN_URLS 중 메뉴에서 못 잡힌 것 보충(선택적 override)
    covered = set(discovered.values())
    for label, url in getattr(config, "KNOWN_URLS", {}).items():
        if not only and url not in covered and label not in discovered:
            info = collect_menu(page, label, url, net_sink, output_dir,
                                download, max_posts, dl_page, state)
            discovered[label] = url
            summary.append(info)

    if dl_page is not None:
        try:
            dl_page.close()
        except Exception:
            pass

    (output_dir / "_discovered_urls.json").write_text(
        json.dumps(discovered, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "_index.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    tot_posts = sum(s["posts"] for s in summary)
    tot_att = sum(s["attachments"] for s in summary)
    n_files = sum(1 for aj in output_dir.rglob("attachments.json")
                  for it in json.loads(aj.read_text(encoding="utf-8")) if it.get("downloaded"))
    print(f"\n완료: 메뉴 {len(summary)}개 · 게시물 {tot_posts}개 · 첨부 {tot_att}개(받음 {n_files}개)")
    print(f"요약: {output_dir}/_index.json,  메뉴 URL: {output_dir}/_discovered_urls.json")
    if state["session_dead"]:
        print("\n⚠️ 진행 중 ilos 세션이 만료됐습니다 → 첨부 일부만 받았을 수 있어요.")
        print("   로그인 직후 곧바로 다시 실행하면 첨부까지 받아집니다 (이미 받은 파일은 그대로 둠):")
        print("     .venv/bin/python collect.py --auto")


# ──────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Konkuk e-campus(ilos) 수집기")
    ap.add_argument("--auto", action="store_true",
                    help="과목 진입 후 메뉴 자동 수집 + 글별 첨부까지")
    ap.add_argument("--analyze", action="store_true", help="output/ 분석(마감/놓친 항목)")
    ap.add_argument("--save-login", action="store_true",
                    help="아이디/비밀번호를 macOS 키체인에 저장(자동 로그인용, ./ec login)")
    ap.add_argument("--no-download", action="store_true", help="첨부는 기록만, 파일은 안 받음")
    ap.add_argument("--menus", default=None, help="특정 메뉴만(라벨 부분일치, 콤마 구분)")
    ap.add_argument("--max-posts", type=int, default=40, help="메뉴당 게시물 상한(기본 40)")
    ap.add_argument("--base", default=config.BASE_URL, help="시작 URL")
    ap.add_argument("--output", default=str(OUTPUT_DIR), help="저장 폴더(기본 output)")
    ap.add_argument("--auth", default=str(AUTH_DIR), help="세션 폴더(기본 .auth)")
    args = ap.parse_args()

    output_dir = Path(args.output)

    if args.save_login:
        save_login()
        return

    if args.analyze:
        import analyze
        analyze.analyze(output_dir)
        return

    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        sys.exit("Playwright가 설치되어 있지 않습니다.\n"
                 "  pip install -r requirements.txt && playwright install chromium")

    net_sink: list = []
    with sync_playwright() as p:
        context = open_context(p, Path(args.auth))
        attach_net_logging(context, net_sink)
        try:
            if args.auto:
                run_auto(context, net_sink, args.base, output_dir,
                         download=not args.no_download, only=args.menus,
                         max_posts=args.max_posts)
            else:
                run_manual(context, net_sink, args.base, output_dir)
        finally:
            try:
                context.close()
            except Exception:
                pass

    print("\n수집 종료. 분석: python collect.py --analyze")


if __name__ == "__main__":
    main()
