"""
ilos 페이지 파싱 (순수 함수 — 네트워크/Playwright 불필요, 단위 테스트 가능).

- extract_menu_links: 좌측 과목 메뉴(LNB) 링크 {라벨: URL}. 항상 /ilos/st/course/ 아래라
  커뮤니티/상단(GNB)의 동명 메뉴(예: '공지사항' 중복)와 안 헷갈린다.
- extract_post_links: 목록 페이지의 게시물 상세 링크. ilos는 제목에
  onclick="pageMove('....view_form.acl?ARTL_NUM=..', event)" 형태를 쓴다(자료=ARTL_NUM, 과제=RT_SEQ 등).
- extract_attachments: 첨부파일. onclick="downloadClick('TOKEN')" 또는 download류 href.
- clean_post_title / post_id_from_url / attachment_key: 재실행 시 같은 글/첨부를
  같은 것으로 알아보기 위한 안정화(조회수 꼬리 제거, URL 식별자, FILE_SEQ 키).
"""

import re
from html import unescape
from urllib.parse import urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup

# 게시물 상세 URL을 메뉴와 구분하는 식별자 파라미터
POST_ID_PARAMS = ("ARTL_NUM", "RT_SEQ", "SEQ", "ARTL_SEQ", "BBS_SEQ",
                  "PROJECT_SEQ", "TEST_SEQ", "QNA_SEQ", "LECTURE_WEEKS")

# .acl 파일명 → 사람이 읽는 라벨 (자동 라벨링용)
ACL_LABELS = {
    "notice": "공지사항",
    "lecture_material_online": "온라인강의",
    "lecture_material": "강의자료",
    "online": "온라인강의",
    "zoom": "실시간강의",
    "qna2": "질의응답",
    "qna": "질의응답",
    "attendance": "출석",
    "report": "과제",
    "project": "팀프로젝트",
    "test": "시험",
    "discuss": "토론",
    "clicker": "투표",
    "survey": "설문",
    "appeal": "이의신청",
    "eval3_result": "성적",
    "plan": "강의계획서",
    "material": "자료실",
}


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def clean_label(text: str) -> str:
    """메뉴 라벨에서 끝의 카운트 배지 제거. 예: '강의자료 4' → '강의자료'."""
    t = clean_text(text)
    t = re.sub(r"\s*\d+$", "", t).strip()
    return t


# 목록 행 텍스트의 꼬리표: '작성자 조회 N'(공지/자료) 또는 '팀장제출/개별제출'(팀프로젝트)
_TITLE_TAIL_RE = re.compile(r"(?:\s+\S{1,20})?\s+조회\s*[\d,]+$|\s+(?:팀장|개별)제출$")


def clean_post_title(text: str) -> str:
    """목록 행 전체 텍스트에서 제목만. ilos는 제목 셀에 작성자·조회수까지 들어 있어
    ('제목 작성자 조회 266') 조회수가 바뀔 때마다 제목이 달라진다 → 꼬리표를 떼어
    재실행해도 같은 글이 같은 제목(=같은 폴더)이 되게 한다."""
    t = clean_text(text)
    cleaned = _TITLE_TAIL_RE.sub("", t).strip()
    if not cleaned:  # 제목이 통째로 꼬리표 패턴에 먹힌 극단 케이스 → 조회수 숫자만 제거
        cleaned = re.sub(r"\s*조회\s*[\d,]+$", "", t).strip()
    return cleaned or t


def post_id_from_url(url: str) -> str:
    """게시물 상세 URL의 안정 식별자 값(ARTL_NUM/RT_SEQ/PROJECT_SEQ …). 없으면 ''.
    조회수·정렬과 무관하게 글마다 고정이라 폴더명 안정화에 쓴다."""
    q = parse_qs(urlparse(url or "").query)
    for k in POST_ID_PARAMS:
        v = q.get(k)
        if v and v[0]:
            return v[0]
    return ""


def attachment_key(att: dict):
    """첨부의 안정 식별자 키(없으면 None). 재실행 시 '이미 받은 첨부' 판별용.
    직접 다운로드 URL은 FILE_SEQ 기준(ud/pf_st_flag 등 부속 파라미터와 무관),
    downloadClick류는 token 기준. attachments.json 레코드에도 그대로 쓸 수 있다."""
    url = att.get("url") or ""
    if url:
        q = parse_qs(urlparse(url).query)
        fseq = (q.get("FILE_SEQ") or q.get("file_seq") or [None])[0]
        return ("file", fseq) if fseq else ("url", url)
    token = att.get("token")
    return ("tok", token) if token else None


def label_from_url(url: str) -> str:
    """URL의 .acl 파일명에서 라벨 유추 (예: notice_list_form.acl → 공지사항)."""
    m = re.search(r"/([A-Za-z0-9_]+)\.acl", url or "")
    stem = (m.group(1) if m else "").lower()
    for key, name in ACL_LABELS.items():  # 긴 키(구체적)가 먼저 오도록 dict 정렬됨
        if key in stem:
            return name
    return stem or "page"


CAMPUS_COURSE_RE = re.compile(
    r"\[[^\[\]]{1,8}\]\s*([0-9A-Za-z가-힣 ·&._-]+?)\s*\((\d{1,4})\)"
)


def extract_course_name(html: str, with_section: bool = False) -> str:
    """현재 과목 화면의 breadcrumb에서 과목명을 뽑는다.
    '수강과목 / 2026-1학기 / [서울]객체지향프로그래밍(001)' → '객체지향프로그래밍'.
    with_section=True 면 '객체지향프로그래밍(001)'. 못 찾으면 ''.
    여러 과목을 한 output/ 에 섞지 않도록, 수집 시 과목별 폴더명을 정하는 데 쓴다.
    """
    soup = BeautifulSoup(html or "", "lxml")
    text = soup.get_text(separator="\n")
    # 1순위: 'YYYY-N학기' 바로 뒤(=breadcrumb 위치)의 '[캠퍼스]과목명(분반)'
    m = re.search(r"\d{4}\s*-\s*\d\s*학기\s*" + CAMPUS_COURSE_RE.pattern, text)
    if not m:  # 2순위: 페이지에서 처음 등장하는 '[캠퍼스]과목명(분반)'
        m = CAMPUS_COURSE_RE.search(text)
    if not m:
        return ""
    name = re.sub(r"\s+", " ", m.group(1)).strip(" ·._-")
    if not name:
        return ""
    return f"{name}({m.group(2)})" if with_section else name


def _href_from_anchor(a) -> str:
    """<a>의 실제 이동 URL. href 우선, 없으면 onclick의 pageGo/pageMove에서 추출."""
    href = a.get("href") or ""
    if href and not href.startswith("#") and not href.lower().startswith("javascript"):
        return href
    oc = a.get("onclick") or ""
    m = re.search(r"(?:pageGo|pageMove)\(['\"]([^'\"]+)['\"]", oc)
    return m.group(1) if m else ""


def extract_menu_links(html: str, base_url: str):
    """좌측 과목 메뉴(LNB) → [(라벨, 절대URL)] (순서 유지, 중복 제거)."""
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.find_all("a"):
        href = _href_from_anchor(a)
        if not href or href.startswith("#"):
            continue
        absu = urljoin(base_url, unescape(href))
        pr = urlparse(absu)
        if "/ilos/st/course/" not in pr.path:        # 과목 메뉴만 (커뮤니티/GNB 제외)
            continue
        if pr.path.endswith(("submain_form.acl", "main_form.acl")):
            continue
        if not pr.path.endswith("_form.acl"):
            continue
        q = parse_qs(pr.query)
        if any(k in q for k in POST_ID_PARAMS):      # 게시물 상세는 메뉴가 아님
            continue
        if "s=menu" in pr.query or "acl=" in pr.query:  # 빵부스러기 중복 링크
            continue
        label = clean_label(a.get_text())
        if not label:
            continue
        key = pr.path + ("?" + pr.query if pr.query else "")
        if key in seen:
            continue
        seen.add(key)
        out.append((label, absu))
    return out


def extract_post_links(html: str, base_url: str):
    """목록 페이지의 게시물 상세 링크 → [{title, url}] (중복 제거)."""
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for el in soup.find_all(True):
        url = ""
        oc = el.get("onclick") or ""
        m = re.search(r"pageMove\(['\"]([^'\"]+)['\"]", oc)
        if m:
            url = m.group(1)
        elif el.name == "a":
            href = el.get("href") or ""
            if "_view_form.acl" in href:
                url = href
        if not url:
            continue
        absu = urljoin(base_url, unescape(url))
        pr = urlparse(absu)
        if "_view_form.acl" not in pr.path:
            continue
        q = parse_qs(pr.query)
        if not any(k in q for k in POST_ID_PARAMS):  # 식별자 없는 건 메뉴(예: 성적)
            continue
        if absu in seen:
            continue
        seen.add(absu)
        out.append({"title": clean_post_title(el.get_text()) or "post", "url": absu})
    return out


# ──────────────────────────────────────────────────────────────────────────
# 팀프로젝트: 팀룸 + 제출 레코드
#   ilos 팀프로젝트의 '실제 제출물'은 게시물 페이지가 아니라
#   ① 팀룸(project_team_detail_view_form)과
#   ② 제출 팝업(project_team_detail_submit_pop → 제출일시/본문, efile_list → 첨부)
#   에 있다. 게시물 목록의 '제출여부: 제출' 플래그만으로는 무엇이 제출됐는지 알 수 없다.
# ──────────────────────────────────────────────────────────────────────────
SUBMIT_POP_ACL = "/ilos/st/course/project_team_detail_submit_pop.acl"
EFILE_LIST_ACL = "/ilos/co/efile_list.acl"


def extract_submit_params(html: str):
    """팀프로젝트 게시물 페이지 JS(showSubmitForm)에서 제출 팝업 POST 파라미터를 뽑는다.
    → {ud, ky, PROJECT_SEQ, TEAM_CD, USER_ID, FLAG}. 팀 미배정 등으로 못 찾으면 None."""
    m = re.search(r"project_team_detail_submit_pop\.acl(.{0,700}?)\}\s*,", html or "", re.S)
    block = m.group(1) if m else ""
    out = {}
    for k in ("ud", "ky", "PROJECT_SEQ", "TEAM_CD", "USER_ID", "FLAG"):
        mm = re.search(r"\b" + k + r"""\s*:\s*['"]([^'"]+)['"]""", block)
        if mm:
            out[k] = mm.group(1)
    if not all(out.get(k) for k in ("ud", "ky", "PROJECT_SEQ", "TEAM_CD")):
        return None
    out.setdefault("USER_ID", out["ud"])
    out.setdefault("FLAG", "STU")
    return out


def extract_team_room_url(html: str, base_url: str) -> str:
    """게시물 페이지에서 '우리 팀' 팀룸(project_team_detail_view_form) 절대 URL. 없으면 ''.
    '다른팀 글보기' 허용 과목은 팀룸 링크가 여럿일 수 있어 TEAM_CD==MY_TEAM_CD 인 것을 우선."""
    cands = []
    for m in re.finditer(r"""['"]([^'"]*project_team_detail_view_form\.acl[^'"]*)['"]""",
                         unescape(html or "")):
        absu = urljoin(base_url, m.group(1))
        q = parse_qs(urlparse(absu).query)
        team, mine = (q.get("TEAM_CD") or [""])[0], (q.get("MY_TEAM_CD") or [""])[0]
        if absu not in (c[1] for c in cands):
            cands.append((bool(team) and team == mine, absu))
    if not cands:
        return ""
    cands.sort(key=lambda c: c[0], reverse=True)  # 내 팀 우선
    return cands[0][1]


def parse_submit_popup(html: str):
    """제출 팝업(SUBMIT_POP_ACL 응답) 파싱 →
    {submitted, submitted_at, body_text, file_content_seq}.
    미제출이면 제출일시 행이 없어 submitted=False."""
    h = unescape(html or "")
    out = {"submitted": False, "submitted_at": "", "body_text": "", "file_content_seq": ""}
    m = re.search(r"제출일시\s*</th>\s*<td[^>]*>\s*(.*?)\s*</td>", h, re.S)
    if m:
        out["submitted"] = True
        out["submitted_at"] = clean_text(re.sub(r"<[^>]+>", " ", m.group(1)))
    m = re.search(r"<td\s+colspan[^>]*>\s*(.*?)<div\s+id=\"tbody_file", h, re.S)
    if m:
        out["body_text"] = clean_text(
            BeautifulSoup(m.group(1), "lxml").get_text(separator=" ", strip=True))
    m = re.search(r"efile_list\.acl.{0,400}?CONTENT_SEQ\s*:\s*['\"]([^'\"]+)['\"]", h, re.S)
    if m:
        out["file_content_seq"] = m.group(1)
    return out


def extract_attachments(html: str, base_url: str = ""):
    """첨부파일 → [{filename, token, url}] (token=downloadClick용, url=직접 다운로드 href)."""
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()

    # 1) onclick="downloadClick('TOKEN')"
    for el in soup.find_all(attrs={"onclick": True}):
        m = re.search(r"downloadClick\(['\"]([^'\"]+)['\"]", el.get("onclick") or "")
        if not m:
            continue
        token = m.group(1)
        if ("tok", token) in seen:
            continue
        seen.add(("tok", token))
        name = clean_text(el.get_text()) or el.get("title") or el.get("alt") or ""
        if not name and el.parent:
            name = clean_text(el.parent.get_text())
        out.append({"filename": name[:150], "token": token, "url": None})

    # 2) 직접 다운로드 href (efile_download / *_down.acl 등)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not re.search(r"(efile_download|file_down|/down\.acl|download\.acl)", href, re.I):
            continue
        absu = urljoin(base_url, unescape(href))
        # 같은 파일이 여러 앵커로 걸려 있을 수 있다(FILE_SEQ는 같고 ud/pf_st_flag만 다름).
        # 안정 식별자 FILE_SEQ로 중복 제거(없으면 URL 전체로 폴백).
        key = attachment_key({"url": absu})
        if key in seen:
            continue
        seen.add(key)
        out.append({"filename": (clean_text(a.get_text()) or "")[:150], "token": None, "url": absu})

    return out
