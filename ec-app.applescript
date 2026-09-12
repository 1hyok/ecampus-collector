-- eCampus 수집.app — ./ec(자동 수집)를 터미널 없이 더블클릭으로 실행하는 런처.
--
-- 빌드(소스 수정 시 재실행, 프로젝트 루트에서):
--   rm -rf "eCampus 수집.app" && osacompile -o "eCampus 수집.app" ec-app.applescript
-- ⚠ rm 없이 기존 .app 위에 덮어 빌드하면 코드서명 seal이 깨져
--   macOS가 "응용 프로그램을 열 수 없습니다"로 실행을 거부한다.
--
-- 동작:
--   · 실행 로그는 output/_logs/run-<시각>.log 에 저장
--   · 끝나면 macOS 알림으로 "완료: 메뉴 N개 · 게시물 N개 …" 요약 표시
--   · 오류로 끝나거나 완료 요약이 없으면(로그인/과목 진입 실패 등) 로그 파일을 열어줌
--   · 이미 수집이 돌고 있으면 중복 실행하지 않음

on run
	set projectDir to "/Users/zach/개발/프로젝트/ecampus-collector"
	-- 출력 3줄(종료코드 / 로그경로 / 완료요약)을 한 번의 do shell script 로 받는다.
	-- pgrep 패턴의 [.] 는 이 셸 자신의 커맨드라인 문자열에 매칭되지 않게 하는 장치.
	set shellCmd to "cd " & quoted form of projectDir & " && " & ¬
		"if pgrep -f 'collect[.]py' >/dev/null; then printf 'RUNNING'; exit 0; fi; " & ¬
		"if ! security find-generic-password -s ecampus-collector >/dev/null 2>&1; then printf 'NOLOGIN'; exit 0; fi; " & ¬
		"mkdir -p output/_logs && " & ¬
		"LOG=\"output/_logs/run-$(date +%Y%m%d-%H%M%S).log\" && " & ¬
		"./ec > \"$LOG\" 2>&1; ST=$?; " & ¬
		"SUMMARY=$(grep '^완료:' \"$LOG\" | tail -1); " & ¬
		"printf '%s\\n%s\\n%s' \"$ST\" \"$PWD/$LOG\" \"$SUMMARY\""
	set runOutput to do shell script shellCmd
	set parts to paragraphs of runOutput
	set exitCode to item 1 of parts
	if exitCode is "RUNNING" then
		display notification "이미 수집이 실행 중입니다." with title "eCampus 수집"
		return
	end if
	-- 앱에는 stdin이 없어 수동 로그인 폴백이 불가능 — 키체인 미등록이면 시작 전에 알려준다.
	if exitCode is "NOLOGIN" then
		display notification "자동 로그인 미등록 — 터미널에서 ./ec login 을 1회 실행한 뒤 다시 여세요." with title "eCampus 수집" sound name "Basso"
		return
	end if
	set logPath to item 2 of parts
	set summaryLine to ""
	if (count of parts) > 2 then set summaryLine to item 3 of parts
	if exitCode is not "0" then
		display notification "오류로 종료됐습니다 — 로그를 엽니다." with title "eCampus 수집 실패" sound name "Basso"
		do shell script "open " & quoted form of logPath
	else if summaryLine is "" then
		display notification "완료 요약이 없습니다(로그인/과목 진입 실패?) — 로그를 엽니다." with title "eCampus 수집 확인 필요"
		do shell script "open " & quoted form of logPath
	else
		display notification summaryLine with title "eCampus 수집 완료" sound name "Glass"
	end if
end run
