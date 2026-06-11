#!/usr/bin/env python3
"""재실행 중복 방지 단위 테스트 (네트워크/Playwright 불필요).

output/클라우드IOT서비스 의 실제 캡처(page.html, attachments.json)로 검증한다:
  - 제목에서 '작성자 조회 N' 꼬리 제거 → 조회수가 바뀌어도 폴더명 동일
  - 게시물 폴더명이 URL 식별자(ARTL_NUM/PROJECT_SEQ …) 기반으로 안정
  - 이미 받은 첨부(FILE_SEQ/token)는 인덱스에 잡혀 스킵 대상이 됨

실행:  .venv/bin/python -m unittest test_parse -v
"""

import json
import re
import tempfile
import unittest
from pathlib import Path

import collect
import parse

ROOT = Path(__file__).resolve().parent
CAPTURE = ROOT / "output" / "클라우드IOT서비스"
BASE = "http://ecampus.konkuk.ac.kr/ilos/st/course/notice_list_form.acl"

has_capture = CAPTURE.exists()


def read_capture(rel: str) -> str:
    return (CAPTURE / rel).read_text(encoding="utf-8")


def bump_views(html: str) -> str:
    """재실행 시뮬레이션: 모든 '조회 N'을 N+1로 바꾼 목록 HTML."""
    return re.sub(r"조회\s*(\d+)", lambda m: f"조회 {int(m.group(1)) + 1}", html)


# ──────────────────────────────────────────────────────────────────────────
# 순수 함수 (캡처 불필요)
# ──────────────────────────────────────────────────────────────────────────
class TestCleanPostTitle(unittest.TestCase):
    def test_strips_author_views_tail(self):
        self.assertEqual(
            parse.clean_post_title("[중요 공지] 마감을 놓친 보고서 제출에 관해서 정갑주 조회 119"),
            "[중요 공지] 마감을 놓친 보고서 제출에 관해서")

    def test_views_change_does_not_change_title(self):
        a = parse.clean_post_title("03.05: 강의소개 및 계획 정갑주 조회 266")
        b = parse.clean_post_title("03.05: 강의소개 및 계획 정갑주 조회 267")
        self.assertEqual(a, b)
        self.assertEqual(a, "03.05: 강의소개 및 계획")

    def test_strips_submit_badge(self):
        self.assertEqual(
            parse.clean_post_title("프로젝트 #4: Amazon Cloud 서비스 사례조사 팀장제출"),
            "프로젝트 #4: Amazon Cloud 서비스 사례조사")
        self.assertEqual(parse.clean_post_title("프로젝트 #0: 팀 편성 개별제출"),
                         "프로젝트 #0: 팀 편성")

    def test_plain_title_unchanged(self):
        self.assertEqual(parse.clean_post_title("Fitbit 기기 수령"), "Fitbit 기기 수령")
        # '제출'로 끝나는 일반 제목은 건드리지 않는다(배지는 '팀장제출/개별제출'만)
        self.assertEqual(parse.clean_post_title("기말 보고서 제출"), "기말 보고서 제출")

    def test_never_returns_empty_for_degenerate_input(self):
        self.assertTrue(parse.clean_post_title("조회 5"))


class TestPostIdFromUrl(unittest.TestCase):
    def test_artl_num(self):
        u = BASE.replace("_list_", "_view_") + "?ARTL_NUM=13436775&SCH_KEY=&display=1&start=1"
        self.assertEqual(parse.post_id_from_url(u), "13436775")

    def test_project_seq(self):
        u = ("http://ecampus.konkuk.ac.kr/ilos/st/course/project_view_form.acl"
             "?PROJECT_SEQ=13679354&start=1&display=1")
        self.assertEqual(parse.post_id_from_url(u), "13679354")

    def test_no_id(self):
        self.assertEqual(parse.post_id_from_url(BASE), "")


class TestAttachmentKey(unittest.TestCase):
    def test_file_seq_ignores_session_params(self):
        a = {"url": "http://x/ilos/co/efile_download.acl?FILE_SEQ=ABC&ky=K1&ud=111&pf_st_flag=2"}
        b = {"url": "http://x/ilos/co/efile_download.acl?FILE_SEQ=ABC&ky=K2&ud=222&pf_st_flag=1"}
        self.assertEqual(parse.attachment_key(a), parse.attachment_key(b))
        self.assertEqual(parse.attachment_key(a), ("file", "ABC"))

    def test_token(self):
        self.assertEqual(parse.attachment_key({"url": None, "token": "T9"}), ("tok", "T9"))

    def test_none(self):
        self.assertIsNone(parse.attachment_key({"url": None, "token": None}))


class TestPostDirName(unittest.TestCase):
    def test_id_based_and_stable(self):
        post = {"title": "03.05: 강의소개 및 계획",
                "url": "http://x/notice_view_form.acl?ARTL_NUM=13436775"}
        name = collect.post_dir_name(13, post)
        self.assertEqual(name, "13436775_03.05_ 강의소개 및 계획")
        self.assertEqual(name, collect.post_dir_name(99, post))  # 목록 순번과 무관

    def test_fallback_to_index_without_id(self):
        post = {"title": "제목", "url": "http://x/notice_view_form.acl"}
        self.assertEqual(collect.post_dir_name(3, post), "03_제목")


class TestLoadDownloadedIndex(unittest.TestCase):
    def test_index_filters_and_path_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            menu = Path(td) / "공지사항"
            pdir = menu / "posts" / "111_옛글"
            (pdir / "files").mkdir(parents=True)
            real = pdir / "files" / "a.pdf"
            real.write_bytes(b"x" * 10)
            records = [
                # 받았고 파일도 있음 — 기록된 path는 옛 CWD 기준이라 깨져 있음 → 폴백으로 찾아야 함
                {"filename": "a.pdf", "token": None, "downloaded": True,
                 "url": "http://x/efile_download.acl?FILE_SEQ=KEEP&ud=1",
                 "path": "output/딴데/posts/111_옛글/files/a.pdf"},
                # 받았다고 기록됐지만 파일이 사라짐 → 인덱스 제외(다시 받아야 함)
                {"filename": "b.pdf", "token": None, "downloaded": True,
                 "url": "http://x/efile_download.acl?FILE_SEQ=GONE&ud=1",
                 "path": str(pdir / "files" / "b.pdf")},
                # 못 받은 기록 → 인덱스 제외
                {"filename": "c.pdf", "token": None, "downloaded": False,
                 "url": "http://x/efile_download.acl?FILE_SEQ=FAIL&ud=1", "path": None},
            ]
            (pdir / "attachments.json").write_text(
                json.dumps(records, ensure_ascii=False), encoding="utf-8")

            idx = collect.load_downloaded_index(menu)
            self.assertEqual(set(idx), {("file", "KEEP")})
            self.assertEqual(idx[("file", "KEEP")], str(real))

    def test_missing_posts_dir(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(collect.load_downloaded_index(Path(td) / "없음"), {})

    def test_indexes_team_project_submission_files(self):
        """팀프로젝트 제출 첨부(submission.json)도 인덱스에 잡혀야 한다.
        안 그러면 재실행마다 submission_files/ 에 _2,_3 사본이 쌓인다(회귀 방지)."""
        with tempfile.TemporaryDirectory() as td:
            menu = Path(td) / "팀프로젝트"
            pdir = menu / "posts" / "10000000_프로젝트 #6"
            (pdir / "submission_files").mkdir(parents=True)
            f = pdir / "submission_files" / "소스코드.zip"
            f.write_bytes(b"z" * 20)
            sub = {"post_url": "http://x", "submitted": True,
                   "attachments": [
                       {"filename": "소스코드.zip", "token": None, "downloaded": True,
                        "url": "http://x/efile_download.acl?FILE_SEQ=ZIPSEQ&ud=1",
                        # 기록 path 는 옛 CWD 기준으로 깨져 있어도 폴백으로 찾아야 함
                        "path": "output/딴데/posts/x/submission_files/소스코드.zip"},
                       {"filename": "없어진것.pdf", "token": None, "downloaded": True,
                        "url": "http://x/efile_download.acl?FILE_SEQ=MISSING&ud=1",
                        "path": str(pdir / "submission_files" / "없어진것.pdf")},
                   ]}
            (pdir / "submission.json").write_text(
                json.dumps(sub, ensure_ascii=False), encoding="utf-8")

            idx = collect.load_downloaded_index(menu)
            self.assertEqual(set(idx), {("file", "ZIPSEQ")})
            self.assertEqual(idx[("file", "ZIPSEQ")], str(f))


SUBMIT_POP_SAMPLE = """
<html lang="ko">
<script type="text/JavaScript">
function insertGo(){
  $.ajax({ url: "/ilos/st/course/project_insert.acl", type: "POST",
    data: { ud : "200000000", ky : "A00000000000000000000", returnData : "json",
      PROJECT_SEQ : "10000000", TEAM_CD : "100000", TXT : txt, encoding : "utf-8" },
  });
}
</script>
<div id="submit_div">
  <table class="bbswrite" border="1">
    <caption>과제물 게시판 내용 작성하기</caption>
    <tbody>
      <tr><th scope="row">제출일시</th><td>2026.06.11 오후 10:31:40</td></tr>
      <tr><td colspan="2" style="padding:10px;">
        <div><p>.</p></div>
        <div id="tbody_file2"></div>
      </td></tr>
    </tbody>
  </table>
  <script>
  $.ajax({ url: "/ilos/co/efile_list.acl", type: "POST",
    data: { CONTENT_SEQ : "CONTENTSEQABC123", encoding : "utf-8" },
  });
  </script>
  <div class="site_button" id="uptBtn" title="수정">수정</div>
</div>
"""

PROJECT_VIEW_SAMPLE = """
<script>
function showSubmitForm(){
  $.ajax({
    url: "/ilos/st/course/project_team_detail_submit_pop.acl",
    type: "POST",
    data: {
      ud : "200000000",
      ky : "A00000000000000000000",
      PROJECT_SEQ : "10000000",
      TEAM_CD : "100000",
      USER_ID : "200000000",
      FLAG : "STU",
      encoding : "utf-8"
          },
    async: false,
  });
}
</script>
<a href="/ilos/st/course/project_team_detail_view_form.acl?PROJECT_SEQ=10000000&amp;TEAM_CD=100000&amp;SHARE_YN=N&amp;MY_TEAM_CD=100000&amp;display=1&amp;start=1&amp;week=">입장</a>
"""


class TestTeamProjectParsing(unittest.TestCase):
    BASE = "http://ecampus.konkuk.ac.kr/ilos/st/course/project_view_form.acl?PROJECT_SEQ=10000000"

    def test_extract_submit_params(self):
        p = parse.extract_submit_params(PROJECT_VIEW_SAMPLE)
        self.assertEqual(p["ud"], "200000000")
        self.assertEqual(p["ky"], "A00000000000000000000")
        self.assertEqual(p["PROJECT_SEQ"], "10000000")
        self.assertEqual(p["TEAM_CD"], "100000")
        self.assertEqual(p["FLAG"], "STU")

    def test_extract_submit_params_absent(self):
        self.assertIsNone(parse.extract_submit_params("<html>팀 미배정</html>"))

    def test_extract_team_room_url(self):
        u = parse.extract_team_room_url(PROJECT_VIEW_SAMPLE, self.BASE)
        self.assertIn("project_team_detail_view_form.acl", u)
        self.assertIn("TEAM_CD=100000", u)
        self.assertTrue(u.startswith("http://ecampus.konkuk.ac.kr/"))

    def test_team_room_url_prefers_my_team(self):
        html = """
        <a href="/ilos/st/course/project_team_detail_view_form.acl?PROJECT_SEQ=1&TEAM_CD=999&MY_TEAM_CD=100000">다른팀</a>
        <a href="/ilos/st/course/project_team_detail_view_form.acl?PROJECT_SEQ=1&TEAM_CD=100000&MY_TEAM_CD=100000">우리팀</a>
        """
        u = parse.extract_team_room_url(html, self.BASE)
        self.assertIn("TEAM_CD=100000&MY_TEAM_CD=100000", u)

    def test_parse_submit_popup_submitted(self):
        r = parse.parse_submit_popup(SUBMIT_POP_SAMPLE)
        self.assertTrue(r["submitted"])
        self.assertEqual(r["submitted_at"], "2026.06.11 오후 10:31:40")
        self.assertEqual(r["body_text"], ".")
        self.assertEqual(r["file_content_seq"], "CONTENTSEQABC123")
        # 본문 추출이 insertGo 쪽 PROJECT_SEQ 값에 오염되지 않아야 한다
        self.assertNotIn("10000000", r["body_text"])

    def test_parse_submit_popup_not_submitted(self):
        r = parse.parse_submit_popup("<div id='submit_div'>아직 제출 전</div>")
        self.assertFalse(r["submitted"])
        self.assertEqual(r["file_content_seq"], "")

    def test_efile_list_attachments(self):
        fl = """
        <div class="attfile-list">
          <a class="site-link" href="/ilos/co/efile_download.acl?FILE_SEQ=FG34PIU6BLEEM&amp;CONTENT_SEQ=10000001&amp;ky=K&amp;ud=1&amp;pf_st_flag=">- 소스코드.zip (266.6KB)</a>
          <a class="site-link" href="/ilos/co/efile_download.acl?FILE_SEQ=NXJWOESLFGHZK&amp;CONTENT_SEQ=10000001&amp;ky=K&amp;ud=1&amp;pf_st_flag=">- 보고서.pdf (1.3MB)</a>
        </div>"""
        atts = parse.extract_attachments(fl, self.BASE)
        self.assertEqual(len(atts), 2)
        self.assertTrue(all(a["url"].startswith("http://ecampus.konkuk.ac.kr/ilos/co/")
                            for a in atts))
        self.assertEqual(parse.attachment_key(atts[0]), ("file", "FG34PIU6BLEEM"))


class TestExtractAttachmentsDedup(unittest.TestCase):
    def test_same_file_seq_two_anchors_one_record(self):
        html = """
        <a href="/ilos/co/efile_download.acl?FILE_SEQ=F1&ud=1&pf_st_flag=2">- a.pdf (1KB)</a>
        <a href="/ilos/co/efile_download.acl?FILE_SEQ=F1&ud=2&pf_st_flag=1">a.pdf</a>
        <span onclick="downloadClick('T1')">b.zip</span>
        <span onclick="downloadClick('T1')">b.zip(중복)</span>
        """
        atts = parse.extract_attachments(html, "http://x/")
        keys = [parse.attachment_key(a) for a in atts]
        self.assertEqual(sorted(keys), [("file", "F1"), ("tok", "T1")])


# ──────────────────────────────────────────────────────────────────────────
# 실제 캡처 기반 (output/클라우드IOT서비스 필요)
# ──────────────────────────────────────────────────────────────────────────
@unittest.skipUnless(has_capture, "output/클라우드IOT서비스 캡처 없음")
class TestWithCapturedPages(unittest.TestCase):
    def test_notice_titles_have_no_view_count(self):
        posts = parse.extract_post_links(read_capture("공지사항/page.html"), BASE)
        self.assertEqual(len(posts), 13)
        for p in posts:
            self.assertNotRegex(p["title"], r"조회\s*[\d,]+$", p["title"])

    def test_rerun_with_bumped_views_same_folders(self):
        """조회수만 바뀐 재실행에서 게시물 폴더명이 전부 동일해야 한다(핵심 회귀)."""
        for menu in ("공지사항", "강의자료", "팀프로젝트"):
            html = read_capture(f"{menu}/page.html")
            run1 = parse.extract_post_links(html, BASE)
            run2 = parse.extract_post_links(bump_views(html), BASE)
            names1 = [collect.post_dir_name(i, p) for i, p in enumerate(run1, 1)]
            names2 = [collect.post_dir_name(i, p) for i, p in enumerate(run2, 1)]
            self.assertTrue(run1, menu)
            self.assertEqual(names1, names2, menu)
            self.assertEqual(len(names1), len(set(names1)), f"{menu}: 폴더명 충돌")

    def test_folder_names_are_post_id_based(self):
        posts = parse.extract_post_links(read_capture("팀프로젝트/page.html"), BASE)
        self.assertEqual(len(posts), 9)
        for i, p in enumerate(posts, 1):
            pid = parse.post_id_from_url(p["url"])
            self.assertTrue(pid, p["url"])
            self.assertTrue(collect.post_dir_name(i, p).startswith(pid + "_"))

    def test_downloaded_index_covers_existing_files(self):
        """기존 attachments.json에서 만든 인덱스가, 그 글 page.html을 다시 파싱해 나온
        첨부의 key를 그대로 커버해야 한다(= 재실행 시 전부 스킵)."""
        menu = CAPTURE / "공지사항"
        idx = collect.load_downloaded_index(menu)
        self.assertTrue(idx)

        checked = 0
        for aj in sorted((menu / "posts").glob("*/attachments.json")):
            records = json.loads(aj.read_text(encoding="utf-8"))
            done = [r for r in records if r.get("downloaded") and r.get("path")]
            if not done:
                continue
            page_html = (aj.parent / "page.html").read_text(encoding="utf-8")
            atts = parse.extract_attachments(
                page_html, "http://ecampus.konkuk.ac.kr/ilos/st/course/notice_view_form.acl")
            keys = {parse.attachment_key(a) for a in atts} - {None}
            for r in done:
                key = parse.attachment_key(r)
                self.assertIn(key, keys, f"{aj.parent.name}: 기록과 재파싱 key 불일치")
                if Path(r["path"]).exists() or (aj.parent / "files" / Path(r["path"]).name).exists():
                    self.assertIn(key, idx, f"{aj.parent.name}: 인덱스 누락")
                    checked += 1
        self.assertGreater(checked, 0, "검증된 첨부가 없음")


if __name__ == "__main__":
    unittest.main(verbosity=2)
