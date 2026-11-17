# v3.18.41 신청 권한 복구 회귀 검증

실제 ADMIN·VIEWER 서버, 공유 SQLite, Chromium(`/usr/bin/chromium`)으로 확인했다. 합성 직원 12명과 김도윤의 1일 교육 신청 55건만 사용했다. 정상 조회·복구 등록·관리자 승인·직원 claim은 실제 서버를 사용하고, 실패·응답 역전 검증에만 브라우저에서 503 또는 응답 지연을 주입했다.

## 결과

- RED: 기존 소스에서 17개 브라우저 검사 중 8개가 의도대로 실패했다. 수동·실제 10초 폴링의 초점 소실, 성공 후 잔존 오류, Escape의 부모 동시 닫힘, 관리자 Space·다음 Tab·목록 스크롤·검색 초점 소실을 재현했다. [로그](red-evidence.json), [실행 출력](red-run.log), [초기 Node RED](red-node.log), [미리보기 추가 RED](red-preview-node.log).
- GREEN: 최종 소스로 **29/29 브라우저 검사 통과**, pageerror 0건. [구조화 로그](green-evidence.json), [실행 출력](green-run.log).
- 관련 Node 회귀 검사 **34/34 통과**. [실행 출력](green-node.log). 저장 권한·보호된 요청 계약·선택 범위·차단 미리보기·중복 실행·claim 완료 검증을 유지했다.

| 확인 항목 | 최종 결과 | 화면 |
|---|---|---|
| Escape 한 번 | 복구 창만 닫히고 내 신청 내역 및 복구 열기 버튼 초점 유지 | [전](red-escape-child-only.png), [후](green-escape-child-only.png) |
| 직원 수동·10초 폴링 | 동일 갱신 버튼 초점 유지; 상태 전환 시 사용 가능한 작업으로 이동 | [후](green-employee-refresh-retry.png) |
| 직원·관리자 503 재시도 | 기존 화면·입력 유지; 최신 성공 조회·미리보기의 이전 오류 제거 | [직원 후](green-employee-refresh-retry.png) |
| 관리자 Space 선택 | 동일 rootUid 체크박스 초점 및 scrollTop **1800 유지**; 다음 Tab은 다음 체크박스 | [전](red-checkbox-scroll-focus.png), [후](green-checkbox-scroll-focus.png) |
| 페이지 이동 | 50건·5건 페이지와 이전 페이지 선택 유지 | [로그](green-evidence.json) |
| 응답 역전 | 닫기·이름 변경·관리자 목록 이동·다시 열기 후 이전 응답이 화면·오류·초점을 덮어쓰지 않음 | [로그](green-evidence.json) |
| 다시 연 창의 수동 조회 | 이전 창의 GET이 대기 중이어도 새 창의 수동 조회가 실제 200 수신 | [로그](green-evidence.json) |
| 관리자 미리보기 | 503 후 성공 시 오류 제거; 늦은 이전 503이 최신 성공을 덮어쓰지 않음 | [로그](green-evidence.json) |
| 실제 승인·claim | 선택 1건 미리보기 → 승인 → 검증된 COMPLETED → 직원 receipt **정확히 1건** | [완료 화면](green-verified-claim-completed.png) |

주요 흐름은 1366×768에서 실행했고, 관리자 복구 목록은 1920×1080·1366×768·1024×768에서 가로 넘침이 없었다. 저장한 RED·GREEN 화면을 직접 열어 비교했다. 스크린리더 음성·터치 기기·실제 Windows 트레이는 이번 브라우저 검증 범위에 포함하지 않았다.

## 재현

프로젝트 루트에서 다음을 실행한다. 설치된 `python3`의 Playwright·httpx 및 기존 Runtime helper를 사용한다. 로컬 서버·Chromium에 대한 로컬 네트워크 실행 권한이 필요하다.

```sh
python3 validation/v3.18.41_details/recovery/browser_proof.py green
node --test --test-isolation=none tests/frontend/request_access_recovery.test.cjs tests/frontend/request_access_session.test.cjs tests/frontend/request_access_lookup.test.cjs
```

[실행 스크립트](browser_proof.py)는 각 실행의 request_access.js SHA-256을 JSON에 기록한다. [소스 메타데이터](source-metadata.json)에 최종 HEAD·관련 세 파일의 해시·실행 명령을 기록했다. 최종 GREEN의 기록은 현재 소스와 일치한다. 브라우저 실행 자료에는 receipt 값이나 launch/session credential을 출력하지 않았다.
