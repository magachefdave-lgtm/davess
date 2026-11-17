# 검수 항목 1·3·9 검증

2026-11-17 UTC · 기준 `dc51d7e` · 임시 합성 자료 · Chromium

설정은 성공한 제출값만 다음 비교 기준에 반영하고 저장 중 중복 제출을 막는다. 입력란은 다시 그리지 않아 대기 중 재입력과 실패한 입력이 유지된다. 결재 검색은 기존 기간 표시 함수를 재사용해 신규 종료일, 변경 전·후 기간, 실제 원본 구간, 취소 구간의 ISO 날짜와 화면의 요일 포함 날짜를 찾는다. 원본 스냅샷이 없는 기존 변경 신청도 화면의 요청 기간으로 검색한다. 장기 부재 목록은 기간 폭을 유지하면서 긴 이름만 말줄임한다.

| 실제 브라우저 검사 | RED | GREEN |
|---|---:|---:|
| 설정 저장 / 임시 INI 확인 | 27 통과 / 12 실패 | 39 / 39 통과 |
| 결재 날짜 검색 / 실제 신청 API | 97 통과 / 42 실패 | 139 / 139 통과 |
| 장기 부재 목록 / 이름·기간 기하 | 65 통과 / 4 실패 | 71 / 71 통과 |

GREEN 합계 **249개 통과**, 브라우저 오류·치명적 오류 0건. RED는 대상 CSS 또는 JS만 `git show dc51d7e:...`로 제공하여 같은 시나리오에서 원래 결함을 재현했다. 사이드바 GREEN에는 1920·1366 폭에서 말줄임 활성 여부 검사 2개를 추가했다.

1920×1080, 1366×768, 1024×768을 모두 검사했다. 설정 검사는 실제 임시 INI에 A→B→A가 기록되는지, 503 실패 후 파일과 입력이 보존되는지, 재시도하는지, 응답 대기 중 새로 입력한 값이 제출 스냅샷과 구분되는지 확인했다. 결재는 실제 신규·승인·변경·취소 API로 신청을 구성하고 검색·확인 취소 후 서버 신청 자료가 그대로인지 확인했다. 이름 3자·4자와 긴 기존 이름, 연도 경계 기간, 끝 요일, 전체 `aria-label`·기간 `title`을 확인했다. 설정 Enter 저장, 결재 키보드 검색·대체 0점 Space 선택·Enter 확인 후 Escape 취소, 사이드바 Enter 미리보기도 검사했다.

새 Node 회귀 검사는 설정 3개와 날짜 검색 5개이다. 최초 7개 중 6개 실패를 확인한 뒤 수정했고, 원본 스냅샷 없는 변경 신청 검사도 별도로 실패를 확인한 뒤 보완했다. 관련 기존 검사와 함께 **151 / 151 통과**했다. 전체 프로젝트 검사는 통합 담당 단계에서 실행한다.

```bash
node --test --test-isolation=none --test-reporter=tap tests/frontend/settings_save.test.cjs tests/frontend/update_status.test.cjs tests/frontend/approval_search_dates.test.cjs tests/frontend/approval_list.test.cjs tests/frontend/usability_requests.test.cjs tests/frontend/absence_management.test.cjs
python validation/v3.18.41_details/settings-search/settings_browser.py green
python validation/v3.18.41_details/settings-search/approval_browser.py green
python validation/v3.18.41_details/settings-search/sidebar_browser.py green
```

브라우저 스크립트의 `green`을 `red`로 바꾸면 수정 전 자료를 재현한다. 세 스크립트 모두 `TemporaryDirectory` 안에 합성 프로필·설정·SQLite를 만들고 서버를 종료한다. Windows 실행기·실제 공유폴더·운영 자료는 이 검사의 대상이 아니다.

JSON은 `settings-{red,green}.json`, `approval-{red,green}.json`, `sidebar-{red,green}.json`이다. 스크린샷 이름은 대상·단계·해상도·상태를 포함한다. `red-unit.tap`, `red-legacy-fallback.tap`, `green-unit.tap`에는 Node 결과를 보관했다. 1366 설정 복원·기존 종료일 검색·긴 이름 사이드바 GREEN 화면을 직접 확인했다.
