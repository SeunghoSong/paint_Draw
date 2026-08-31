# 롤백 절차

## 중요: 이 저장소에서 "이미지 롤백"은 겉보기와 다르게 동작한다

`docker-compose.yml`을 확인해보면 3개 서비스 모두 소스 파일을 **볼륨으로 직접 마운트**하고 있다
(로컬 개발 시 코드 수정을 바로 반영하기 위한 편의 설정):

```yaml
web_server:
  volumes:
    - ./Web_Server/public:/app/public
    - ./Web_Server/server.js:/app/server.js
video_engine:
  volumes:
    - ./Video_Engine/app.py:/app/app.py
motion_engine:
  volumes:
    - ./Motion_Engine/app.py:/app/app.py
```

즉 **컨테이너 안 이미지에 어떤 코드가 들어있든, 실제 실행되는 건 항상 로컬 디스크의 현재 파일이다.**
`deploy.yml`이 `ghcr.io`에 올린 이미지 태그를 바꿔서 실행해도, 이 볼륨들 때문에 `server.js`/`app.py`는
로컬 체크아웃 상태 그대로 돈다 — 이미지 태그만으로는 실제 실행 코드가 롤백되지 않는다.

**결론: 이 프로젝트의 롤백은 이미지 기반이 아니라 사실상 git 기반이다.** `ghcr.io`의 이미지 태그는
"그 시점에 정확히 어떤 커밋 + 어떤 모델 버전이 배포됐었는지"를 감사(audit)하는 기록용으로 보는 게
맞다 — 그 이미지를 그대로 다시 실행하는 배포 방식은 볼륨 마운트를 제거한 별도 프로덕션용
compose 구성(예: `docker-compose.prod.yml`)이 있어야 의미가 있는데, 이 프로젝트는 아직 그 단계까지
가지 않았다 (POC 규모라 이렇게 결정한 것 — 필요해지면 별도 작업으로 도입).

## 실제 롤백 절차 (git 기반)

1. `python scripts/show_deployment_history.py`로 문제가 시작된 시점 직전의 정상 배포 기록을 찾는다
   (`git_sha`, `model_version` 확인).
2. 해당 커밋으로 되돌린다:
   ```bash
   git checkout <정상이었던 git_sha>
   ```
3. 로컬(또는 실행 중인 서버)에서 재빌드 후 재기동:
   ```bash
   docker compose up --build -d
   ```
   `--build`가 중요하다 — `requirements.txt`/`package.json`이 그 커밋 시점 것으로 바뀌었을 수
   있으므로, 볼륨 마운트와 무관하게 의존성 레이어도 그 시점 것으로 다시 빌드해야 한다.
4. 3개 서비스 `/health`가 전부 정상인지 확인 (`docker compose ps`, healthcheck 상태 참고).
5. 롤백 완료 후, 문제의 원인이 된 커밋에 대해 별도로 이슈를 남기고 재작업한다 (이 롤백 자체가
   `main`에 대한 새 커밋이 아니라 **로컬/서버에서 임시로 되돌린 상태**라는 점 주의 — `main` 브랜치
   히스토리를 되돌리려면 `git revert`로 별도 PR을 올리는 걸 권장, `git reset --hard` 후
   force-push는 하지 않는다).

## 모델 롤백

`Video_Engine/models/manifest.json`의 `version`/`checksum`을 이전 값으로 되돌리고 위 git 기반
절차를 그대로 따르면 된다 — 모델은 아직 버전이 1개(`"1"`)뿐이라 실제로 롤백할 이전 버전이 없는
상태다. 모델을 2개 버전 이상 운영하게 되면 이 섹션을 갱신해야 한다 (백로그의 "모델 파일
버전별 디렉토리" 항목 참고).

## API 버전 롤백

해당 없음 — 이 프로젝트는 API 버전 분리 자체를 도입하지 않았다 (백로그 참고, 독립 소비자가
생기기 전까진 불필요하다고 판단함).
