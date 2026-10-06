# 06: 로그인 Discord 알림

**What to build:** 로그인에 성공하거나 IP가 시도 제한에 걸리면 Discord로 알림이 온다. 알림에는 시각과 접속 정보(IP, 브라우저·OS)가 들어 있고, 내가 아닌 로그인이나 비밀번호 맞히기 시도를 바로 알 수 있다.

Spec: `.scratch/password-auth/spec.md` (User Stories 16~18, Discord 알림)

**Blocked by:** 05

**Status:** ready-for-agent

- [ ] 앱이 Discord 알림 모듈을 만들고, 테스트는 가짜 Discord를 따로 넘길 수 있다(키움용 가짜 응답과 섞이지 않음).
- [ ] 로그인 성공: embed 제목 "로그인", 항목 시각(KST), IP, 브라우저·OS. 투자 환경 머리말은 없다.
- [ ] 시도 제한: embed 제목 "로그인 시도 제한", 항목 IP, 연속 실패 수, 풀리는 시각(KST), 브라우저·OS. 잠긴 뒤의 추가 시도는 알리지 않는다.
- [ ] 브라우저·OS는 User-Agent에서 고른다(Edge, Chrome, Firefox, Safari / Windows, Android, iOS, macOS, Linux, 그 밖은 "알 수 없음"). 원문 User-Agent(앞 200자)는 로그에만 남긴다.
- [ ] 로그인 응답은 알림을 기다리지 않는다. 진행 중인 알림 작업을 앱이 모아 두어 중간에 사라지지 않게 한다. 서버 종료 때 남은 알림은 버린다.
- [ ] Discord가 꺼져 있거나 실패해도 로그인은 204다.
- [ ] 입력한 비밀번호는 틀린 것이라도 알림·로그에 없다.
- [ ] 백엔드 테스트: 성공·잠김에서 가짜 Discord에 embed가 나가고 IP·브라우저·OS가 있으며 비밀번호가 없다. Discord 실패에도 로그인 204.
- [ ] 실제 Discord 확인: 사용자가 로그인해 채널에 알림이 오는지 본다.
- [ ] 전체 검증 명령 통과.
