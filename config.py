"""
수집 대상 URL 설정.

- BASE_URL: 처음 브라우저가 열릴 때 이동할 주소(로그인 시작점).
- KNOWN_URLS: --auto 모드에서 자동 순회할 {라벨: URL} 목록.

--auto 는 과목 화면의 메뉴를 직접 찾아 수집하므로 KNOWN_URLS 를 채우지 않아도 됩니다.
아래 목록은 '선택' 입니다:
  - 자동 발견이 특정 메뉴를 못 찾을 때의 폴백,
  - 또는 메뉴에 없는 추가 페이지를 강제로 수집하고 싶을 때.
한 번 --auto 를 돌리면 output/_discovered_urls.json 에 발견된 URL이 기록되니,
고정하고 싶으면 그 값을 여기에 붙여넣으면 됩니다.
"""

# https 로 시작(평문 http 로 다니면 로그인 후 세션 쿠키가 평문으로 노출될 수 있음).
# ilos 가 http 로 리다이렉트하는 경로가 있어도 시작점은 https 로 둔다.
BASE_URL = "https://ecampus.konkuk.ac.kr/"

# 자동 로그인(./ec login 으로 키체인에 자격증명 등록 후 동작)
LOGIN_URL = "https://ecampus.konkuk.ac.kr/ilos/main/member/login_form.acl"
CAMPUS = "서울"            # "서울" | "글로컬" — 로그인 폼의 캠퍼스 라디오

# 로그인 후 메인에서 자동 진입할 과목명 키워드(과목 카드 title 부분일치).
# None 이면 종전처럼 사람이 과목에 들어가고 Enter.
AUTO_COURSE = "클라우드IOT서비스"

KNOWN_URLS = {
    # 비워둬도 됩니다 — --auto 가 과목 좌측 메뉴(/ilos/st/course/..._form.acl)를
    # 직접 찾아 전부 수집합니다. 메뉴에 안 뜨는 페이지를 강제로 받고 싶을 때만 추가하세요.
    # "특강자료": "http://ecampus.konkuk.ac.kr/ilos/st/course/....acl",
}
