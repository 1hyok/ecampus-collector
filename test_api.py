#!/usr/bin/env python3
"""HTTP(API) 수집 경로의 순수 함수 단위 테스트 (네트워크·키체인·Playwright 불필요).

실행:  .venv/bin/python -m unittest test_api -v
"""
import unittest
from pathlib import Path
import tempfile

import api
import collect_api
import notify

LIST_FORM = """
<script>
function listPage(start){
  $.ajax({
    url: "/ilos/st/course/lecture_material_list.acl",
    type: "POST",
    data: {
      start : start,
      display : "1",
      SCH_VALUE : $("#SCH_VALUE").val(),
      ud : "202111373",
      ky : "A20263BBAB120013154003",
      encoding : "utf-8"
          },
    async: true
  });
}
</script>"""

POST_PAGE = """
<script>
$.ajax({ url: "/ilos/co/efile_list.acl", type: "POST",
  data: { ud : "1", ky : "K", pf_st_flag : "2", CONTENT_SEQ : "AAA", encoding : "utf-8" }, async: false });
$.ajax({ url: "/ilos/co/efile_list.acl", type: "POST",
  data: { ud : "1", ky : "K", pf_st_flag : "2", CONTENT_SEQ : "BBB", TURNITIN_SEQ : "", encoding : "utf-8" }, async: false });
$.ajax({ url: "/ilos/co/efile_list.acl", type: "POST",
  data: { ud : "1", ky : "K", pf_st_flag : "2", CONTENT_SEQ : "AAA", encoding : "utf-8" }, async: false });
</script>"""


class TestLogin(unittest.TestCase):
    def test_parse_jsonp(self):
        body = '\n\njsonLogin ({\n"isError":false,\n"message":"로그인 성공.",\n"VERIFY"  : ""\n});\n'
        res = api.parse_jsonp(body)
        self.assertFalse(res["isError"])
        self.assertEqual(res["VERIFY"], "")

    def test_parse_jsonp_without_object(self):
        with self.assertRaises(ValueError):
            api.parse_jsonp("<html></html>")

    def test_parse_courses(self):
        html = ('<em onclick="eclassRoom(\'K1\')" title="데이터베이스 강의실 들어가기" kj="K1" kj_auth="S">'
                '<em title="x" kj="K1"><em title="[학부생5] 폭력예방교육 강의실 들어가기" kj="N2">')
        self.assertEqual(api.parse_courses(html), [
            {"kj": "K1", "title": "데이터베이스"},
            {"kj": "N2", "title": "[학부생5] 폭력예방교육"}])


class TestSessionDead(unittest.TestCase):
    def test_small_alert_page_is_dead(self):
        self.assertTrue(api.is_session_dead('<script>alert("접속이 종료되었습니다. 다시 로그인 하세요");</script>'))

    def test_big_page_with_same_phrase_is_alive(self):
        # 과제 상세는 임시저장 실패 안내 문구를 스크립트에 품고 있다
        page = '<script>alert("다시 로그인 하세요")</script>' + "x" * 5000
        self.assertFalse(api.is_session_dead(page))


class TestAjaxReplay(unittest.TestCase):
    def test_parse_list_ajax(self):
        url, data = api.parse_list_ajax(LIST_FORM)
        self.assertEqual(url, "/ilos/st/course/lecture_material_list.acl")
        self.assertEqual(data["ud"], "202111373")
        self.assertEqual(data["ky"], "A20263BBAB120013154003")
        self.assertEqual(data["display"], "1")
        self.assertEqual(data["SCH_VALUE"], "")        # JS 식 값은 빈 문자열로
        self.assertNotIn("start", data)                # 호출 때 채운다
        self.assertNotIn("encoding", data)

    def test_parse_list_ajax_absent(self):
        self.assertEqual(api.parse_list_ajax("<html>서버 렌더링</html>"), (None, None))

    def test_parse_efile_calls_collects_all_and_dedups(self):
        calls = api.parse_efile_calls(POST_PAGE)
        self.assertEqual([c["CONTENT_SEQ"] for c in calls], ["AAA", "BBB"])
        self.assertEqual(calls[1]["TURNITIN_SEQ"], "")
        self.assertNotIn("encoding", calls[0])

    def test_parse_efile_calls_none(self):
        self.assertEqual(api.parse_efile_calls("<html></html>"), [])


class TestDownloadName(unittest.TestCase):
    def test_rfc5987(self):
        d = "attachment; filename*=UTF-8''%EA%B0%95%EC%9D%98.pdf"
        self.assertEqual(api.filename_from_disposition(d), "강의.pdf")

    def test_plain(self):
        self.assertEqual(api.filename_from_disposition('attachment; filename="a b.pdf"'), "a b.pdf")

    def test_missing(self):
        self.assertIsNone(api.filename_from_disposition(""))


class TestCollectApi(unittest.TestCase):
    def test_online_weeks(self):
        html = '<div id="week-3" class="wb"></div><div id="week-4"></div><div id="week-3"></div>'
        self.assertEqual(collect_api.online_weeks(html), ["3", "4"])

    def test_pick_courses_default_skips_bracketed(self):
        cards = [{"kj": "1", "title": "알고리즘"}, {"kj": "2", "title": "[학부생5] 폭력예방교육"}]
        self.assertEqual([c["kj"] for c in collect_api.pick_courses(cards)], ["1"])

    def test_pick_courses_filter(self):
        cards = [{"kj": "1", "title": "알고리즘"}, {"kj": "2", "title": "컴퓨터네트워크1"},
                 {"kj": "3", "title": "[학부생5] 폭력예방교육"}]
        got = collect_api.pick_courses(cards, "알고리즘, 폭력")
        self.assertEqual([c["kj"] for c in got], ["1", "3"])

    def test_capture_html_layout(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "공지사항"
            meta = collect_api.capture_html(
                out, [("http://x/form", "<html><body><table><tr><th>제목</th></tr><tr><td>a</td></tr></table></body></html>"),
                      ("http://x/frag", "<p>조각</p><script>var z=1</script>")], "공지")
            for name in ("content.txt", "tables.json", "links.json", "page.html", "meta.json"):
                self.assertTrue((out / name).exists(), name)
            self.assertTrue((out / "frames" / "frame_01.html").exists())
            text = (out / "content.txt").read_text(encoding="utf-8")
            self.assertIn("===== FRAME: http://x/frag =====", text)
            self.assertIn("조각", text)
            self.assertNotIn("var z", text)
            self.assertEqual(meta["table_count"], 1)


class TestNotify(unittest.TestCase):
    def test_html_to_lines_matches_parse_notifications(self):
        html = """
        <div class="notification_title">2026.10.10 토요일</div>
        <div class="notification_content"><ul><li>
          <div class="notification_subject"><span>
             [서울] 컴퓨터공학세미나(001)
          </span></div>
          <div class="notification_text"><span>[과제]</span>
             새로운 과제가 있습니다. "7주차" (기간:2026.10.10 오후 3:32 ~ 2026.10.22 오후 11:59)
          </div>
          <div class="notification_day"><div class="time"></div><span>3일 전 오후 3:32</span></div>
        </li></ul></div>"""
        notis = notify.parse_notifications(notify.html_to_lines(html))
        self.assertEqual(len(notis), 1)
        n = notis[0]
        self.assertEqual((n["course"], n["kind"], n["when"]), ("컴퓨터공학세미나(001)", "과제", "3일 전 오후 3:32"))
        self.assertTrue(n["title"].startswith("새로운 과제가 있습니다."))


if __name__ == "__main__":
    unittest.main(verbosity=2)
