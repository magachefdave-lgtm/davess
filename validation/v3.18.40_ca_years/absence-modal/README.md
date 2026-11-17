# 관리자 본인 부재 등록 모달 회귀 검증

실제 ADMIN 런타임과 별도 임시 프로필, 공유 SQLite, Chromium을 사용했다. 직원 12명과 관리자 암호는 전부 검증용 가상 데이터이며 운영 데이터는 사용하지 않았다.

## 원인과 수정

휴가·부재 관리창은 `OnAirUI.raise`로 문서와 Escape 닫기 목록의 끝으로 이동하지만, 관리자 암호창과 저장 후 빈자리 안내창은 기존 위치에서 active만 켰다. 같은 z-index 1000에서 관리창이 두 창을 가렸고, Escape도 관리창으로 전달됐다. 암호를 기다리는 동안 관리창은 저장 중 닫기를 막으므로 사용자가 빠져나올 수 없었다.

두 후속 창을 열 때 같은 raise 경로로 화면 및 Escape 순서를 맞췄다. 빈자리 안내로 초점을 옮기고 닫으면 부재 편집창으로 돌려준다. 메인 레이아웃/CSS는 바꾸지 않았다.

## RED

수정 전 `python validation/v3.18.40_ca_years/absence-modal/acceptance.py --red`로 재현했다. `red-evidence.json`에 실제 암호 입력 클릭 타임아웃(부재 관리창의 pointer interception), 잘못된 Escape 대상, 가려진 빈자리 창의 hit-test 결과가 있다. `red-password.png`, `red-vacancy-False.png`가 당시 실제 화면이다.

## GREEN

수정 후 `python validation/v3.18.40_ca_years/absence-modal/acceptance.py`의 모든 21개 검증이 통과했다. 관리자 내 이름 선택, 확인 취소와 입력 보존, 암호 취소, 틀린 암호 재시도, Escape, 올바른 암호 저장, 부재 1건 저장 및 기존 당직 해제, 빈자리 창 나중에, 메인 복귀, 재열기/수정 취소를 실제 조작했다. 암호가 없는 환경도 함께 검사했다. 브라우저 스크립트 오류는 없다.

`evidence.json`과 `green-*.png`에 결과가 있다. 메인 헤더/메뉴/카드와 달력 가로 배치는 그대로다. 저장으로 달력 상태가 달라질 때 달력 머리글 높이가 달라질 수 있어 전체 before/after 치수를 원본 그대로 기록했다.

관련 기존 프런트엔드 회귀 테스트 9개 파일도 통과했다: absence_calendar, absence_detail, absence_edit, absence_management, absence_request_calendar, calendar_absence_chips, system_info_auth, admin_workflows, usability_admin.
