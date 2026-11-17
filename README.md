# WLR OnAir Windows 빌드

처음 빌드하거나 게시할 때는 [시작하기.txt](시작하기.txt)의 순서를 먼저 따라 주세요.

근태·당직 관리 프로그램의 Admin 및 Viewer 실행파일을 Windows 환경에서 빌드합니다.
현재 기준 소스는 v3.18.42입니다. 배포관리의 Windows 선택 창과 관리자 헤더의 암호 인증을 개선했습니다.

## 빌드와 게시

Actions → Windows Admin and Viewer → Run workflow를 실행합니다.
main에 새 커밋을 올려도 자동 실행됩니다.
성공한 실행의 Artifacts에서 WLR-OnAir-Windows 파일을 내려받아 압축을 풉니다.

- Admin/WLR_OnAir_Admin.exe: 관리자
- Viewer/WLR_OnAir_Viewer.exe: 조회용
- WLR_OnAir_v버전_Update.zip: 기존 배포관리에서 게시할 업데이트 패키지
- 배포관리.cmd: 공유폴더 게시 도구
- Admin_SELFTEST.json, Viewer_SELFTEST.json: 실제 EXE 자체점검 결과
- BUILD_MANIFEST.json: 소스 지문 및 결과 파일 해시

빌드 결과 보관 기간은 30일입니다. 사내 공유폴더에 자동 게시하지 않습니다.
배포 전 실제 PC에서 최초 설정·공유폴더 연결·업데이트 흐름을 확인하세요.
개인 DB와 실제 연결 설정은 저장소에 넣지 않습니다.

로컬 Windows 빌드는 build_windows.cmd를 사용합니다. 자세한 내용은 WINDOWS_BUILD_GUIDE.md를 참고하세요.
다음 배포에서 버전 번호와 소스 지문을 함께 갱신하려면 버전올리기.cmd를 사용하세요.
