"""ilos(eCampus) HTTP 클라이언트 — 브라우저 없이 로그인·과목 입장·페이지 조회·첨부 다운로드.

Playwright 흐름(collect.py)과 달리 쿠키 세션 하나로 .acl 엔드포인트를 직접 부른다.
로그인은 로그인 폼이 쓰는 JSONP POST(/ilos/lo/login.acl) 그대로이고, 과목 입장은
메인 화면 과목 카드의 eclassRoom(KJKEY) 이 부르는 /ilos/st/course/eclass_room2.acl 이다.

    from api import Client
    c = Client.from_keychain()
    for course in c.courses(): ...
    c.enter(course["kj"])
    html = c.get("/ilos/st/course/notice_list_form.acl")

주의: ilos 는 중복 로그인 시 기존 세션을 끊는다(브라우저로 보던 사람이 로그아웃된다).
"""
import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request

import config
import parse

ORIGIN = "https://ecampus.konkuk.ac.kr"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
CAMPUS_DIV = {"서울": "1", "글로컬": "2"}


class LoginError(RuntimeError):
    pass


class SessionDead(RuntimeError):
    """응답이 '다시 로그인' 페이지다(세션 만료·중복 로그인으로 끊김)."""


def is_session_dead(text: str) -> bool:
    """세션 만료 응답인가. 만료되면 alert + redirect 만 있는 작은 페이지가 온다.
    정상 페이지(과제 상세 등)는 임시저장 실패 안내 같은 같은 문구를 스크립트에 품고 있으므로
    길이로 거른다."""
    if len(text) > 4000:
        return False
    return ("다시 로그인" in text) or ("접속이 종료" in text) or ("로그인 하세요" in text)


def parse_jsonp(body: str) -> dict:
    """`jsonLogin ({...});` 형태 응답에서 JSON 객체를 꺼낸다(키가 따옴표 없이 오지 않는다)."""
    m = re.search(r"\{.*\}", body, re.S)
    if not m:
        raise ValueError("JSON 본문이 없다: %r" % body[:120])
    return json.loads(m.group(0))


def parse_courses(html: str) -> list:
    """메인 화면에서 [{kj, title}] — 과목 카드 `<em ... title="X 강의실 들어가기" kj="...">`."""
    out, seen = [], set()
    for tag in re.findall(r"<[^>]*\bkj=\"[^\"]*\"[^>]*>", html):
        kj = re.search(r'\bkj="([^"]*)"', tag).group(1)
        if kj in seen:
            continue
        seen.add(kj)
        t = re.search(r'\btitle="([^"]*)"', tag)
        title = re.sub(r"\s*강의실 들어가기\s*$", "", t.group(1)) if t else ""
        out.append({"kj": kj, "title": title})
    return out


class Client:
    def __init__(self, uid: str, pw: str, campus: str = None):
        self._uid, self._pw = uid, pw
        self.campus = campus or config.CAMPUS
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.addheaders = [("User-Agent", UA), ("Accept-Language", "ko-KR,ko;q=0.9")]
        self.logged_in = False

    @classmethod
    def from_keychain(cls, campus: str = None):
        import collect
        uid, pw = collect.keychain_creds()
        if not uid:
            raise LoginError("키체인 미등록 — ./ec login 으로 등록하세요.")
        return cls(uid, pw, campus)

    # ── 요청 ────────────────────────────────────────────────────────────
    def _url(self, path: str) -> str:
        return path if path.startswith("http") else ORIGIN + path

    def _open(self, path, data=None, referer=None, timeout=30):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        headers = {"Referer": referer or ORIGIN + "/ilos/main/main_form.acl"}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            headers["X-Requested-With"] = "XMLHttpRequest"
        req = urllib.request.Request(self._url(path), data=body, headers=headers)
        return self.opener.open(req, timeout=timeout)

    def get(self, path: str, referer: str = None) -> str:
        with self._open(path, None, referer) as r:
            text = r.read().decode("utf-8", "ignore")
        if is_session_dead(text):
            raise SessionDead(path)
        return text

    def post(self, path: str, data: dict = None, referer: str = None) -> str:
        with self._open(path, {**(data or {}), "encoding": "utf-8"}, referer) as r:
            text = r.read().decode("utf-8", "ignore")
        if is_session_dead(text):
            raise SessionDead(path)
        return text

    # ── 로그인·과목 ─────────────────────────────────────────────────────
    def login(self) -> None:
        login_form = ORIGIN + "/ilos/main/member/login_form.acl"
        self._open(login_form).read()                       # JSESSIONID 발급
        data = {"usr_id": self._uid, "usr_pwd": self._pw, "returnURL": "",
                "campus_div": CAMPUS_DIV.get(self.campus, "1"), "encoding": "utf-8",
                "data": "jsonLogin"}
        with self._open("/ilos/lo/login.acl", data, referer=login_form) as r:
            res = parse_jsonp(r.read().decode("utf-8", "ignore"))
        if res.get("isError"):
            raise LoginError(res.get("message") or "로그인 실패")
        if res.get("VERIFY"):
            raise LoginError("추가 인증(VERIFY)이 필요하다 — 브라우저로 한 번 로그인해 처리하세요.")
        self.logged_in = True

    def courses(self) -> list:
        return parse_courses(self.get("/ilos/main/main_form.acl"))

    def enter(self, kj: str) -> str:
        """과목방 입장(세션에 현재 과목이 박힌다). 반환: 입장 후 이동할 URL."""
        res = parse_jsonp(self.post("/ilos/st/course/eclass_room2.acl", {
            "KJKEY": kj, "returnData": "json",
            "returnURI": "/ilos/st/course/submain_form.acl"}))
        if res.get("isError"):
            raise RuntimeError(res.get("message") or "과목 입장 실패")
        return res.get("returnURL") or ""

    # ── 목록·첨부 ───────────────────────────────────────────────────────
    def list_posts(self, form_path: str, max_posts: int = 40, referer: str = None) -> list:
        """*_list_form.acl 메뉴의 글 목록 [{title, url}] — 브라우저가 쓰는 AJAX 를 그대로 재현한다.
        AJAX 가 없는 메뉴는 폼 HTML 자체에서 글 링크를 뽑는다. 페이지는 새 글이 안 나올 때까지 넘긴다."""
        base = ORIGIN + form_path.rsplit("/", 1)[0] + "/"
        form = self.get(form_path, referer)
        url, data, paged = parse_list_ajax(form, form_path)
        if not url:
            return parse.extract_post_links(form, base)[:max_posts]
        out, seen, page = [], set(), 1
        while len(out) < max_posts and page <= 50:
            frag = self.post(url, {**data, "start": page} if paged else data, referer=ORIGIN + form_path)
            # 링크 URL 에 start= 가 박혀 있어 URL 로는 중복을 못 거른다 — 글 식별자로 거른다
            fresh = [p for p in parse.extract_post_links(frag, base)
                     if parse.post_id_from_url(p["url"]) not in seen]
            if not fresh:
                break
            seen.update(parse.post_id_from_url(p["url"]) for p in fresh)
            out.extend(fresh)
            if not paged:
                break
            page += 1
        return out[:max_posts]

    def post_attachments(self, post_html: str, post_url: str) -> list:
        """글 상세 HTML 의 첨부 [{filename, token, url}] — 본문에 박힌 것 + efile_list AJAX 로 오는 것."""
        atts = parse.extract_attachments(post_html, post_url)
        have = {parse.attachment_key(a) for a in atts}
        for args in parse_efile_calls(post_html):
            frag = self.post("/ilos/co/efile_list.acl", args, referer=post_url)
            for a in parse.extract_attachments(frag, post_url):
                if parse.attachment_key(a) not in have:
                    have.add(parse.attachment_key(a))
                    atts.append(a)
        return atts

    # ── 첨부 ────────────────────────────────────────────────────────────
    def download(self, url: str, referer: str = None):
        """첨부 1개. 반환: (파일명 or None, bytes). 세션 만료 페이지면 SessionDead."""
        with self._open(url, None, referer, timeout=120) as r:
            ctype = r.headers.get("Content-Type", "")
            disp = r.headers.get("Content-Disposition", "")
            blob = r.read()
        if "text/html" in ctype and not disp:
            text = blob.decode("utf-8", "ignore")
            if is_session_dead(text):
                raise SessionDead(url)
            return None, blob
        return filename_from_disposition(disp), blob


def _static_args(block: str) -> dict:
    """ajax data 블록의 `키 : "값"` 쌍(JS 식 `$(...)` 가 값인 건 건너뜀)."""
    return {k: v for k, v in re.findall(r"""(\w+)\s*:\s*["']([^"']*)["']""", block)}


def parse_list_ajax(form_html: str, form_path: str):
    """메뉴 폼(*_form.acl)이 브라우저에서 AJAX 로 채우는 목록 호출 → (url, data, paged).
    ilos 는 폼 `X_list_form.acl` 에 목록 `X_list.acl` 을 대응시키고, 호출부(listPage·attendList …)의
    함수 이름은 메뉴마다 다르다. 그래서 함수 이름 대신 그 URL 을 부르는 $.ajax 블록을 찾는다.
    data 는 따옴표 값만 가져오고 JS 식(`$("#SCH_VALUE").val()`)은 빈 문자열로 둔다.
    paged 는 호출부가 start 인자를 쓰는지. 해당 AJAX 가 없으면 (None, None, False)
    — 서버가 목록을 HTML 에 직접 그려 준 메뉴."""
    list_url = re.sub(r"_form\.acl.*$", ".acl", form_path.split("?")[0])
    for m in re.finditer(r"""url\s*:\s*["']([^"']+)["'](.{0,300}?)data\s*:\s*\{(.*?)\}""", form_html or "", re.S):
        if m.group(1) != list_url:
            continue
        block = m.group(3)
        data = _static_args(block)
        for k, v in re.findall(r"(\w+)\s*:\s*([^,\n}]+)", block):
            if k not in data and k not in ("start", "encoding"):
                data[k] = ""                     # JS 식 값
        data.pop("encoding", None)
        return list_url, data, bool(re.search(r"\bstart\s*:", block))
    return None, None, False


def parse_efile_calls(post_html: str) -> list:
    """글 상세의 첨부 AJAX(/ilos/co/efile_list.acl) 호출들의 인자 [dict, ...].
    과제 상세처럼 한 글에 여러 번(출제 첨부·참고자료·제출 첨부) 부르는 화면도 있다."""
    out = []
    for m in re.finditer(r"/ilos/co/efile_list\.acl.{0,200}?data\s*:\s*\{(.*?)\}", post_html or "", re.S):
        args = _static_args(m.group(1))
        args.pop("encoding", None)
        if args.get("CONTENT_SEQ") and args not in out:
            out.append(args)
    return out


def filename_from_disposition(disp: str):
    """Content-Disposition 에서 파일명(RFC 5987 filename*= 우선, 없으면 filename=)."""
    m = re.search(r"filename\*\s*=\s*[\w-]+''([^;]+)", disp)
    if m:
        return urllib.parse.unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"?([^";]+)"?', disp)
    if not m:
        return None
    raw = m.group(1).strip()
    for enc in ("utf-8", "euc-kr"):                 # 서버가 헤더 바이트를 latin-1 로 내려줄 때가 있다
        try:
            return urllib.parse.unquote(raw).encode("latin-1").decode(enc)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return urllib.parse.unquote(raw)
