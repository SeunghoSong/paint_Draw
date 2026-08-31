# 배포 체크리스트

`main`에 머지하기 전/후로 확인할 것. [ci-cd-overview.md](ci-cd-overview.md) 참고.

## 머지 전 (PR 단계)

- [ ] `ci.yml`의 `lint-and-syntax`, `docker-build` 두 Job이 모두 통과했는가
- [ ] `.env.example`/`.env.tuning.example`에 새 환경변수를 추가했다면, 실제 `.env`/`.env.tuning`에도
  반영했는가 (CI는 `.example` 파일을 복사해서 쓰므로 example이 최신이어야 CI도 정확함)
- [ ] `Video_Engine/models/manifest.json`을 바꿨다면(모델 버전 업), 체크섬(`checksum.value`)도
  같이 갱신했는가 — 안 그러면 `Video_Engine`이 기동 시 `RuntimeError`로 죽는다

## 머지 후 (deploy.yml 실행 확인)

- [ ] `deploy.yml`이 성공했는지 Actions 탭에서 확인
- [ ] `ghcr.io`에 3개 이미지(`*-web-server`, `*-video-engine`, `*-motion-engine`)가 새 태그로
  올라왔는지 확인 (태그 규칙은 [image-tag-convention.md](../02_version/image-tag-convention.md))
- [ ] `deployment-history.jsonl`에 이번 배포 기록이 추가됐는지 확인 —
  `python scripts/show_deployment_history.py`로 조회
- [ ] 실제 운영 환경에 반영할 계획이면(이 프로젝트는 현재 그 단계까지는 없음 — docker-compose를
  직접 `docker compose pull && docker compose up -d`로 갱신하는 수동 단계가 남아있음), 새 이미지로
  교체 후 3개 서비스의 `/health`가 전부 `status: "ok"`인지 확인

## 문제가 생겼을 때

→ [rollback-procedure.md](rollback-procedure.md) 참고.
