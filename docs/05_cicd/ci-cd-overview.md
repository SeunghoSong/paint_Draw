# CI/CD 개요

목적: 사람이 실수로 배포하는 상황을 없애고, 검증된 코드만 배포되게 함.

## 전체 흐름

```
PR 생성/갱신 ──▶ ci.yml (lint-and-syntax → docker-build)
                   │
                   ▼ (통과해야 머지 가능하도록 브랜치 보호 규칙 설정 권장 — 아래 참고)
main에 머지/push ──▶ deploy.yml (이미지 빌드 → ghcr.io 푸시 → 배포 이력 기록)
```

두 워크플로우는 `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`로 분리되어 있다.
**PR 검증과 실제 배포(이미지 push)를 완전히 분리**해서, 검증 안 된 코드가 실수로 배포되는 걸
막는 게 핵심이다.

## ci.yml — PR/브랜치 검증용

| Job | 하는 일 |
|---|---|
| `lint-and-syntax` | `Web_Server`(`npm test` = `node --check server.js`), `Video_Engine`/`Motion_Engine`(`python -m py_compile`), `docker compose config --quiet` |
| `docker-build` | `docker compose build`로 3개 서비스 이미지가 실제로 빌드되는지 확인 (로컬 환경엔 Docker 데몬이 없어서 이번 세션 내내 못 해봤던 검증 — CI에서는 확인 가능) |

**한계**: 지금은 실제 유닛 테스트가 없다. `node --check`/`py_compile`은 문법 오류만 잡고, 로직이
맞는지는 검증하지 못한다. 실제 테스트 스위트(pytest, node:test 등)를 도입하려면 별도 작업 필요 —
특히 `Web_Server/server.js`는 파일 상단에서 바로 서버를 기동하는 구조라, 단위 테스트가 가능하게
하려면 로직(로거, 서킷브레이커, 알림 디바운싱 등)을 별도 모듈로 분리하는 리팩터링이 먼저 필요하다.

## deploy.yml — main 전용 배포

`main` 브랜치에 push(=PR 머지)될 때만 동작. PR 자체에서는 절대 실행되지 않는다.

1. 커밋 SHA / `Video_Engine/models/manifest.json`의 모델 버전을 읽어서 이미지 태그에 사용
   (자세한 규칙은 [image-tag-convention.md](../02_version/image-tag-convention.md))
2. `ghcr.io`(GitHub Container Registry)에 3개 서비스 이미지를 각각 빌드/푸시 — 별도 레지스트리
   계정이나 시크릿 설정 없이 `GITHUB_TOKEN`만으로 동작 (GitHub이 기본 제공)
3. 배포 시점의 (git sha, 모델버전) 조합을 `deployment-history.jsonl`에 한 줄 추가하고 자동 커밋
   (`[skip ci]` 태그를 붙여서 이 커밋이 다시 CI/배포를 트리거하지 않게 함)

**주의할 점**: `deploy.yml`은 `permissions: contents: write, packages: write`를 명시적으로 요구한다.
저장소 설정에서 Actions의 기본 권한이 read-only로 되어 있으면 이미지 푸시/커밋이 실패한다
(Settings → Actions → General → Workflow permissions에서 확인). 또한 이 워크플로우는 **main에
push될 때마다 자동으로 봇 커밋(`deployment-history.jsonl` 갱신)을 만든다** — 이 동작을 원치 않으면
"배포 이력 커밋" 스텝을 빼거나 별도 브랜치/PR로 바꿔야 한다.

## 검증 상태

이 세션에서는 GitHub에 실제로 push해서 워크플로우를 실행해보진 않았다(그 자체가 "실제 배포를
트리거하는" 행동이라 임의로 하지 않음). 대신 로컬에서:
- 두 YAML 파일을 PyYAML로 파싱해서 문법 오류 없음을 확인
- `run:` 블록 안의 셸/파이썬 스크립트(중첩 따옴표가 있는 태그 생성 로직 포함)를 실제로 실행해서
  의도한 값이 정확히 나오는 것을 확인
- `deployment-history.jsonl` 기록 로직을 표본 데이터로 실행해서 JSON Lines 형식이 올바른지 확인

실제 PR을 올려서 `ci.yml`이 정상 동작하는지 한 번 확인하는 걸 권장한다.
