# v3.18.41 공통 모달 회귀 검증

실제 배포 프런트엔드와 Chromium(`/usr/bin/chromium`), Python Playwright, Python 서버를 사용했다. 모든 브라우저 프로필·SQLite·사원 데이터는 임시 합성 자료다. 운영 데이터는 열지 않았다.

## 변경과 근거

- 기존 `.modal.active`/`display:flex` 경로와 `OnAirUI.open/close/raise`를 한 생명주기로 관리한다. 새 창은 초기 포커스를 받고, 최상위 창이 Tab/Shift+Tab을 순환하며, 배경은 `inert`로 차단한다.
- 최상위 표시 순서와 Escape 닫기 대상이 일치한다. `OnAirUI.registerClose(id, callback)`은 기존의 실제 닫기 함수를 등록하므로 저장하지 않은 변경 확인과 암호 취소 규칙을 유지한다.
- 닫을 때 호출 요소로 돌아간다. 호출 메뉴 항목이 이미 숨겨졌다면 같은 메뉴의 보이는 펼침 버튼으로 돌아간다.
- 열린 창의 내용 갱신은 재열기로 취급하지 않는다. 부모를 갱신해도 자식의 표시 순서·포커스·Escape 순서가 유지된다. 명시적 `raise()`는 계속 사용할 수 있다.
- Escape는 기존처럼 버블 단계에서 처리하고 `defaultPrevented`를 존중한다. 날짜 선택 중 첫 Escape는 선택만 취소하며, 다음 Escape가 창을 닫는다.

## RED → GREEN

| 회귀 | 수정 전 | 최종 결과 |
|---|---|---|
| 근무 규칙 → Tab → Enter | 1366/1920 모두 배경 자동 배정 창을 열었음 (`baseline.json`, 4/4 실패) | 배경 실행 방지 |
| 숨겨진 메뉴 항목의 모달 닫기 포커스 | 1366/1920 모두 BODY (`return-baseline.json`, 2/2 실패) | 보이는 메뉴 펼침 버튼으로 복귀 |
| 날짜 선택 중 Escape | 첫 Escape가 편집창까지 닫음 (`calendar-baseline.json`, 2/4 실패) | 첫 Escape는 선택 취소, 두 번째는 편집창 닫기 |
| 기존 창 갱신 시 포커스·중첩 순서 | 새 Node 회귀 3개가 각각 실패하는 것을 확인 | 모두 통과 |

최종 브라우저 검증은 **114/114 통과**, `pageerror`와 실행 예외 **0건**이다 (`evidence.json`, `browser-green.log`). 화면은 1366×768, 1920×1080 두 크기다.

검증에는 사원 명단·규칙·당직 통계·변경 이력·명절·공휴일의 초기 포커스, 양쪽 Tab 경계, 실제 포커스 소실 후 복구, 닫기 복귀가 포함된다. 중첩된 저장 안 함 확인·관리자 암호·연결 정보, 부모 내용 갱신 중 자식 유지, 날짜 선택 취소도 실제 UI로 확인했다. 최종 저장이나 실제 자동 배정·복원은 실행하지 않았다.

## 실행

```sh
python3 validation/v3.18.41_details/modal/acceptance.py
node --test --test-isolation=none --test-reporter=spec tests/frontend/usability_common.test.cjs tests/frontend/usability_modal.test.cjs tests/frontend/usability_notifications.test.cjs
```

공통 모달·새 회귀·알림 관련 Node 검증은 **65/65 통과** (`focused-suite.log`). 전체 프런트엔드 검증과 버전/해시 정합성 검사는 상위 통합 작업에서 실행한다.

대표 화면: `baseline-rule-tab-enter-1366.png`, `baseline-rule-tab-enter-1920.png`, `fixed-rule-tab-enter-1366.png`, `fixed-rule-tab-enter-1920.png`, `nested-rule-restored-1366.png`, `nested-rule-restored-1920.png`.
