# 컨테이너 이미지 태그 규칙

목적: `latest`처럼 매번 덮어써지는 태그 대신, 이미지 하나만 보고 "무슨 커밋 + 무슨 모델 버전으로
빌드됐는지" 바로 알 수 있게 함.

## 규칙

| 서비스 | 레지스트리 경로 | 태그 형식 | 예시 |
|---|---|---|---|
| Web_Server | `ghcr.io/<owner>/<repo>-web-server` | `<git-sha>` | `ghcr.io/seunghosong/paint_draw-web-server:a1b2c3d` |
| Video_Engine | `ghcr.io/<owner>/<repo>-video-engine` | `<git-sha>-model<모델버전>` | `ghcr.io/seunghosong/paint_draw-video-engine:a1b2c3d-model1` |
| Motion_Engine | `ghcr.io/<owner>/<repo>-motion-engine` | `<git-sha>` | `ghcr.io/seunghosong/paint_draw-motion-engine:a1b2c3d` |

- `git-sha`는 `git rev-parse --short HEAD` (7자리)
- 모델 버전은 `Video_Engine/models/manifest.json`의 `version` 필드를 그대로 읽어서 붙임 —
  Video_Engine만 모델을 갖고 있으므로 태그에 모델 버전을 넣는 것도 Video_Engine뿐
- `latest` 태그는 아예 만들지 않음 — 항상 특정 커밋을 가리키는 태그만 사용

## 빌드 시점 라벨 (`docker inspect`로 확인 가능)

이미지 자체에도 메타데이터를 남겨서, 태그를 안 봐도 `docker inspect`로 확인 가능하게 함:

```bash
docker inspect ghcr.io/seunghosong/paint_draw-video-engine:a1b2c3d-model1 \
  --format '{{ .Config.Labels }}'
# → map[git.sha:a1b2c3d model.version:1]
```

각 `Dockerfile`에 `ARG`/`LABEL`로 구현되어 있음 (Video_Engine만 `model.version` 라벨 추가):
```dockerfile
ARG GIT_SHA=unknown
LABEL git.sha=$GIT_SHA
```

## 구현 위치

`.github/workflows/deploy.yml`이 `main` push 시 커밋 SHA/모델 버전을 읽어서 `--build-arg`로
넘기고, 위 규칙대로 태그를 붙여 `ghcr.io`에 푸시한다. 별도 레지스트리 계정 없이 `GITHUB_TOKEN`만
사용(GitHub Container Registry).

## 중요한 한계 — 이 이미지 태그가 실제 롤백에 바로 쓰이지 않는 이유

`docker-compose.yml`이 소스 파일(`server.js`, `app.py` 등)을 볼륨으로 직접 마운트하고 있어서,
이미지 태그를 바꿔서 실행해도 실제 도는 코드는 로컬 체크아웃 상태를 따라간다. 즉 이 태그 규칙은
**"그 시점에 무엇이 배포됐었는지 감사(audit)하기 위한 기록"**이지, 그 이미지를 그대로 다시
실행하는 배포/롤백 메커니즘으로는 아직 안 쓰인다. 자세한 내용과 실제 롤백 절차는
[../05_cicd/rollback-procedure.md](../05_cicd/rollback-procedure.md) 참고.
