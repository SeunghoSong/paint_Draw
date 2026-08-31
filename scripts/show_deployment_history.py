#!/usr/bin/env python3
"""배포 이력(deployment-history.jsonl) 조회 스크립트.

장애 시 "그때 정확히 뭐가 배포돼 있었는지"를 git/이미지 태그를 뒤지지 않고 바로 확인하기 위한 것.
deploy 워크플로우가 배포할 때마다 이 파일에 한 줄씩(JSON) 추가한다.

사용법:
    python scripts/show_deployment_history.py           # 최근 10건
    python scripts/show_deployment_history.py --all      # 전체
    python scripts/show_deployment_history.py --at 2026-08-31T12:00:00Z  # 특정 시각 이전 마지막 배포
"""
import argparse
import json
import sys
from pathlib import Path

HISTORY_PATH = Path(__file__).resolve().parent.parent / "deployment-history.jsonl"


def load_records() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    records = []
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def print_record(record: dict) -> None:
    print(f"{record.get('ts', '?')}  git_sha={record.get('git_sha', '?')}  model_version={record.get('model_version', '?')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="전체 이력 출력 (기본은 최근 10건)")
    parser.add_argument("--at", metavar="ISO8601_TIMESTAMP", help="이 시각 이전 마지막 배포 1건만 출력 (장애 시각 기준 조회용)")
    args = parser.parse_args()

    records = load_records()
    if not records:
        print("배포 이력이 아직 없음 (deployment-history.jsonl 비어있음)")
        return 0

    if args.at:
        candidates = [r for r in records if r.get("ts", "") <= args.at]
        if not candidates:
            print(f"{args.at} 이전 배포 이력 없음")
            return 1
        print_record(candidates[-1])
        return 0

    shown = records if args.all else records[-10:]
    for record in shown:
        print_record(record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
