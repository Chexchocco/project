"""
Map expert.

Picks the best route from the current node toward the act boss. Built around a
single `StateVector` (HP, gold, deck health, relic flags, …) that selects one of
four scoring profiles — survive / farm / power_spike / boss_prep — which then
drives a per-room scoring table. The routing itself is a bottom-up DP over the
full map graph: every node's `path_value` is its own room score plus the best
child's `path_value`. The chosen child is the argmax over the current node's
children.

This is the deterministic-heuristic version (CLAUDE.md: build deterministic
first, swap in a toolformer later).
"""

import logging
from dataclasses import dataclass

from experts import synergy as synergy_expert

log = logging.getLogger("STS_AI")



# ─── Scoring table ──────────────────────────────────────────────────────────
# SCORE_TABLE[symbol][mode]로 각 방의 점수를 계산할 수 있다. 각 Coefficient를 튜닝함으로써 개선이 가능하다.
# Symbol은 Communication mode convention 을 따른다(M=monster, E=elite, ?=event, $=shop, R=rest, T=treasure, B=boss)
# mode 설명 : 
# survive : CRITICAL_HP_RATIO 미만일때 선택되는 모드
# farm : 골드가 많거나 덱이 부족할 때 상점/이벤트 우선해 자원 수집
# power_spike : 나머지 모든 상태. 전투로 카드/보상 수집
# boss_prep : 보스전 준비
SCORE_TABLE = {
    "M": {"survive": -10, "farm": +5, "power_spike": +10, "boss_prep": -5},
    "E": {"survive": -50, "farm": +20, "power_spike": +25, "boss_prep": -10},
    "?": {"survive": +15, "farm": +10, "power_spike": +10, "boss_prep": +5},
    "$": {"survive": +5, "farm": +25, "power_spike": +15, "boss_prep": +5},
    "R": {"survive": +50, "farm": +5, "power_spike": +15, "boss_prep": +50},
    "T": {"survive": +20, "farm": +40, "power_spike": +20, "boss_prep": +20},
    "B": {"survive": 0, "farm": 0, "power_spike": 0, "boss_prep": 0},
}

ACT_COMPLETION_BONUS = 100  # Every reachable path must end at the boss.

CRITICAL_HP_RATIO = 0.3
GOLD_PRESSURE_THRESHOLD = 300
PRE_BOSS_DISTANCE = 2


# ─── State vector ───────────────────────────────────────────────────────────
# 맵 이동 결정에 필요한 모든 정보를 하나의 벡터로 묶은 것.
# 체력, 자원, 덱 상태(시너지에서 계산), 진행 상황, 모드 정보로 구분된다.
@dataclass
class StateVector:
    # Survival
    hp_current: int = 0
    hp_max: int = 1
    hp_ratio: float = 1.0
    critical_hp: bool = False # CRITICAL_HP_RATIO 이하이면 휴식

    # Economy
    gold: int = 0
    gold_pressure: bool = False # GOLD_PRESSURE_THRESHOLD 이상이면 상점 방문

    # Deck - ⭐⭐ 용진's 시너지 구현에 따라 수정 필요 ⭐⭐
    deck_size: int = 0
    curse_count: int = 0
    status_count: int = 0
    needs_card: bool = False
    needs_upgrade: bool = False

    # Progression
    floor: int = 0 # 현재 층
    act_num: int = 1 # 현재 막
    floors_to_boss: int = 99 # 보스까지 남은 층수
    pre_boss: bool = False # 보스까지 PRE_BOSS_DISTANCE 이하이면 보스전 준비

    # Derived
    mode: str = "power_spike"

#  CommunicationMod에서 받은 game_state JSON을 파싱해서 StateVector를 만드는 함수.
def build_state_vector(state) -> StateVector:
    hp_current = state.get("current_hp", 0) or 0
    hp_max = state.get("max_hp", 1) or 1
    hp_ratio = hp_current / hp_max if hp_max else 0.0

    deck = state.get("deck", []) or []
    try:
        profile = synergy_expert.score_deck(deck)
    except NotImplementedError:
        profile = {
            "size": len(deck),
            "curses": 0,
            "statuses": 0,
            "needs_card": len(deck) < 12,
            "needs_upgrade": False,
        }

    floor = state.get("floor", 0) or 0
    act_num = state.get("act", state.get("act_num", 1)) or 1

    floors_to_boss = _floors_to_boss_from_state(state, floor)

    sv = StateVector(
        hp_current=hp_current,
        hp_max=hp_max,
        hp_ratio=hp_ratio,
        critical_hp=hp_ratio < CRITICAL_HP_RATIO,
        gold=state.get("gold", 0) or 0,
        deck_size=profile["size"],
        curse_count=profile["curses"],
        status_count=profile["statuses"],
        needs_card=profile["needs_card"],
        needs_upgrade=profile["needs_upgrade"],
        floor=floor,
        act_num=act_num,
        floors_to_boss=floors_to_boss,
        pre_boss=floors_to_boss <= PRE_BOSS_DISTANCE,
    )
    sv.gold_pressure = sv.gold >= GOLD_PRESSURE_THRESHOLD
    sv.mode = _pick_mode(sv)
    return sv


def _pick_mode(sv: StateVector):
    if sv.pre_boss:
        return "boss_prep"
    if sv.critical_hp:
        return "survive"
    if sv.gold_pressure or sv.needs_card:
        return "farm"
    return "power_spike"


# state 정보에서 map 정보만 반환
def _collect_map_nodes(state):
    nodes = state.get("map", [])
    return nodes if isinstance(nodes, list) else []


# 보스까지 남은 층수 계산
def _floors_to_boss_from_state(state, current_floor: int):
    nodes = _collect_map_nodes(state)
    if nodes:
        max_y = max(n.get("y", 0) for n in nodes) if nodes else 0
        # Boss sits one floor above the top regular node.
        return max(0, (max_y + 1) - current_floor)
    return max(0, 17 - current_floor)


def parse_map(state):
    """
       nodes: dict[(x, y) -> node]. 노드 정보를 딕셔너리로 저장. 좌표를 통해 raw node 의 정보 접근 가능
       children_of: dict[(x, y) -> list[(x, y)]]. 자식 노드의 좌표 리스트
    """
    raw_nodes = _collect_map_nodes(state)
    nodes = {}
    children_of = {}

    for n in raw_nodes:
        x = n.get("x")
        y = n.get("y")
        if x is None or y is None:
            continue
        key = (x, y)
        nodes[key] = n
        kids = []
        for c in n.get("children", []) or []:
            cx = c.get("x")
            cy = c.get("y")
            if cx is not None and cy is not None:
                kids.append((cx, cy))
        children_of[key] = kids

    return nodes, children_of


# single node의 점수를 계산하는 함수
def _score_room(node: dict, sv: StateVector):
    symbol = node.get("symbol")
    base = SCORE_TABLE[symbol][sv.mode]

    # SCORE_TABLE만으로 표현 못하는 상황. 예를 들면 엘리트 전투인데 hp 얼마 안 남은 경우.
    # 이후 자기평가 모듈이 구현되면 개선될 수 있다.
    if symbol == "E" and sv.critical_hp:
        base -= 30
    elif symbol == "$" and sv.gold >= 200:
        base += 15
    elif symbol == "R" and sv.needs_upgrade and sv.mode != "survive":
        base += 10

    return base


# 모든 노드에서 보스까지의 경로 총점을 계산한다. Bottom-up DP 이용
# 결과: pick_route가 current 노드의 자식들 중 path_value가 가장 높은 것을 선택한다.
def _compute_path_values(nodes, children_of, sv: StateVector) -> dict:
    path_value = {}
    """
    함수 수행 결과 아래와 같은 형식으로 path value가 저장된다.
    path_value = {
    (0,2): 110,  (1,2): 110,
    (0,1): 125,  (1,1): 135,
    (0,0): 145,  (1,0): 145
    }
    """

    def dp(key):
        if key in path_value:
            return path_value[key]
        own = _score_room(nodes[key], sv)
        kids = children_of.get(key, [])
        valid_kids = [c for c in kids if c in nodes]
        if not valid_kids:
            # 자식이 모두 nodes 밖 → 보스를 가리키는 노드
            path_value[key] = own + ACT_COMPLETION_BONUS
        else:
            path_value[key] = own + max(dp(c) for c in valid_kids)
        return path_value[key]

    for key in nodes:
        dp(key)
    return path_value


# CommunicationMod의 next_nodes를 기반으로 DP 점수를 비교하고, 배열 인덱스를 반환한다.
def pick_route(state, sv: StateVector) -> int:
    nodes, children_of = parse_map(state)
    next_nodes = state.get("screen_state", {}).get("next_nodes")
    path_value = _compute_path_values(nodes, children_of, sv)

    scored = []
    for idx, node in enumerate(next_nodes):
        x = node["x"]
        y = node["y"]
        score = path_value[(x, y)]
        scored.append((score, idx, x, y))

    scored.sort(key=lambda t: (-t[0], t[2]))
    best_value, best_idx, best_x, best_y = scored[0]
    log.info(f"🗺️ → choose idx={best_idx} (x={best_x}, y={best_y}, value={best_value})")
    return best_idx



def handle_map(state):
    log.info("🗺️ [이동] 맵 탐색 에이전트 가동")
    if state.get("screen_state", {}).get("boss_available"):
        log.info("👑 보스 입장")
        print("choose 0", flush=True)
        return
    sv = build_state_vector(state)
    log.info(
        f"📊 state: mode={sv.mode} hp={sv.hp_current}/{sv.hp_max} gold={sv.gold} "
        f"floor={sv.floor} to_boss={sv.floors_to_boss} deck={sv.deck_size}"
    )
    print(f"choose {pick_route(state, sv)}", flush=True)
