"""--auto 의 HTTP(API) 구현 — 브라우저 없이 같은 output/ 구조를 만든다.

collect.py 의 Playwright 흐름과 저장 형식(content.txt · tables.json · links.json · page.html ·
frames/ · meta.json · posts/<글ID>_<제목>/ · attachments.json · files/)이 같아서 analyze.py,
재실행 중복 방지(다운로드 인덱스), 잔재 정리를 그대로 쓴다. 다른 점은 둘:
  - 목록·온라인강의 주차·첨부 목록처럼 브라우저가 AJAX 로 채우던 조각을 같은 엔드포인트로 직접 받는다
    (온라인강의는 화면에 떠 있던 한 주차가 아니라 모든 주차의 출석인정기간을 받는다).
  - 첨부는 efile_download 링크를 쿠키 세션 그대로 GET 한다.
로그인이 키체인 자격으로 되는 경우에만 쓴다(추가 인증이 뜨면 --browser 로 사람이 처리).
"""
import json
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

import api
import collect
import config
import parse

ONLINE_FORM = "online_list_form.acl"
ONLINE_AJAX = "/ilos/st/course/online_list.acl"


# ──────────────────────────────────────────────────────────────────────────
# 저장 (collect.capture 의 HTML 입력판)
# ──────────────────────────────────────────────────────────────────────────
def frame_data(url: str, html: str) -> dict:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)

    tables = []
    for t in soup.find_all("table"):
        rows = []
        for tr in t.find_all("tr"):
            row = [collect._cell_text(c) for c in tr.find_all(["th", "td"])]
            if any(row):
                rows.append(row)
        if rows:
            tables.append(rows)

    links = []
    for a in soup.find_all("a"):
        href, onclick = a.get("href"), a.get("onclick")
        if not href and not onclick:
            continue
        links.append({"text": a.get_text(separator=" ", strip=True), "href": href,
                      "abs": collect.urljoin(url, href) if href else None, "onclick": onclick})
    return {"url": url, "html": html, "text": text, "tables": tables, "links": links}


def capture_html(out_dir: Path, frames: list, label: str = "") -> dict:
    """frames: [(url, html), ...] — 첫 번째가 최상위 페이지, 나머지는 AJAX 로 받은 조각."""
    out_dir.mkdir(parents=True, exist_ok=True)
    data = [frame_data(u, h) for u, h in frames]

    (out_dir / "content.txt").write_text(
        "\n\n".join(f"===== FRAME: {d['url']} =====\n{d['text']}" for d in data if d["text"].strip()),
        encoding="utf-8")
    tables = [{"frame": d["url"], "rows": tbl} for d in data for tbl in d["tables"]]
    (out_dir / "tables.json").write_text(json.dumps(tables, ensure_ascii=False, indent=2), encoding="utf-8")
    links = [{**ln, "frame": d["url"]} for d in data for ln in d["links"]]
    (out_dir / "links.json").write_text(json.dumps(links, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "page.html").write_text(data[0]["html"] if data else "", encoding="utf-8")
    if len(data) > 1:
        fdir = out_dir / "frames"
        fdir.mkdir(exist_ok=True)
        for i, d in enumerate(data):
            (fdir / f"frame_{i:02d}.html").write_text(d["html"], encoding="utf-8")

    meta = {"label": label, "saved_at": datetime.now().isoformat(timespec="seconds"),
            "top_url": data[0]["url"] if data else "", "title": "", "via": "api",
            "frame_count": len(data), "table_count": len(tables), "link_count": len(links)}
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


# ──────────────────────────────────────────────────────────────────────────
# 첨부
# ──────────────────────────────────────────────────────────────────────────
def fetch_attachment(client: api.Client, att: dict, post_url: str, files_dir: Path):
    """첨부 1개 저장. 반환: (ok, 경로, status) — status='session' 이면 세션 만료."""
    url = att.get("url")
    if not url:
        return False, None, None          # downloadClick(token) 만 있는 첨부는 URL 이 없다 → 기록만
    try:
        fname, blob = client.download(url, referer=post_url)
    except api.SessionDead:
        return False, None, "session"
    except Exception:
        return False, None, None
    if fname is None:                      # 파일이 아니라 오류 HTML 이 왔다
        return False, None, None
    name = collect.clean_filename(att.get("filename") or "") or collect.sanitize_label(fname) or "file"
    files_dir.mkdir(parents=True, exist_ok=True)
    dest = collect._unique(files_dir / name)
    dest.write_bytes(blob)
    return True, str(dest), None


def save_attachments(client, atts, post_url, files_dir, download, state, dl_index):
    """첨부 목록 처리(이미 받은 건 건너뜀) → (records, 새로 받은 수, 건너뛴 수)."""
    records, got, skipped = [], 0, 0
    for att in atts:
        rec = {**att, "downloaded": False, "path": None}
        key = parse.attachment_key(att)
        prev = dl_index.get(key) if key else None
        if prev:
            rec["downloaded"], rec["path"], rec["skipped"] = True, prev, True
            try:
                rec["bytes"] = Path(prev).stat().st_size
            except Exception:
                pass
            skipped += 1
        elif download and not state.get("session_dead"):
            ok, path, status = fetch_attachment(client, att, post_url, files_dir)
            if status == "session":
                state["session_dead"] = True
                print("     ⚠ 세션 만료 감지 — 이후 첨부는 '기록만'. 다른 곳에서 로그인했다면 다시 --auto 하세요.")
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
    return records, got, skipped


# ──────────────────────────────────────────────────────────────────────────
# 팀프로젝트 (collect.collect_team_project 의 HTTP판)
# ──────────────────────────────────────────────────────────────────────────
def collect_team_project(client, post_dir, post_url, post_html, download, state, dl_index):
    params = parse.extract_submit_params(post_html)
    room_url = parse.extract_team_room_url(post_html, post_url)
    if not params and not room_url:
        return
    sub = {"post_url": post_url, "project_seq": (params or {}).get("PROJECT_SEQ", ""),
           "team_cd": (params or {}).get("TEAM_CD", ""),
           "submitted": False, "submitted_at": "", "body_text": "", "attachments": []}
    if params:
        try:
            pop = client.post(parse.SUBMIT_POP_ACL, params, referer=post_url)
            (post_dir / "submission_pop.html").write_text(pop or "", encoding="utf-8")
            if "권한이 없습니다" in (pop or ""):
                print("     ⚠ 제출 레코드 조회 권한 없음(과목 세션 만료?) — 건너뜀")
                pop = ""
            parsed = parse.parse_submit_popup(pop)
            sub.update({k: parsed[k] for k in ("submitted", "submitted_at", "body_text")})
            if parsed["file_content_seq"]:
                fl = client.post(parse.EFILE_LIST_ACL, {"CONTENT_SEQ": parsed["file_content_seq"]},
                                 referer=post_url)
                atts = parse.extract_attachments(fl or "", post_url)
                sub["attachments"], _, _ = save_attachments(
                    client, atts, post_url, post_dir / "submission_files", download, state, dl_index)
        except Exception as e:
            print(f"     ⚠ 제출 레코드 조회 실패: {e}")
    (post_dir / "submission.json").write_text(json.dumps(sub, ensure_ascii=False, indent=2), encoding="utf-8")

    if room_url:
        try:
            capture_html(post_dir / "teamroom", [(room_url, client.get(room_url, referer=post_url))], "팀룸")
        except Exception as e:
            print(f"     ⚠ 팀룸 캡처 실패: {e}")
    status = f"제출 {sub['submitted_at']}" if sub["submitted"] else "미제출"
    print(f"     · 팀프로젝트: {status} · 제출첨부 {len(sub['attachments'])}개"
          f" · 팀룸 {'저장' if room_url else '링크없음'}")


# ──────────────────────────────────────────────────────────────────────────
# 글 / 메뉴
# ──────────────────────────────────────────────────────────────────────────
def drill_post(client, menu_dir, idx, post, form_url, download, state, dl_index) -> int:
    post_dir = menu_dir / "posts" / collect.post_dir_name(idx, post)
    if not post_dir.exists():                       # 제목이 바뀐 글은 같은 글ID 폴더를 개명해 재사용
        pid = collect.sanitize_label(parse.post_id_from_url(post["url"]))
        if pid and pid != "untitled" and (menu_dir / "posts").is_dir():
            for old in sorted(p for p in (menu_dir / "posts").iterdir()
                              if p.is_dir() and p.name.startswith(f"{pid}_")):
                old.rename(post_dir)
                print(f"     · 제목 변경 감지 — 폴더 개명: {old.name} → {post_dir.name}")
                break
    try:
        html = client.get(post["url"], referer=form_url)
    except api.SessionDead:
        state["session_dead"] = True
        print(f"     ✗ 글 {idx} — 세션 만료")
        return 0
    except Exception as e:
        print(f"     ✗ 글 {idx} 조회 실패: {e}")
        return 0

    capture_html(post_dir, [(post["url"], html)], post["title"])

    if "project_view_form" in post["url"]:
        collect_team_project(client, post_dir, post["url"], html, download, state, dl_index)
        atts = parse.extract_attachments(html, post["url"])
    else:
        atts = client.post_attachments(html, post["url"])

    records, got, skipped = save_attachments(
        client, atts, post["url"], post_dir / "files", download, state, dl_index)
    (post_dir / "attachments.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

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


def online_weeks(form_html: str) -> list:
    """온라인강의 폼의 주차 선택 요소 id="week-N" → ['3', '4', ...]."""
    return list(dict.fromkeys(re.findall(r'\bid="week-(\w+)"', form_html or "")))


def menu_frames(client, form_path: str, referer: str):
    """메뉴 1개의 [(url, html)] — 폼 + 브라우저가 AJAX 로 채우던 조각(목록 / 온라인강의 주차들)."""
    form_url = api.ORIGIN + form_path
    form = client.get(form_path, referer)
    frames = [(form_url, form)]
    base = form_path.rsplit("/", 1)[0] + "/"
    if form_path.endswith(ONLINE_FORM):
        ud = re.search(r'ud\s*:\s*"([^"]+)"', form)
        ky = re.search(r'ky\s*:\s*"([^"]+)"', form)
        for w in online_weeks(form):
            if ud and ky:
                frag = client.post(ONLINE_AJAX, {"ud": ud.group(1), "ky": ky.group(1), "WEEK_NO": w},
                                   referer=form_url)
                frames.append((f"{api.ORIGIN}{ONLINE_AJAX}?WEEK_NO={w}", frag))
        return frames, form
    url, data = api.parse_list_ajax(form)
    if url:
        seen_ids, page = set(), 1
        while page <= 50:
            frag = client.post(url, {**data, "start": page}, referer=form_url)
            ids = {parse.post_id_from_url(p["url"]) for p in parse.extract_post_links(frag, api.ORIGIN + base)}
            if not ids or ids <= seen_ids:
                break
            seen_ids |= ids
            frames.append((f"{api.ORIGIN}{url}?start={page}", frag))
            page += 1
    return frames, form


def collect_menu(client, label, form_url, referer, output_dir, download, max_posts, state) -> dict:
    form_path = form_url.replace(api.ORIGIN, "")
    form_path = re.sub(r"^https?://[^/]+", "", form_path)
    menu_dir = output_dir / collect.sanitize_label(label)
    for msg in collect.normalize_posts(menu_dir / "posts"):
        print(f"     · {msg}")
    try:
        frames, form = menu_frames(client, form_path, referer)
    except Exception as e:
        print(f"  ✗ {label}: 조회 실패 {e}")
        return {"label": label, "url": form_url, "posts": 0, "attachments": 0}

    meta = capture_html(menu_dir, frames, label)
    base = api.ORIGIN + form_path.rsplit("/", 1)[0] + "/"
    posts, seen = [], set()
    for _, html in frames:                          # 폼 + 조각 모두에서 글 링크 수집(식별자로 중복 제거)
        for p in parse.extract_post_links(html, base):
            pid = parse.post_id_from_url(p["url"]) or p["url"]
            if pid not in seen:
                seen.add(pid)
                posts.append(p)
    print(f"  ✓ {label}  (게시물 {len(posts)}개, 표 {meta['table_count']})")
    if len(posts) > max_posts:
        print(f"     · 게시물 {len(posts)}개 중 {max_posts}개만 수집 (--max-posts 로 조정)")
        posts = posts[:max_posts]

    dl_index = collect.load_downloaded_index(menu_dir)
    n_att = 0
    for i, post in enumerate(posts, 1):
        if state.get("fatal"):
            break
        n_att += drill_post(client, menu_dir, i, post, form_url, download, state, dl_index)
    return {"label": label, "url": form_url, "posts": len(posts), "attachments": n_att}


# ──────────────────────────────────────────────────────────────────────────
# 과목 / 실행
# ──────────────────────────────────────────────────────────────────────────
def pick_courses(cards: list, wanted: str = None) -> list:
    """--course(콤마 구분 부분일치)가 있으면 그것만, 없으면 정규 과목 전부([대괄호] 시작 비교과 제외)."""
    if wanted:
        keys = [k.strip() for k in wanted.split(",") if k.strip()]
        return [c for c in cards if any(k in c["title"] for k in keys)]
    return [c for c in cards if not c["title"].startswith("[")]


def collect_course(client, card, output_root, download, only, max_posts) -> dict:
    submain = client.enter(card["kj"]) or "/ilos/st/course/submain_form.acl"
    html = client.get(submain)
    course = parse.extract_course_name(html) or card["title"]
    output_dir = output_root / collect.sanitize_label(course)
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n📚 {course}  →  {output_dir}/")

    base = api.ORIGIN + submain.rsplit("/", 1)[0] + "/"
    menus = parse.extract_menu_links(html, base)
    if only:
        keys = [k.strip() for k in only.split(",") if k.strip()]
        menus = [(l, u) for (l, u) in menus if any(k in l for k in keys)]
    if not menus:
        print("  메뉴를 못 찾았습니다.")
        return {"course": course, "menus": 0, "posts": 0, "attachments": 0}
    print("  메뉴 %d개: %s" % (len(menus), ", ".join(l for l, _ in menus)))

    state = {"session_dead": False}
    discovered, summary = {}, []
    for label, url in menus:
        summary.append(collect_menu(client, label, url, api.ORIGIN + submain,
                                    output_dir, download, max_posts, state))
        discovered[label] = url
    (output_dir / "_discovered_urls.json").write_text(json.dumps(discovered, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "_index.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"course": course, "menus": len(summary), "posts": sum(s["posts"] for s in summary),
            "attachments": sum(s["attachments"] for s in summary), "session_dead": state["session_dead"]}


def run_api(output_dir: Path, download=True, only=None, max_posts=40, courses=None):
    client = api.Client.from_keychain()
    try:
        client.login()
    except api.LoginError as e:
        raise SystemExit(f"로그인 실패: {e}\n  → 브라우저로 직접 하려면 --browser")
    print("✓ 로그인(API)")
    cards = pick_courses(client.courses(), courses)
    if not cards:
        raise SystemExit("대상 과목이 없습니다. --course 키워드를 확인하세요.")
    results = []
    for card in cards:
        try:
            results.append(collect_course(client, card, output_dir, download, only, max_posts))
        except api.SessionDead:
            print("⚠ 세션이 끊겼습니다(다른 곳에서 로그인했을 수 있음) — 중단합니다.")
            break
    tot = lambda k: sum(r.get(k, 0) for r in results)
    print(f"\n완료: 과목 {len(results)}개 · 게시물 {tot('posts')}개 · 첨부 {tot('attachments')}개")
    if any(r.get("session_dead") for r in results):
        print("⚠️ 진행 중 세션이 만료돼 일부 첨부는 기록만 했습니다. 다시 실행하면 이어서 받습니다.")
    return results
