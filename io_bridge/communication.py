"""
CommunicationMod stdin/stdout bridge.

Wraps the protocol used to talk to Slay the Spire's CommunicationMod:
  - reads JSON state messages from stdin
  - sends commands (strings like "play 1 0", "choose 2", "wait 30") on stdout
"""
import json
import sys
import logging

log = logging.getLogger("STS_AI")


def announce_ready():
    """Send the initial 'ready' handshake. Called once at startup."""
    log.info(f"🚨 현재 통신 모드가 훔쳐 쓰고 있는 파이썬 경로: {sys.executable}")
    print("ready", flush=True)
    log.info("✅ 파이썬 에이전트 연결 완료!")


def read_state():
    """
    Generator that yields parsed JSON messages from stdin, one per line.
    Returns None when the stream closes so callers can break their loop.
    """
    while True:
        line = sys.stdin.readline()
        if not line:
            log.info("❌ 게임과 연결이 끊어졌습니다.")
            return

        line = line.strip()
        if not line:
            continue

        data = json.loads(line)
        yield data


def send_command(cmd):
    """Send a single command string to CommunicationMod."""
    print(cmd, flush=True)
