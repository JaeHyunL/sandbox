import os

from flask import Flask, jsonify
from redis.exceptions import ConnectionError, ReadOnlyError, TimeoutError
from redis.sentinel import Sentinel

app = Flask(__name__)

MASTER_NAME = os.environ.get("REDIS_MASTER_NAME", "mymaster")
SENTINEL_HOSTS = [
    ("sentinel-1", 26379),
    ("sentinel-2", 26379),
    ("sentinel-3", 26379),
]

# redis-py의 Sentinel 클라이언트: 특정 redis 호스트를 하드코딩하지 않고,
# 매 요청마다 sentinel들에게 "지금 master가 누구야?"를 물어서 연결한다.
# 이게 바로 이전 대화에서 설명한 "앱은 master IP를 하드코딩하면 안 된다"의 실제 구현.
sentinel = Sentinel(SENTINEL_HOSTS, socket_timeout=0.5, decode_responses=True)


def get_master():
    return sentinel.master_for(MASTER_NAME, socket_timeout=0.5)


def get_replica():
    return sentinel.slave_for(MASTER_NAME, socket_timeout=0.5)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/status")
def status():
    """지금 sentinel들이 인식하고 있는 master/replica 주소를 그대로 보여준다."""
    try:
        master_addr = sentinel.discover_master(MASTER_NAME)
    except Exception as e:
        master_addr = f"unavailable ({e})"
    try:
        replica_addrs = sentinel.discover_slaves(MASTER_NAME)
    except Exception as e:
        replica_addrs = f"unavailable ({e})"
    return jsonify({"master": master_addr, "replicas": replica_addrs})


@app.get("/set/<key>/<value>")
def set_key(key, value):
    try:
        get_master().set(key, value)
    except (ConnectionError, TimeoutError, ReadOnlyError) as e:
        # master가 failover 중이거나 아직 못 찾은 경우. 클라이언트는 잠깐 뒤 재시도하면 된다.
        return jsonify({"error": f"master unavailable, retry shortly: {e}"}), 503
    return jsonify({"result": "ok", "key": key, "value": value})


@app.get("/get/<key>")
def get_key(key):
    try:
        value = get_master().get(key)
    except (ConnectionError, TimeoutError) as e:
        return jsonify({"error": f"master unavailable, retry shortly: {e}"}), 503
    return jsonify({"key": key, "value": value, "source": "master"})


@app.get("/get-from-replica/<key>")
def get_from_replica(key):
    try:
        value = get_replica().get(key)
    except (ConnectionError, TimeoutError) as e:
        return jsonify({"error": f"replica unavailable, retry shortly: {e}"}), 503
    return jsonify({"key": key, "value": value, "source": "replica"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
