#!/bin/bash
# local-test 스택을 완전히 초기 상태로 리셋한다.
#
# Redis/Sentinel은 failover가 일어나거나 REPLICAOF가 바뀔 때마다 CONFIG REWRITE로
# 자기 conf 파일에 "지금 상태"(replicaof, known-replica, known-sentinel 등)를
# 영구 저장한다. 여기서는 conf 파일을 디렉터리째로 바인드 마운트하고 있어서, 그
# 상태가 컨테이너를 지워도(docker compose down -v) 호스트에 그대로 남는다.
# 그래서 failover 테스트를 반복하다 보면 다음 실행 때 엉뚱한 IP를 참조하거나
# master/replica 역할이 뒤바뀌는 문제가 생긴다. 이 스크립트는 conf-templates/의
# 깨끗한 원본으로 되돌리고 완전히 새로 띄운다.

set -e
cd "$(dirname "$0")"

echo "==> 기존 컨테이너/볼륨/네트워크 정리"
docker compose down -v

echo "==> conf 파일을 깨끗한 템플릿으로 복원"
cp conf-templates/master.conf master/redis.conf
cp conf-templates/replica.conf replica/redis.conf
cp conf-templates/sentinel-1.conf sentinel-1/sentinel.conf
cp conf-templates/sentinel-2.conf sentinel-2/sentinel.conf
cp conf-templates/sentinel-3.conf sentinel-3/sentinel.conf

echo "==> 새로 빌드 및 기동"
docker compose up -d --build

echo "==> 안정화 대기 (8초)"
sleep 8

docker compose ps
echo ""
echo "==> master/replica 역할 확인"
docker exec local-redis-master redis-cli info replication | grep -E "^role"
docker exec local-redis-replica redis-cli info replication | grep -E "^role"
