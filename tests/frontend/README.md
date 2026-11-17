# 프런트엔드 회귀 검사

3.18.3: **407개 통과**, 0개 실패·미실행 (2026-10-27 KST, Node 24.19.0). 현재 변경 및 실제 화면 검증은 [VALIDATION_REPORT.md](../../변경이력/VALIDATION_REPORT.md)에 있습니다.

Node 24에서 개별 시험 결과까지 확인한 명령:

    node --test --test-isolation=none --test-reporter=tap tests/frontend/*.test.cjs

일부 시험은 자식 Node 프로세스를 실행합니다. 자식 프로세스 실행이 제한된 환경에서는 날짜 검사의 출력이 비어 실패할 수 있습니다. 실제 실행 오류를 먼저 확인하세요.

아래는 이전 버전의 검사 기록과 기본 실행 방법입니다.

    node --test tests/frontend/*.test.cjs

3.18.0: 24개 파일, **406 tests** (2026-10-15, Node 22.22.0, UTC·Asia/Seoul·America/Los_Angeles 각각 406 통과; `qa_v318_fixes.test.cjs` 추가).
Node VM으로 실제 `frontend/dist`의 HTML script와 사이드카 JS를 실행하며, 화면 DOM/네트워크 경계만 대역을 사용합니다.
`holiday_assignment.test.cjs`는 별도의 672개 재생성 조합을 검사합니다.
브라우저 렌더링이나 Windows EXE 검증은 아닙니다.

3.18.0에서 새로 추가한 파일:

| 파일 | 건수 | 내용 |
|---|---|---|
| `admin_workflows.test.cjs` | 40 | 관리자 작업 흐름(`usability_admin.js`): 작업 직전 자동 사본과 60초 [실행 취소(되돌리기)], 미리 보기 후 원자적 복원 호출·취소 시 요청 없음, 되돌리기 이름 세 가지, 일괄 작업 판정, 백업 파일 불러오기(JSON) 미리 보기, 마감 달 잠금·메뉴 이름, 사원 삭제 미리 보기, 패밀리데이 해제 3버튼 창, 고정 순번 이름 칩, 미리보기 묶음·'바뀐 날만', 화면 안 대화 상자 |
| `main_screen_v318.test.cjs` | 7 | 메인 화면: 공지(globalMemo)를 메모판 첫 줄로, 패밀리데이 이름표와 메모 분리, 메모 줄 수, 기간 14일 경계·종류·꼬리표(시간대 무관), 달력 범례(휴/교/출/부, 배정/필요), '결재 대기 N건' 문구 |
| `wording_connection.test.cjs` | 13 | 용어 통일, 영어·원문 오류 대신 안내 문장, 기본 prompt/confirm 미사용, 조사(은/는) 고르기, 연결 표시·프로그램 꺼짐 띠(10초 안 표시)·공유 폴더 띠(경로 없음, 가짜 새로 고침 안내 없음), 끊긴 동안 보내기 막음(입력 유지), 예시 placeholder 없음 |

3.18.0에서 크게 보강한 파일: `usability_requests` 40(통합 신청 창, 맞교환 후보 선택, 다시 작성, 다른 PC 신청), `usability_personal` 44(신청 대기 표시, ICS 지난 일정 포함), `usability_notifications` 27(결과 알림 1건 1회, 새로 고침 안내 억제, 닫아도 안 읽음 유지), `today_vacation` 13(금일 휴가·부재에 단기 부재 포함), `calendar_absence_chips` 11, `approval_list` 6(0점 체크 시작값 = 신청자 선택), `weekly` 11.

나머지 파일과 건수: `absence_detail` 3, `absence_edit` 12, `family_day` 22, `family_day_decided` 20, `family_day_stable` 20, `fresh_rebuild` 10, `holiday_assignment` 38, `my_duty` 8, `rotation_rules` 13, `usability_admin` 9, `usability_common` 3, `usability_robustness` 10, `v316_screens` 9.

공통 도우미는 `helpers/`(`scheduler.cjs`, `generate_payload.cjs`)에 있습니다.
실제 결과와 남은 한계는 루트 [VALIDATION_REPORT.md](../../변경이력/VALIDATION_REPORT.md)를 참고하세요.
