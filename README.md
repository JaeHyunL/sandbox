# Redis Sentinel 3대 구성 (Master 1 / Replica 1 / Sentinel-only 1)

## 구성 개요

| 서버 | 역할 | 컨테이너 |
|---|---|---|
| 서버1 | Redis Master + Sentinel | `redis-master`, `sentinel-1` |
| 서버2 | Redis Replica + Sentinel | `redis-replica`, `sentinel-2` |
| 서버3 | Sentinel 전용 (투표용, 데이터 없음) | `sentinel-3` |

- Sentinel은 총 3대 → 쿼럼(quorum) = **2** 로 설정 (과반수 필요).
  서버3은 실제 데이터를 갖지 않고 장애 판단 투표에만 참여하는 arbiter 역할입니다.
- Redis 버전은 **7.2-alpine**으로 고정했습니다. Redis는 7.4부터 라이선스가
  RSALv2/SSPLv1로 변경되어 상용(특히 SaaS성) 사용에 제약이 있어, 마지막 BSD
  라이선스 버전인 7.2를 사용합니다.
- 요청에 따라 `requirepass` 인증은 적용하지 않았습니다. (필요 시 아래 "인증 추가" 참고)
- 3대의 물리/가상 서버에 각각 배포하는 구조이므로, 컨테이너 간 통신에
  `network_mode: host`를 사용합니다. 브릿지 네트워크를 쓰면 Sentinel/Replica가
  서로에게 알리는 IP가 컨테이너 내부 IP가 되어 다른 서버에서 접근할 수 없는
  문제가 생기기 때문입니다.
- 각 서비스의 conf 파일은 단일 파일이 아니라 **디렉터리 단위**로 바인드
  마운트합니다 (`master-conf/`, `sentinel-conf/` 등). Redis/Sentinel은 설정을
  재작성(rewrite)할 때 임시 파일을 쓴 뒤 rename으로 덮어쓰는데, 단일 파일을
  직접 바인드 마운트하면 이 rename이 `Permission denied`로 실패합니다. 이 문제는
  로컬 시뮬레이션(`local-test/`)에서 실제로 재현/수정 후 검증했습니다.

## 배포 순서

### 1) 서버1 (Master)

```bash
cd server1-master
# master-conf/redis.conf, sentinel-conf/sentinel.conf 안의 <MASTER_IP> 를
# 서버1의 실제 IP로 교체
sed -i 's/<MASTER_IP>/10.0.0.1/g' master-conf/redis.conf sentinel-conf/sentinel.conf   # 예시
docker compose up -d
```

> `master-conf/redis.conf`에는 `<MASTER_IP>`가 실제로 쓰이진 않지만, 사람이 보고
> 실수 없이 IP를 통일해서 넣을 수 있도록 동일 플레이스홀더를 사용했습니다.

### 2) 서버2 (Replica)

```bash
cd server2-replica
# <MASTER_IP> 를 서버1의 실제 IP로 교체 (replica-conf/redis.conf, sentinel-conf/sentinel.conf 둘 다)
sed -i 's/<MASTER_IP>/10.0.0.1/g' replica-conf/redis.conf sentinel-conf/sentinel.conf
docker compose up -d
```

### 3) 서버3 (Sentinel 전용)

```bash
cd server3-sentinel-arbiter
sed -i 's/<MASTER_IP>/10.0.0.1/g' sentinel-conf/sentinel.conf
docker compose up -d
```

## 동작 확인

```bash
# 복제 상태 확인 (서버1)
docker exec -it redis-master redis-cli info replication

# 복제 상태 확인 (서버2)
docker exec -it redis-replica redis-cli info replication

# Sentinel이 master를 제대로 인지하는지 확인 (아무 서버에서나)
docker exec -it sentinel-1 redis-cli -p 26379 sentinel masters
docker exec -it sentinel-1 redis-cli -p 26379 sentinel replicas mymaster
docker exec -it sentinel-1 redis-cli -p 26379 sentinel sentinels mymaster
```

`sentinel sentinels mymaster` 실행 시 서버1 기준으로 나머지 2개 sentinel(서버2,
서버3)이 보여야 정상입니다.

## Failover 테스트

```bash
# 서버1에서 master 강제 종료
docker stop redis-master
```

몇 초 후 (`down-after-milliseconds: 5000` 기준) Sentinel들이 과반수 투표(2/3)로
서버2의 redis-replica를 새로운 master로 승격시킵니다.

```bash
# 승격 확인 (서버2)
docker exec -it redis-replica redis-cli info replication
# role:master 로 바뀌어야 정상

# 아무 sentinel에서나 현재 master 조회
docker exec -it sentinel-2 redis-cli -p 26379 sentinel get-master-addr-by-name mymaster
```

이후 서버1의 redis-master를 다시 살리면, Sentinel이 자동으로 이를 replica로
편입시킵니다 (`master-conf/redis.conf`에 `replicaof`가 없어도 sentinel이
런타임에 `REPLICAOF`를 주입하고, `CONFIG REWRITE`로 영구 반영합니다).

## 로컬(단일 macOS 호스트)에서 먼저 시뮬레이션해보기

`network_mode: host`는 Linux에서만 정확히 동작하므로, 실제 3대 서버에 배포하기
전에 failover 로직 자체만 macOS에서 빠르게 확인하고 싶다면 `local-test/`
디렉터리를 사용하세요.

```bash
cd local-test
docker compose up -d
docker compose ps                       # 5개 컨테이너(master, replica, sentinel x3) 확인

# failover 유발
docker stop local-redis-master
sleep 15
docker exec local-sentinel-1 redis-cli -p 26379 sentinel get-master-addr-by-name mymaster
docker exec local-redis-replica redis-cli info replication | grep role   # role:master 확인

# 원래 master 복구 → 자동으로 replica 편입되는지 확인
docker start local-redis-master
sleep 10
docker exec local-redis-master redis-cli info replication | grep role    # role:slave 확인

docker compose down -v   # 정리
```

이 구성은 일반 브릿지 네트워크 + 포트 매핑(6379/6380, 26379/26380/26381)을
쓰고, Sentinel이 Docker 서비스명(`redis-master`)을 호스트명으로 인식하도록
`sentinel resolve-hostnames yes` / `sentinel announce-hostnames yes`가 추가돼
있습니다. **실제 배포 파일(`server*/`)에는 이 옵션이 없습니다** — 그쪽은
호스트명 대신 실제 IP(`<MASTER_IP>`)를 쓰기 때문에 필요 없습니다.

이 저장소의 5개 컨테이너 기동, replication 확인, failover(마스터 다운→승격),
구 마스터 복구 후 replica 자동 재편입까지 실제로 실행해 검증했습니다.

## 인증(requirepass) 추가가 필요해지면

1. 각 `redis.conf`(master-conf/replica-conf)에 `requirepass <비밀번호>` 와
   `masterauth <비밀번호>` 추가
2. 각 `sentinel.conf`에 `sentinel auth-pass mymaster <비밀번호>` 추가
3. 클라이언트/애플리케이션 연결 문자열에도 동일 비밀번호 반영

## 참고 / 운영 시 체크포인트

- `network_mode: host`를 쓰므로 방화벽에서 각 서버 간 `6379`(Redis), `26379`
  (Sentinel), 그리고 replica가 master로부터 받는 복제용 포트가 열려 있어야
  합니다.
- `protected-mode no` + 인증 미적용 상태이므로, 반드시 서버 자체 방화벽/보안그룹
  에서 신뢰할 수 있는 IP 대역만 6379/26379 포트에 접근 가능하도록 제한하세요.
- Sentinel과 Redis는 failover/승격 시 자신의 conf 파일을 재작성(`CONFIG
  REWRITE`, Sentinel 자체 rewrite)합니다. 이게 정상 동작하려면 conf 파일이
  담긴 **디렉터리 전체**가 바인드마운트되어 있어야 하고(현재 구성이 이미 그렇게
  되어 있음), 컨테이너가 그 디렉터리에 쓰기 권한을 가져야 합니다.
