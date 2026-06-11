# ecampus-collector

Konkuk e-campus(ilos LMS)에서 내 수강 과목의 **공지·강의자료·과제·일정(+강의계획서)**을
로컬 파일로 수집하는 도구. 저장된 결과를 Claude Code에게 읽혀 *"마감 임박/놓친 항목"*을
더블체크하는 용도.

## 핵심 원칙
- **비밀번호는 파일/레포에 절대 안 남긴다.** 자동 로그인을 켜도 자격증명은 macOS
  **키체인**에만 저장된다(`./ec login` 으로 1회 등록). 키체인이 없으면 종전처럼
  headful 브라우저에서 사람이 직접 로그인하는 흐름으로 폴백.
- ilos 세션 쿠키는 **브라우저를 닫으면 사라진다**(중복 로그인 시 기존 세션도 끊김)
  → 매 실행 로그인이 필요해서 자동 로그인이 있는 것.
- ilos는 frame/iframe/frameset을 자주 쓰므로 **모든 frame**을 긁는다.
- 과목 깊은 링크(`/ilos/st/course/...`)는 **과목방에 입장한 세션에서만** 동작한다.
  `--auto` 는 `config.AUTO_COURSE` 과목 카드를 찾아 자동 입장까지 한다.

## 설치 (최초 1회)
```bash
cd ~/개발/프로젝트/ecampus-collector
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```
> ⚠️ **프로젝트 폴더를 옮기면 `.venv`가 깨집니다**(venv는 경로를 하드코딩함).
> 옮긴 뒤 "Playwright가 설치되어 있지 않습니다" 가 뜨면 venv만 다시 만드세요:
> `rm -rf .venv && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
> (브라우저는 캐시에 남아 있어 `playwright install` 재실행은 보통 불필요)

## 사용법

> 💡 **그냥 `./ec` 하나만 기억하세요.** venv 활성화 없이 바로 실행됩니다.
> `./ec` = 자동 수집, `./ec login` = 자동 로그인 등록(1회), `./ec analyze` = 분석,
> `./ec manual` = 수동 캡처, 그 외 인자는 collect.py로 그대로 전달
> (예: `./ec --auto --menus 공지,과제`).

### 0) 자동 로그인 등록 (1회, 선택이지만 강력 추천)
```bash
./ec login     # 아이디(학번)/비밀번호 → macOS 키체인에 저장
```
- 비밀번호는 **키체인에만** 저장됩니다(이 폴더/레포 어디에도 안 남음).
- 등록 후엔 `./ec` 가 로그인 → `config.AUTO_COURSE` 과목 입장 → 수집까지 **무인으로** 돕니다.
- 캠퍼스가 글로컬이면 `config.py` 의 `CAMPUS = "글로컬"` 로.
- 해제: `security delete-generic-password -s ecampus-collector`

### 1) 자동 수집 (권장 · "알아서 다 해줘")
```bash
python collect.py --auto      # = ./ec
```
1. 키체인 등록이 돼 있으면 **로그인부터 과목 입장까지 자동** — 그냥 끝까지 돈다.
   (등록 전이면: 브라우저에서 직접 로그인 + 과목 입장 후 터미널에서 Enter)
2. 좌측 과목 메뉴(공지/강의자료/과제/시험/팀프로젝트/강의계획서…)를
   `/ilos/st/course/..._form.acl` 주소로 **직접 찾아 전부 저장**합니다.
3. 공지·강의자료·과제처럼 **목록형 메뉴는 글 하나하나 들어가** 본문 + **첨부파일까지 내려받습니다.**
4. **팀프로젝트 게시물은 한 단계 더** — 팀룸(팀진행내용)과 **제출 레코드**(제출일시/본문/제출 첨부)까지
   내려받습니다. 목록의 '제출여부: 제출' 표시는 기존 제출물이 있으면 늘 '제출'이라
   믿을 수 없고, 진짜 제출 내용은 제출 레코드에만 있기 때문.
5. 메뉴 URL은 `output/_discovered_urls.json`, 메뉴별 글/첨부 수 요약은 `output/_index.json`.

> 메뉴 텍스트가 아니라 **실제 href(/ilos/st/course/…)** 로 찾으므로 동명의 커뮤니티 메뉴와 안 헷갈립니다.
> 다른 과목도 받으려면 그 과목에 들어가서 다시 `--auto`.

옵션:
```bash
python collect.py --auto --no-download         # 첨부는 '기록'만(파일 안 받음)
python collect.py --auto --menus 공지,과제      # 특정 메뉴만(라벨 부분일치)
python collect.py --auto --max-posts 100        # 메뉴당 글 상한(기본 40)
```

### 2) 수동 캡처 (특정 페이지만 콕 집어 저장)
```bash
python collect.py
```
1. 브라우저에서 원하는 페이지로 이동.
2. 터미널에서 **그냥 Enter** → 현재 탭을 **자동 라벨**로 저장(라벨 입력 불필요).
   라벨을 직접 정하고 싶을 때만 텍스트 입력 후 Enter.
3. `auto` 입력 → 메뉴 자동 발견 모드 실행 / `q` 입력 → 종료.

### 3) 분석 (마감/놓친 항목)
```bash
python collect.py --analyze     # 또는: python analyze.py
```

## 저장 구조
수집할 때 **과목을 자동 인식해 `output/<과목명>/` 아래에 분리 저장**합니다(여러 과목을 받아도 안 섞임).
과목명은 화면의 `[서울]객체지향프로그래밍(001)` 같은 표기에서 자동으로 읽습니다.
```
output/
  <과목명>/                예: 객체지향프로그래밍, SIGNAL PROCESSING …
    _discovered_urls.json   발견한 메뉴 {라벨: URL}
    _index.json             메뉴별 글/첨부 수 요약
    <메뉴>/                  예: 공지사항, 강의자료, 과제, 시험 …
      content.txt  tables.json  links.json  page.html  network.json  meta.json   ← 목록 페이지
      posts/
        <글ID>_<글제목>/       글ID = URL의 ARTL_NUM/RT_SEQ/PROJECT_SEQ 값 (재실행해도 동일)
          content.txt  tables.json  links.json  page.html  meta.json            ← 글 본문
          attachments.json   첨부 목록 [{filename, token, downloaded, path}]
          files/             내려받은 첨부 실제 파일
          submission.json    (팀프로젝트만) 제출 여부/일시/본문 + 제출 첨부 목록
          submission_pop.html(팀프로젝트만) 제출 팝업 원본
          submission_files/  (팀프로젝트만) 제출 레코드에 첨부된 실제 파일
          teamroom/          (팀프로젝트만) 팀룸(팀진행내용) 페이지 캡처
```
> **재실행해도 중복이 안 생깁니다.** 글 폴더명은 조회수가 아니라 글 식별자 기반이고,
> 이미 받은 첨부는 attachments.json의 FILE_SEQ/token으로 알아보고 건너뜁니다
> (파일이 실제로 디스크에 있을 때만 — 지웠으면 다시 받음). 예전 형식(`01_제목 … 조회 N`)
> 폴더에 받아둔 파일도 그대로 인식해 재다운로드하지 않습니다.
>
> 다른 과목을 받으려면 그 과목 화면에서 다시 `--auto` — 과목명 폴더가 달라 자동으로 안 섞입니다.
> 분석도 과목별로 나옵니다: `python collect.py --analyze` (특정 과목만: `python analyze.py output/객체지향프로그래밍`).
> `_` 로 시작하는 폴더(예: `_archive_*`)는 분석에서 제외됩니다.

## 메뉴 URL — 완전 자동
`--auto` 가 과목 좌측 메뉴의 실제 링크(`/ilos/st/course/..._form.acl`)를 DOM에서 직접 읽어
**모두** 수집하므로 URL을 미리 채울 필요가 없습니다. `config.py` 의 `KNOWN_URLS` 는 비워둬도 되고,
메뉴에 안 뜨는 페이지를 강제로 받고 싶을 때만 추가하는 **선택 항목**입니다.

## 주의
- `.auth/`(세션)와 `output/`(개인 자료)는 `.gitignore` 처리됨. **커밋 금지.**
- LMS 자동 접근이 학교 이용약관에 저촉될 수 있음을 인지하고 사용.
