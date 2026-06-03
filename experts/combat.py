"""
Combat expert — hybrid (deterministic simulator + LLM hint layer).

═══════════════════════════════════════════════════════════════════════════
전략 (이 모듈이 구현하는 의사결정 규칙)
═══════════════════════════════════════════════════════════════════════════
[적이 공격할 때]
  - 한 공격당 현재 HP의 10%까지 피해는 용인 (HP 낮을수록 용인치 축소).
  - 그 이상이면 최대한 막는다.
  - 강한 공격 적을 이번 턴 죽일 수 있으면 죽인다 (incoming 제거).

[적이 공격 안 할 때]
  - 약화/힘증가/취약 셋업을 먼저 깔고 딜한다 (DFS가 순서 자동 최적화).
  - 또는 장기 이득 파워카드(타락, 악마형상)를 둔다.

[공통]
  - 내 모든 공격으로도 적 방어막을 못 뚫으면(바리케이드 없는 한) 공격 낭비 →
    지속 효과/자버프만 (block은 reset되므로 HP 데미지 0 = score 0, DFS가 회피).
  - 여러 적: 죽일 수 있는 고공격 적 우선, 대장/미니언 엔진은 LLM 힌트로 우선.

[다중턴 위협] (monsterDB.patterns 기반, deterministic)
  - 이번 턴 = 게임 state(정확), 다음 턴~ = patterns 기댓값 × strength 성장.
  - 스케일링 적(Ritual/Strength Up)은 "지금 죽이는 가치"가 커진다.

[LLM 역할] (monsterDB.logic_notes 프로즈 해석, 턴/전투당 1회)
  - Split 임계(50%), 충전 후 대공격, 연속 무브 제한 등을 구조화 제약으로 변환.
  - 하드 가드(절대 죽지 않음, lethal 우선)는 LLM 힌트를 항상 오버라이드.
═══════════════════════════════════════════════════════════════════════════
"""

import os
import re
import json
import logging

import ollama

from db import db_loader
from config import LOCAL_PATH, MODEL_NAME

log = logging.getLogger("STS_AI")
combat_log = logging.getLogger("CARD_PICKER")

with open(os.path.join(LOCAL_PATH, "db", "monsterDB.json"), "r", encoding="utf-8") as f:
    _MDB = json.load(f)
MONSTERS_INFO = _MDB.get("monsters", {})

from run_logger import *
# ╔══════════════════════════════════════════════════════════════════════╗
# ║ 특수 카드 레지스트리 — parser가 못 잡는 효과를 시뮬에 직접 반영       ║
# ╚══════════════════════════════════════════════════════════════════════╝

# 가변/다단 데미지 카드: parser가 못 잡는 hit 수를 보정 (이름 → 실제 hits)
# (parsed_item의 hits 필드가 부정확한 카드들)
_FIXED_HITS = {
    'Sword Boomerang': 3, 'Sword Boomerang+': 4,
    'Twin Strike': 2, 'Twin Strike+': 2,
    'Pummel': 4, 'Pummel+': 5,
}

# 적 strength를 (이번 턴 한정 또는 영구) 깎는 카드 → incoming 감소
_STRENGTH_LOSS = {
    'Dark Shackles': 9, 'Dark Shackles+': 15,
    'Disarm': 2, 'Disarm+': 3,
}

# 에너지를 추가로 주는 카드 ([R] 기호 = 1 에너지). DFS가 추가 플레이를 보게 함.
# → "에너지 부족 + 손패에 강한 고코 카드" 상황에서 에너지 카드를 먼저 쓰게 됨
_ENERGY_GAIN = {
    'Offering': 2, 'Offering+': 2,
    'Bloodletting': 1, 'Bloodletting+': 2,
    'Seeing Red': 2, 'Seeing Red+': 2,
}
# Double Energy는 현재 에너지를 2배 (별도 처리)
_DOUBLE_ENERGY = {'Double Energy', 'Double Energy+'}

# HP를 소모하는 자해 카드 (안전 마진 가드가 무모한 사용 방지)
_SELF_HP_LOSS = {
    'Offering': 6, 'Offering+': 6,
    'Bloodletting': 3, 'Bloodletting+': 3,
}

# Rage: 이번 턴 공격 카드를 낼 때마다 방어도 획득 (즉시 방어가 아님 → 맨 먼저 써야 이득)
_RAGE_BLOCK = {'Rage': 3, 'Rage+': 5}


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ 손패 enrichment & 공용 유틸                                          ║
# ╚══════════════════════════════════════════════════════════════════════╝

def enrich_hand(hand):
    for card in hand:
        if "damage" in card:
            continue
        info = db_loader.get_card_info(card)
        if info:
            card.update({
                "damage": info.get("damage", 0),
                "block": info.get("block", 0),
                "draw": info.get("draw", 0),
                "hits": info.get("hits", 1),
                "is_aoe": info.get("is_aoe", False),
                "effects": info.get("effects", {}),
                "card_type": info.get("type", "Skill"),
                "base_value": info.get("base_value", 0),
            })
    return hand


def _avg_pile_value(cards):
    """앞으로 뽑힐 카드들(뽑을더미+버린더미)의 평균 base_value. 비었으면 기본 6.0.
    드로우 카드의 기댓값 평가에 쓰임."""
    vals = []
    for c in cards:
        info = db_loader.get_card_info(c)
        if info:
            vals.append(info.get('base_value', 5.0))
    return sum(vals) / len(vals) if vals else 6.0


def _power_amount(entity, pid):
    for p in entity.get('powers', []):
        if p.get('id') == pid:
            return p.get('amount', 0)
    return 0


def _add_power(entity, pid, amount):
    for p in entity.get('powers', []):
        if p.get('id') == pid:
            p['amount'] = p.get('amount', 0) + amount
            return
    entity.setdefault('powers', []).append({'id': pid, 'amount': amount})


def _is_alive(m):
    return not m.get('is_gone') and not m.get('half_dead') and m.get('current_hp', 0) > 0


def _alive_indices(monsters):
    return [i for i, m in enumerate(monsters) if _is_alive(m)]


def _card_cost(card, energy):
    cost = card.get('cost', 0)
    if isinstance(cost, str) and cost.upper() == 'X':
        return energy
    try:
        return int(cost)
    except (TypeError, ValueError):
        return 0


def _parse_chance(s):
    """'30%' → 0.3, 없으면 1.0."""
    m = re.search(r'\d+', str(s))
    return int(m.group()) / 100 if m else 1.0


def _parse_pattern_damage(mv):
    """패턴의 damage를 (per_hit, hits)로 정규화.
    '16'→(16,1), '6x2'→(6,2), '5-7'→(6,1 평균), 'NxM/공식'→근사 또는 (0,1)."""
    dmg = mv.get('damage', 0)
    hits = mv.get('hits', 1)
    if isinstance(dmg, int):
        return dmg, hits
    s = str(dmg)
    # 'AxB' 다단히트
    m = re.match(r'(\d+)\s*[xX]\s*(\d+)', s)
    if m:
        return int(m.group(1)), int(m.group(2))
    # 'A-B' 범위 → 평균
    m = re.match(r'(\d+)\s*-\s*(\d+)', s)
    if m:
        return (int(m.group(1)) + int(m.group(2))) // 2, hits
    # 'AxN', 공식 등 가변 → 첫 숫자를 per-hit로, 평균 2~3회 가정
    m = re.match(r'(\d+)', s)
    if m:
        return int(m.group(1)), 2
    return 0, 1


def _base_damage(card, sim, cost):
    """카드의 hit당 기본 데미지(strength 반영, weak/vuln 적용 전)와 hit 수를 계산.
    가변/특수 카드(몸통박치기/완벽한타격/대검 등)를 여기서 통합 처리.
    return (per_hit_damage, hits). 데미지 카드 아니면 (0, 0)."""
    name = card.get('name', '')
    upg = name.endswith('+')
    s = sim.p_strength
    dmg = card.get('damage', 0)

    # ── 특수 스케일링 카드 ──────────────────────────────
    if name in ('Body Slam', 'Body Slam+'):
        return sim.p_block, 1                          # 현재 방어도만큼 (strength 무관)
    if name in ('Heavy Blade', 'Heavy Blade+'):
        return dmg + s * (5 if upg else 3), 1          # strength ×3 (강화 ×5)
    if name in ('Perfected Strike', 'Perfected Strike+'):
        per = 3 if upg else 2
        return dmg + s + per * sim.strike_count, 1     # 덱의 'Strike' 이름 카드당 +2/3
    if name in ('Rampage', 'Rampage+'):
        inc = 8 if upg else 5
        return dmg + s + inc * sim.rampage_count, 1    # 사용 횟수당 증가
    if name in ('Fiend Fire', 'Fiend Fire+'):
        per = 10 if upg else 7
        n = len(sim.hand) - 1                          # 손패 나머지 소멸 수
        return per * max(0, n) + s, 1                  # 소멸 카드당 데미지(단일 큰 한방)

    # ── 일반 공격 ───────────────────────────────────────
    if dmg <= 0:
        return 0, 0
    # X코스트 다단(Whirlwind 등): 남은 에너지(cost)만큼 타격
    if str(card.get('cost', '')).upper() == 'X' or card.get('is_x_cost'):
        return dmg + s, max(1, cost)
    hits = _FIXED_HITS.get(name, card.get('hits', 1))
    if not isinstance(hits, int):
        hits = max(1, cost)
    return dmg + s, hits


def _monster_db(name):
    if name in MONSTERS_INFO:
        return MONSTERS_INFO[name]
    return next((v for k, v in MONSTERS_INFO.items() if k.lower() == str(name).lower()), None)


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ 몬스터별 전투 클래스 → 태도 (monster.md 분류, 결정적)               ║
# ╚══════════════════════════════════════════════════════════════════════╝
# Class 1 Aggro/Scaling → BURST  : 체력 손해 감수, 빨리 처치 (방어코스트→공격)
# Class 2 Setup/Defense → TURTLE : 전투 길어져도 체력 보존, 방어 사이클 우선
# Class 3 High-Threat   → RACE   : 체력 보존 + 빠른 처치 동시 (포션 권장)
# Class 4 Gimmick       → GIMMICK: 기믹 파훼 우선 (LLM/hp_stops 주도, 중립 방어)
_CLASS_ATTITUDE = {1: 'BURST', 2: 'TURTLE', 3: 'RACE', 4: 'GIMMICK'}
_CLASS_BY_TYPE = {'Boss': 3, 'Elite': 3, 'Minion': 2, 'Normal': 2}

# 태도별 튜닝: (block 용인 배수, 공격/처치 가치 배수)
_ATTITUDE_TUNING = {
    'BURST':   (1.6, 1.5),   # 데미지 감수(용인↑) + 공격 가치↑
    'RACE':    (0.6, 1.5),   # 체력 보존(용인↓) + 공격 가치↑ (둘 다 최대치)
    'TURTLE':  (0.5, 1.0),   # 철벽 방어(용인↓↓)
    'GIMMICK': (1.0, 1.0),   # 중립 (hp_stops/priority_target가 주도)
    'NORMAL':  (1.0, 1.0),
}

# 처치/오버킬 튜닝
#  _KILL_FLAT_BONUS: 적 1기 제거의 기본 가치(위협량 kill_saves와 별개의 '머릿수 감소' 가치).
#  _OVERKILL_RATE: 처치에 필요 이상으로 쏟은 데미지(낭비)의 점당 페널티.
#    → 딸피 적에게 강한 카드를 낭비하지 않고, 다른 적/다음 턴에 그 데미지를 쓰게 유도.
#    단 강공격 예정 적은 survival 항이 처치를 강하게 보상하므로 여전히 즉시 처치.
_KILL_FLAT_BONUS = 200
_OVERKILL_RATE = 9


def _monster_class(monster):
    """몬스터의 전투 클래스(1-4). DB에 combat_class 없으면 타입 기반 기본값."""
    info = _monster_db(monster.get('name')) or {}
    c = info.get('combat_class')
    if c in (1, 2, 3, 4):
        return c
    return _CLASS_BY_TYPE.get(info.get('type'), 2)


def _fight_attitude(monsters):
    """살아있는 적들의 클래스로부터 전투 전반의 결정적 태도 결정.
    우선순위: Class 3(고위협) > Class 1(어그로) > Class 4(기믹) > Class 2(방어).
    (가장 위협적인 적의 태도가 전투를 지배 — 강적을 빨리 잡는 게 생존에 직결)."""
    classes = {_monster_class(m) for m in monsters if _is_alive(m)}
    if not classes:
        return 'NORMAL'
    if 3 in classes:
        return 'RACE'
    if 1 in classes:
        return 'BURST'
    if 4 in classes:
        return 'GIMMICK'
    return 'TURTLE'


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ 다중턴 위협 예측 (deterministic, monsterDB.patterns 기반)            ║
# ╚══════════════════════════════════════════════════════════════════════╝

def _strength_per_turn(monster):
    return _power_amount(monster, 'Ritual') + _power_amount(monster, 'Strength Up')


def _expected_attack(monster):
    """patterns의 공격 무브를 확률 가중 평균한 다음-턴 기대 데미지. DB 없으면 None."""
    info = _monster_db(monster.get('name'))
    if not info:
        return None
    total = 0.0
    for mv in info.get('patterns', {}).values():
        per_hit, hits = _parse_pattern_damage(mv)
        if per_hit > 0:
            total += per_hit * hits * _parse_chance(mv.get('chance', '100%'))
    return total


def _projected_attack(monster, turns_ahead):
    """turns_ahead 턴 뒤 예상 공격력 (strength 성장 반영)."""
    base = _expected_attack(monster)
    if base is None:
        return 0.0
    str_gain = _strength_per_turn(monster)
    info = _monster_db(monster.get('name')) or {}
    patterns = info.get('patterns', {})
    avg_hits = max(1, sum(_parse_pattern_damage(mv)[1] for mv in patterns.values())
                   / max(1, len(patterns)))
    return base + str_gain * turns_ahead * avg_hits


def _kill_saved_damage(monster):
    """이 적을 지금 죽이면 막는 미래 위협 총합 (이번 턴 + 2턴)."""
    if "ATTACK" not in monster.get('intent', '') and _expected_attack(monster) is None:
        return 0.0
    now = monster.get('move_adjusted_damage', 0) * monster.get('move_hits', 1)
    return now + _projected_attack(monster, 1) + _projected_attack(monster, 2)


def _incoming_now(monsters):
    """이번 턴 받을 데미지 (게임 state 기준, 정확)."""
    total = 0
    for m in monsters:
        if _is_alive(m) and "ATTACK" in m.get('intent', ''):
            mult = 0.75 if m.get('_weak_applied') else 1.0
            total += int(m.get('move_adjusted_damage', 0) * m.get('move_hits', 1) * mult)
    return total


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ 시뮬레이션 상태                                                       ║
# ╚══════════════════════════════════════════════════════════════════════╝

def _clone_monster(m):
    return {
        'name': m.get('name'),
        'current_hp': m.get('current_hp', 0),
        'max_hp': m.get('max_hp', 0),
        'block': m.get('block', 0),
        'is_gone': m.get('is_gone', False),
        'half_dead': m.get('half_dead', False),
        'intent': m.get('intent', ''),
        'move_adjusted_damage': m.get('move_adjusted_damage', 0),
        'move_hits': m.get('move_hits', 1),
        'powers': [p.copy() for p in m.get('powers', [])],
        '_curl_used': False,
        '_malleable_stacks': 0,
        '_weak_applied': m.get('_weak_applied', False),
    }


class SimState:
    def __init__(self, hand, energy, monsters, player, hints=None, strike_count=0, avg_draw_value=6.0):
        self.original_hand = hand  # 게임 state의 최신 cost를 반영하기 위해 원본 참조 유지
        self.hand = [c.copy() for c in hand]
        self.hand_indices = list(range(len(hand)))  # 각 카드의 원본 인덱스 추적
        self.energy = energy
        self.monsters = [_clone_monster(m) for m in monsters]
        self.p_strength = _power_amount(player, 'Strength')
        self.p_dexterity = _power_amount(player, 'Dexterity')
        self.p_block = player.get('block', 0)
        self.p_weak = _power_amount(player, 'Weak')      # 내 공격 -25%
        self.p_frail = _power_amount(player, 'Frail')    # 내 방어 -25%
        self.p_hp = player.get('current_hp', 0)
        self.p_hp_start = player.get('current_hp', 0)  # 턴 시작 HP (회복량 평가용)
        self.p_max_hp = player.get('max_hp', 1)
        self.hints = hints or {}
        self.strike_count = strike_count  # 덱 전체 'Strike' 이름 카드 수 (완벽한 타격용)
        self.rampage_count = 0   # Rampage 이번 전투 사용 횟수
        self.tempo = 0           # 파워카드 장기 가치 누적
        self.extra_incoming = 0  # Enrage/Curiosity로 적이 강해진 이번 턴 분
        self.enemy_str_given = 0 # 적에게 준 영구 strength 총량 (다중턴 페널티)
        self.recoil = 0          # Thorns/Sharp Hide 자기 피해
        self.wasted_attacks = 0  # 방어막에 막혀 HP 데미지 0인 공격 (reset 적 한정)
        self.block_stripped = 0  # 방어막 보존 적(Barricade 등)의 방어막을 깎은 양
        self.draw_bonus = 0      # 드로우 카드의 기댓값 가치 (사용 시점에 1회 고정 적립)
        self.avg_draw_value = avg_draw_value  # 뽑을더미/버린더미 카드 평균 가치
        # Corruption(스킬 비용 0): 이미 발동 중이거나 이번 턴 발동하면 True → 스킬 0코
        self.corruption = any(p.get('id') == 'Corruption' for p in player.get('powers', []))
        # Rage(공격당 방어도): 이미 발동 중이면 그 값, 이번 턴 Rage 쓰면 누적
        self.rage_block = _power_amount(player, 'Rage')
        # Barricade(방어도 영구 보존): 발동 중이면 방어도가 다음 턴까지 유지 → 지속 가치
        self.barricade = any(p.get('id') == 'Barricade' for p in player.get('powers', []))
        # Combust 턴 종료 데미지 (이미 발동 중인 스택 × 5, + 이번 턴 추가분)
        self.end_of_turn_damage = _power_amount(player, 'Combust') * 5
        # 디버프 추적 (Vulnerable, Weak 적용된 정도 — score에서 이득 계산)
        self.vulnerable_applied = 0  # 적용된 vulnerable 총량
        self.weak_applied = 0  # 적용된 weak 총량
        self.overkill = 0  # 처치에 필요 이상으로 쏟은 데미지(낭비) 누적
        self.alive_at_start = frozenset(i for i, m in enumerate(self.monsters) if _is_alive(m))
        # 적별 "죽이면 막는 미래 위협" 미리 계산 (트리 전체 재사용)
        self._kill_saves = {i: _kill_saved_damage(self.monsters[i]) for i in self.alive_at_start}
        # 시작 시 HP (hp_stops 버스트 보너스 판정용)
        self._hp_at_start = {i: m['current_hp'] for i, m in enumerate(self.monsters)}

    def clone(self):
        c = SimState.__new__(SimState)
        c.original_hand = self.original_hand  # 원본 참조 유지 (게임 state 최신 cost)
        c.hand = [card.copy() for card in self.hand]
        c.hand_indices = self.hand_indices.copy()  # 인덱스 추적
        c.energy = self.energy
        c.monsters = [_clone_monster(m) for m in self.monsters]
        c.p_strength, c.p_dexterity = self.p_strength, self.p_dexterity
        c.p_block, c.p_weak, c.p_frail = self.p_block, self.p_weak, self.p_frail
        c.p_hp, c.p_max_hp = self.p_hp, self.p_max_hp
        c.p_hp_start = self.p_hp_start
        c.hints = self.hints
        c.strike_count, c.rampage_count = self.strike_count, self.rampage_count
        c.tempo, c.extra_incoming, c.recoil = self.tempo, self.extra_incoming, self.recoil
        c.enemy_str_given = self.enemy_str_given
        c.wasted_attacks = self.wasted_attacks
        c.block_stripped = self.block_stripped
        c.vulnerable_applied = self.vulnerable_applied
        c.weak_applied = self.weak_applied
        c.overkill = self.overkill
        c.draw_bonus, c.avg_draw_value = self.draw_bonus, self.avg_draw_value
        c.corruption = self.corruption
        c.rage_block = self.rage_block
        c.barricade = self.barricade
        c.end_of_turn_damage = self.end_of_turn_damage
        c.alive_at_start = self.alive_at_start
        c._kill_saves = self._kill_saves
        c._hp_at_start = self._hp_at_start
        return c

    def alive_indices(self):
        return _alive_indices(self.monsters)

    def _is_card_playable(self, card):
        """카드의 사용 가능 여부. 손패 구성에 따라 조건이 바뀌는 카드는 라이브 재계산.
        (게임 state의 is_playable은 턴 시작 시점 고정이라, Defend를 먼저 낸 뒤
         손패가 전부 공격이 되어야 쓸 수 있는 격돌(Clash) 같은 카드를 못 잡는다.)"""
        if card.get('name', '') in ('Clash', 'Clash+'):
            return all(c.get('card_type') == 'Attack' for c in self.hand)
        return card.get('is_playable', False)

    def _effective_cost(self, card):
        """Corruption 발동 중이면 스킬은 0코.
        게임 상태의 실시간 cost 변화(포션 등)를 반영하기 위해 원본 hand에서 조회."""
        if self.corruption and card.get('card_type') == 'Skill':
            return 0
        # 카드의 인덱스로 원본 hand의 최신 cost를 가져오기
        try:
            idx = self.hand.index(card)
            if idx < len(self.hand_indices):
                orig_idx = self.hand_indices[idx]
                if orig_idx < len(self.original_hand):
                    return _card_cost(self.original_hand[orig_idx], self.energy)
        except (ValueError, IndexError):
            pass
        return _card_cost(card, self.energy)

    def playable_indices(self):
        return [i for i, c in enumerate(self.hand)
                if self._is_card_playable(c) and self.energy >= self._effective_cost(c)]

    # ── 카드 사용 ────────────────────────────────────────────────────
    def play(self, card_idx, target_idx):
        card = self.hand[card_idx]
        cost = self._effective_cost(card)
        self.energy -= cost
        ctype = card.get('card_type', 'Skill')
        name = card.get('name', '')

        # 1. 적 페해 트리거 (Enrage=스킬 사용 시, Curiosity=파워 사용 시 → 적 영구 힘 ↑)
        #    Gremlin Nob(Enrage) 상대로 스킬을 최소화해야 하는 이유.
        for m in self.monsters:
            if not _is_alive(m):
                continue
            gain = _power_amount(m, 'Enrage') if ctype == 'Skill' else \
                   _power_amount(m, 'Curiosity') if ctype == 'Power' else 0
            if gain:
                _add_power(m, 'Strength', gain)
                self.extra_incoming += gain * max(1, m.get('move_hits', 1))  # 이번 턴 데미지 ↑
                self.enemy_str_given += gain   # 영구 strength → 향후 여러 턴 비용 (score에서 큰 페널티)

        # 2. 데미지 (가변/특수 카드 모두 _base_damage가 통합 처리)
        base, hits = _base_damage(card, self, cost)
        if base > 0:
            dealt = self._deal(base, hits, card.get('is_aoe', False), target_idx)
            # Reaper: 가한 피해만큼 회복
            if name in ('Reaper', 'Reaper+'):
                self.p_hp = min(self.p_max_hp, self.p_hp + dealt)
        if name in ('Rampage', 'Rampage+'):
            self.rampage_count += 1
        # Rage 발동 중: 공격 카드를 낼 때마다 방어도 획득 (Rage를 먼저 써야 이후 공격들이 이득)
        if ctype == 'Attack' and self.rage_block:
            self.p_block += self.rage_block

        # 3. 방어 (Rage의 block 필드는 '공격당 방어'라 즉시 방어가 아님 → 제외)
        if card.get('block', 0) > 0 and name not in _RAGE_BLOCK:
            b = card.get('block', 0) + self.p_dexterity
            if self.p_frail > 0:
                b = int(b * 0.75)
            self.p_block += b
        # Rage 발동: 이후 공격마다 방어도 (즉시 방어 대신 누적 플래그)
        if name in _RAGE_BLOCK:
            self.rage_block += _RAGE_BLOCK[name]

        # 4. 효과 (parser가 잡은 디버프/자버프)
        self._apply_effects(card, target_idx)

        # 5. 특수 효과 (적 strength 감소 / 에너지 / 드로우 / 자해)
        loss = _STRENGTH_LOSS.get(name, 0)
        if loss and target_idx is not None and 0 <= target_idx < len(self.monsters):
            m = self.monsters[target_idx]
            if _is_alive(m) and "ATTACK" in m.get('intent', ''):
                self.extra_incoming -= loss * m.get('move_hits', 1)
        # 에너지 보충 (DFS가 보충 후 고코 카드 플레이를 보게 됨)
        self.energy += _ENERGY_GAIN.get(name, 0)
        if name in _DOUBLE_ENERGY:
            self.energy *= 2
        self.p_hp -= _SELF_HP_LOSS.get(name, 0)
        # 드로우 (기댓값 방식): 사용 시점에 1회 고정 적립.
        # "이 시점에 남은 에너지로 낼 수 있는 만큼"의 뽑은 카드만 가치 인정.
        # 고정값이라 이후 에너지를 써도 줄지 않음 → 에너지 비축 유인 없음 (공격 우선).
        drawn = card.get('draw', 0)
        if drawn:
            playable = min(drawn, max(0, self.energy))   # self.energy는 이 카드 비용 차감 후
            self.draw_bonus += playable * max(0, self.avg_draw_value - 1.5) * 0.5

        # 6. 파워카드 장기 가치 (base_value를 tempo로 누적)
        if ctype == 'Power':
            self.tempo += card.get('base_value', 0)
        # Corruption 발동 → 이후 스킬 0코 (DFS가 "Corruption → 스킬 공짜 연계"를 보게 됨)
        if name in ('Corruption', 'Corruption+'):
            self.corruption = True
        # Barricade 발동 → 방어도 영구 보존. 전략적 가치(향후 방어 누적)를 tempo로 부여하고,
        # 이후 쌓는 방어도가 score에서 지속 가치를 받게 한다.
        if name in ('Barricade', 'Barricade+'):
            self.barricade = True
            self.tempo += 16
        # Combust 발동 → 턴 종료 시 모든 적에게 데미지 (+ HP 1 손실)
        if name in ('Combust', 'Combust+'):
            amount = 5 if name == 'Combust' else 7
            self.end_of_turn_damage += amount
            self.p_hp -= 1
        # Infernal Blade 발동 → 공격 카드가 손패에 추가되고 0 코스트
        # (정확한 카드는 random이지만, 평균적으로 기본 공격 카드 정도 가정)
        if name in ('Infernal Blade', 'Infernal Blade+'):
            # 손패에 추가될 "free attack card" 객체 생성
            # 실제 게임에서 random이지만 DFS에서는 대표적 공격(Strike 정도) 가정
            free_attack = {
                'name': 'Infernal Add',
                'card_type': 'Attack',
                'cost': '0',  # 이번 턴만 0코
                'is_playable': True,
                'damage': 6,  # Strike 정도의 평균 데미지
                'hits': 1,
                'block': 0,
                'draw': 0,
                'base_value': 6.0,
                'effects': {},
            }
            self.hand.append(free_attack)
            self.hand_indices.append(len(self.hand) - 1)  # 추가 카드의 인덱스

        self.hand.pop(card_idx)
        self.hand_indices.pop(card_idx)

    def _deal(self, base, hits, is_aoe, target_idx):
        """base(hit당 데미지)를 hits회 적용. Weak는 여기서 일괄(-25%), Vuln은 타겟별(+50%).
        return: 적에게 실제로 들어간 총 HP 데미지 (Reaper 회복용)."""
        if self.p_weak > 0:
            base = int(base * 0.75)
        targets = self.alive_indices() if is_aoe else \
                  ([target_idx] if target_idx is not None and 0 <= target_idx < len(self.monsters) else [])
        hp_damage_dealt = 0
        block_stripped = 0          # 방어막 보존 적에게서 깎은 방어막 (가치 있음)
        wasted_on_reset_block = True  # [중요] 기본: 낭비라고 가정 → 타겟 중 하나라도 의미 있으면 False로 전환
        for tgt in targets:
            m = self.monsters[tgt]
            if not _is_alive(m):
                continue
            # 방어막이 다음 턴까지 보존되는 적인가 = Barricade.
            # (Plated Armor/Metallicize는 매 턴 방어막을 '다시' 주지만 방어막 자체는
            #  턴 종료 시 reset되므로, 이번 턴 깎아도 다음 턴 새로 생긴다 → 깎기 무의미.
            #  Plated Armor의 약점은 '막히지 않은 HP 데미지'인데 그건 이미 HP데미지로 평가됨.)
            # [예외] Spheric Guardian은 Barricade로 방어막이 영구 보존되는 유일한 적인데,
            #  실제 게임 state가 'Barricade' 파워를 노출하지 않는 경우가 있어 이름으로도 직접 판정.
            #  → 방어막을 다 못 깎아도 깎는 만큼 다음 턴 이득 = 공격이 낭비가 아님.
            keeps_block = _power_amount(m, 'Barricade') > 0 or 'Spheric Guardian' in m.get('name', '')
            self.recoil += _power_amount(m, 'Sharp Hide')
            vuln = _power_amount(m, 'Vulnerable') > 0
            intangible = _power_amount(m, 'Intangible') > 0
            per_hit = 1 if intangible else (int(base * 1.5) if vuln else base)
            tgt_hp_dmg = 0
            for _ in range(hits):
                if not _is_alive(m):
                    break
                self.recoil += _power_amount(m, 'Thorns')
                mb = m['block']
                if per_hit <= mb:
                    m['block'] = mb - per_hit
                    if keeps_block:
                        block_stripped += per_hit   # 보존 방어막을 깎음 = 다음 턴 이득
                else:
                    m['block'] = 0
                    if keeps_block:
                        block_stripped += mb
                    hp_dmg_this = per_hit - mb
                    # 처치에 필요 이상으로 들어간 데미지 = 오버킬(낭비). Barricade 적은 방어막
                    # 자체가 자산이라 제외 (block_stripped로 이미 평가).
                    if not keeps_block and hp_dmg_this > m['current_hp']:
                        self.overkill += hp_dmg_this - m['current_hp']
                    tgt_hp_dmg += hp_dmg_this
                    m['current_hp'] = max(0, m['current_hp'] - hp_dmg_this)
                if not m.get('_curl_used') and _is_alive(m):
                    cu = _power_amount(m, 'Curl Up')
                    if cu:
                        m['block'] += cu
                        m['_curl_used'] = True
                if _is_alive(m):
                    mall = _power_amount(m, 'Malleable')
                    if mall:
                        m['block'] += mall + m['_malleable_stacks']
                        m['_malleable_stacks'] += 1
            hp_damage_dealt += tgt_hp_dmg
            # [핵심] 이 타겟에 대해 HP 데미지 있거나 Barricade 있으면 → 공격 의미 있음 (낭비 아님)
            if tgt_hp_dmg > 0 or keeps_block:
                wasted_on_reset_block = False

        if wasted_on_reset_block:
            self.wasted_attacks += 1
        # 방어막 보존 적의 방어막을 깎은 만큼 작은 가치 (다음 턴 데미지로 이어짐)
        self.block_stripped += block_stripped
        return hp_damage_dealt

    def _apply_effects(self, card, target_idx):
        eff = card.get('effects', {})
        name = card.get('name', '')

        # Spot Weakness: 공격 의도가 있는 적에게만 효과 발동
        if name in ('Spot Weakness', 'Spot Weakness+'):
            if target_idx is None or target_idx < 0 or target_idx >= len(self.monsters):
                return  # 타겟이 없으면 효과 없음
            m = self.monsters[target_idx]
            if "ATTACK" not in m.get('intent', ''):
                return  # 공격 의도가 없으면 효과 없음

        # Shockwave 같은 AOE 디버프: 모든 살아있는 적에게 적용
        is_aoe = card.get('is_aoe', False)
        targets_to_apply = []
        if is_aoe:
            targets_to_apply = [i for i, m in enumerate(self.monsters) if _is_alive(m)]
        elif target_idx is not None and 0 <= target_idx < len(self.monsters):
            targets_to_apply = [target_idx]

        for tgt in targets_to_apply:
            m = self.monsters[tgt]
            if _is_alive(m):
                for k in ('vulnerable', 'weak', 'frail'):
                    if k in eff:
                        amount = eff[k]
                        _add_power(m, k.capitalize(), amount)
                        if k == 'weak':
                            m['_weak_applied'] = True
                            self.weak_applied += amount  # 추적
                        elif k == 'vulnerable':
                            self.vulnerable_applied += amount  # 추적

        self.p_strength += eff.get('strength', 0) + eff.get('strength_temp', 0)
        self.p_dexterity += eff.get('dexterity', 0) + eff.get('dexterity_temp', 0)

    # ── 평가 ─────────────────────────────────────────────────────────
    def score(self):
        # 턴 종료 시 Combust 등의 자동 데미지를 '유효 HP'로만 반영 (self.monsters는 절대 변경 금지).
        # [중요] 예전엔 여기서 current_hp를 직접 깎았는데, score()가 DFS 노드마다 호출되고
        #        clone이 깎인 HP를 복사해 깊이만큼 누적 → 가짜 lethal 발생. 그래서 순수 평가로 전환.
        eot = self.end_of_turn_damage

        def _eff_hp(m):
            return max(0, m['current_hp'] - eot) if eot > 0 else m['current_hp']

        def _alive_eot(m):
            return not m.get('is_gone') and not m.get('half_dead') and _eff_hp(m) > 0

        alive = [m for m in self.monsters if _alive_eot(m)]
        s = 0

        if not alive:
            s += 100_000   # Lethal

        # 태도별 공격/처치 가치 배수 (BURST/RACE는 공격 우선, TURTLE은 중립)
        _, atk_mult = _ATTITUDE_TUNING.get(self.hints.get('strategy', 'NORMAL'), (1.0, 1.0))

        # 적 HP 감소 (공격 가치) — eot 반영 유효 HP 기준
        # Spheric Guardian: 방어도가 누적되므로 어떻게든 공격하도록 점수 높게
        for m in self.monsters:
            hp_dmg = m['max_hp'] - _eff_hp(m)
            is_spheric_guardian = 'Spheric Guardian' in m.get('name', '')
            weight = 5 if is_spheric_guardian else 3  # Spheric Guardian은 최우선
            s += hp_dmg * weight * atk_mult

        # 처치 보너스 = 죽여서 막는 미래 위협 (스케일링 적일수록 큼)
        # [Darkling 예외] Life Link: 하나라도 살아있으면 죽은 개체는 다다음 턴 절반 HP로 부활.
        #   전부 동시에 죽여야 영구 처치 → 단독/부분 처치는 일반 보너스를 주지 않고 별도 처리.
        for i in self.alive_at_start:
            if 'Darkling' in self.monsters[i].get('name', ''):
                continue   # Darkling은 아래에서 동시처치 기준으로 별도 평가
            if not _alive_eot(self.monsters[i]):
                s += int((_KILL_FLAT_BONUS + self._kill_saves[i] * 4) * atk_mult)

        # Darkling 동시처치 평가 (부활 메커닉 반영)
        dk_idxs = [i for i in self.alive_at_start
                   if 'Darkling' in self.monsters[i].get('name', '')]
        if dk_idxs:
            dk_killed = sum(1 for i in dk_idxs if not _alive_eot(self.monsters[i]))
            if dk_killed == len(dk_idxs):
                # 살아있던 Darkling 전부 동시 처치 = 부활 없음(영구) → 일반 처치급 보너스.
                # (이들이 전체 적이면 위에서 이미 lethal +100000도 발동)
                s += int(sum(_KILL_FLAT_BONUS + self._kill_saves[i] * 4 for i in dk_idxs) * atk_mult)
            elif dk_killed >= 2:
                # 2명 이상 동시 처치: 부활하더라도 2턴간 압박↓ + 마무리 셋업 → superlinear 보너스.
                # (많이 한꺼번에 죽일수록 제곱으로 가중 → 3명>2명)
                s += int(dk_killed * dk_killed * 100 * atk_mult)
            # dk_killed == 1 (나머지 생존): 곧 부활 → 처치 보너스 없음.
            #   → 단독 처치를 탐내지 않고 HP를 고르게 낮추도록 유도 (HP감소·incoming감소로만 평가).

        # 생존: 용인치 밴드 (HP 비율 + 미래 위협으로 조정)
        net = max(0, _incoming_now(self.monsters) + self.extra_incoming - self.p_block)
        tol = self._block_tolerance()
        if net <= tol:
            s -= net * 1                         # 용인 범위: 가볍게
        else:
            s -= tol * 1 + (net - tol) * 6        # 초과: 무겁게 (최대한 막아라)
        hp_after = self.p_hp - net
        if hp_after <= 0:
            s -= 50_000
        elif hp_after < self.p_max_hp * 0.15:
            s -= (self.p_max_hp * 0.15 - hp_after) ** 2 * 2

        # 회복 가치 (Reaper/Feed 등): 이번 턴 순회복량.
        # AOE 회복(사신)은 적이 많을수록 unblocked 데미지 합이 커져 회복량↑ → 자연히 적 수에 비례.
        # 체력이 낮을수록 1HP의 가치가 커진다 (만피 근처면 cap으로 healed≈0이라 보너스도 0).
        healed = max(0, self.p_hp - self.p_hp_start)
        if healed > 0:
            heal_weight = 2.0 + 3.0 * (1.0 - self.p_hp / self.p_max_hp)  # 만피~2.0, 저체력~5.0
            s += healed * heal_weight

        # 내 영구 버프
        s += self.p_strength * 25 + self.p_dexterity * 18

        # 파워카드 장기 가치 (위협 없을수록 가중)
        safe = 1.0 if net <= tol else 0.4
        s += self.tempo * 0.6 * safe

        # Barricade 발동 중: 쌓은 방어도가 다음 턴까지 보존 → 막은 양을 넘는 방어도도 가치.
        # (Barricade 없으면 overblock은 무의미하지만, 있으면 영구히 쌓이는 자산)
        if self.barricade:
            s += self.p_block * 2

        # LLM 힌트: priority_target 처치/타격 보너스
        pt = self.hints.get('priority_target')
        if pt is not None and 0 <= pt < len(self.monsters):
            tm = self.monsters[pt]
            if not _is_alive(tm):
                s += 300
            else:
                s += (tm['max_hp'] - tm['current_hp']) * 2

        # LLM 힌트: hp_stops (Split 등) — "임계를 넘을 거면 한 방에 처치(버스트)" 권장.
        # 단, 페널티로 임계 통과를 '막으면' 분열이 불가피한 적(슬라임보스 등)에서
        # 영원히 임계 직전에 멈추는 데드락이 생긴다. 그래서:
        #   - 임계 밑에서 '죽이면' 보너스 (버스트 유도)
        #   - 임계 밑인데 '살아있으면' 페널티 없음 (데드락 방지 — 분열은 어차피 일어남)
        for stop in self.hints.get('hp_stops', []):
            i = stop.get('enemy')
            if i is not None and 0 <= i < len(self.monsters):
                m = self.monsters[i]
                thresh = m['max_hp'] * stop.get('ratio', 0.5)
                if not _is_alive(m) and self._hp_at_start.get(i, 0) > thresh:
                    s += 250   # 임계를 넘겨 한 턴에 처치 = 버스트 성공

        s += self.draw_bonus          # 드로우 기댓값 (사용 시점 고정 적립)

        # 디버프 가치: Vulnerable과 Weak의 효과 계산
        # Vulnerable: 50% 추가 데미지 → 평균 3 damage × 1.5 = 1.5 extra per hit
        # Weak: -25% 데미지 → 적 공격이 줄어듦 → 미래 턴에서 방어 덜 필요
        vuln_value = self.vulnerable_applied * 2.0  # 각 vulnerable당 약 2점
        weak_value = self.weak_applied * 1.5        # 각 weak당 약 1.5점 (차후 방어 절감)
        s += vuln_value + weak_value

        s -= self.recoil * 4          # 반동 페널티
        s -= self.wasted_attacks * 8  # 방어막에 막힌 무의미한 공격 회피 (reset 적 한정)
        # 오버킬 페널티: 처치에 필요 이상으로 쏟은 데미지는 낭비 (다른 적/다음 턴에 썼어야).
        #   → 딸피 적에게 강한 카드를 낭비하지 않게. 단 전멸(lethal) 시엔 남는 적이 없어 낭비 아님.
        if alive:
            s -= self.overkill * _OVERKILL_RATE
        # 적에게 준 영구 strength(Enrage/Curiosity): 향후 여러 턴 데미지로 이어지므로 큰 페널티.
        # → Gremlin Nob 상대로 불필요한 스킬/파워를 피하게 됨 (생존이 더 급하면 가드가 우선).
        s -= self.enemy_str_given * 8
        # 방어막 보존 적(Barricade/Plated Armor 등)의 방어막을 깎으면 다음 턴 데미지로
        # 이어지므로 작은 가치 부여 (HP 데미지 ×3보다 작게 — 직접 딜이 여전히 우선)
        # Spheric Guardian: 방어도가 영구 누적되므로 더 큰 가치
        has_spheric_guardian = any(
            'Spheric Guardian' in m.get('name', '') and _is_alive(m)
            for m in self.monsters
        )
        block_strip_value = 2.0 if has_spheric_guardian else 1.0
        s += self.block_stripped * block_strip_value

        # 자원 보존 보너스 (작게): 같은 결과면 에너지/카드를 덜 쓰는 쪽을 선호.
        #   - 죽일 수 있는 적은 최소 비용으로 처치 (HP3에 Bash 대신 Strike)
        #   - 공격 의도 없는 적에게 쓸모없는 방어/카드 낭비 안 함
        # 값이 작아 유용한 플레이(공격 +HP데미지 등)는 절대 막지 않는다.
        s += self.energy * 2          # 남은 에너지 보존
        s += len(self.hand) * 1       # 남은 손패 보존

        # 조건부 카드 페널티: 조건을 만족하지 못하면 사용한 의미가 없음
        # Spot Weakness (공격 의도 필요)
        for card in [c for c in self.hand if c.get('name') in ('Spot Weakness', 'Spot Weakness+')]:
            has_attack_intent = any("ATTACK" in m.get('intent', '') for m in self.monsters if _is_alive(m))
            if not has_attack_intent:
                s -= card.get('cost', 1) * 10  # 비용을 아까운 방식으로 낭비

        return s

    def _block_tolerance(self):
        ratio = self.p_hp / self.p_max_hp
        tol = self.p_hp * (0.15 if ratio > 0.6 else 0.10 if ratio > 0.4 else 0.05)
        # 다음 턴 위협이 크면 용인치 축소 (자원 보존)
        future = sum(_projected_attack(m, 1) for m in self.monsters if _is_alive(m))
        if future > self.p_hp * 0.4:
            tol *= 0.5
        # 태도별 용인치 보정 (BURST=피해감수↑, TURTLE/RACE=방어↑)
        tol_mult, _ = _ATTITUDE_TUNING.get(self.hints.get('strategy', 'NORMAL'), (1.0, 1.0))
        tol *= tol_mult
        return tol


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ DFS 탐색                                                              ║
# ╚══════════════════════════════════════════════════════════════════════╝

def _play_priority(card):
    """카드 사용 우선순위(클수록 먼저). 점수 동점일 때 '올바른 순서'를 고르는 타이브레이커.
    디버프/버프 부여 카드와 큰 카드를 먼저 → 취약/힘 셋업을 공격보다 앞에,
    큰 공격을 방어막 앞에. 점수가 같은 경우에만 작동하므로 최적 시퀀스를 해치지 않는다."""
    eff = card.get('effects', {})
    p = 0
    if eff:                       # 디버프/자버프 부여 → 공격보다 먼저
        p += 100
    p += card.get('damage', 0) + card.get('block', 0)   # 큰 카드 먼저
    p += card.get('draw', 0) * 5  # 드로우는 일찍 (더 많은 선택지)
    return p


def search_best_action(sim, depth=6):
    """모든 카드 순서를 탐색해 최적의 첫 행동 결정. return (score, (card_idx, target) or None)."""
    best_score, best_priority, best_action = sim.score(), -float('inf'), None
    if depth == 0:
        return best_score, None

    playable = sim.playable_indices()
    if not playable:
        return best_score, None
    # 우선순위 높은 카드부터 탐색 → 점수 동점이면 우선순위 적용 = 올바른 순서
    playable.sort(key=lambda i: -_play_priority(sim.hand[i]))
    alive = sim.alive_indices()

    for ci in playable:
        card = sim.hand[ci]
        targets = [None] if (card.get('is_aoe') or not card.get('has_target')) else (alive or [None])
        for tgt in targets:
            child = sim.clone()
            child.play(ci, tgt)
            sc, _ = search_best_action(child, depth - 1)
            # [중요] 점수 비교 + 동점일 때 우선순위 적용 (tuple 비교)
            current_priority = _play_priority(card)
            if (sc, current_priority) > (best_score, best_priority):
                best_score = sc
                best_priority = current_priority
                best_action = (ci, tgt)
    return best_score, best_action


def _simulate_turn_outcome(sim, first_action):
    """첫 행동부터 시작해 시뮬의 최선 수를 끝까지 따라가 이번 턴 종료 상태를 반환.
    포션이 "정말 필요한지" 판단용. first_action은 battle_module이 이미 구한 값을 재활용.
    return (best_score, hp_after_turn, can_lethal)."""
    cur, action = sim, first_action
    while action is not None:
        cur = cur.clone()
        cur.play(action[0], action[1])
        _, action = search_best_action(cur)

    can_lethal = not cur.alive_indices()
    net = max(0, _incoming_now(cur.monsters) + cur.extra_incoming - cur.p_block)
    hp_after = cur.p_hp - (0 if can_lethal else net)
    return cur.score(), hp_after, can_lethal


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ LLM 힌트 — logic_notes 프로즈 → 구조화 제약 (전투당 1회 캐시)        ║
# ╚══════════════════════════════════════════════════════════════════════╝

_HINT_CACHE = {}

_HINT_SCHEMA = """{
    "priority_target": <enemy index to focus, or null>,
    "hp_stops": [{"enemy": <index>, "ratio": <0.0-1.0>}],
    "strategy": "BURST" | "TURTLE" | "RACE" | "NORMAL"
}"""


def _combat_hints(monsters):
    """logic_notes 있는 적이 있으면 LLM으로 구조화 힌트 1회 생성, 전투당 캐시."""
    alive = [m for m in monsters if _is_alive(m)]
    noted = [(i, m) for i, m in enumerate(monsters)
             if _is_alive(m) and (_monster_db(m['name']) or {}).get('logic_notes')]
    if not noted:
        return {}

    key = frozenset(m['name'] for m in alive)
    if key in _HINT_CACHE:
        return _HINT_CACHE[key]

    enemy_lines = []
    for i, m in noted:
        info = _monster_db(m['name'])
        enemy_lines.append(
            f"[{i}] {m['name']} HP:{m['current_hp']}/{m['max_hp']} "
            f"type:{info.get('type', '?')} notes: {info.get('logic_notes', '')}"
        )

    prompt = f"""You are a Slay the Spire tactical analyst. Read enemy notes and output combat constraints.

[Enemies]
{chr(10).join(enemy_lines)}

[Rules]
- priority_target: index of the enemy to kill FIRST (boss/leader, or a minion powering an engine). null if none stands out.
- hp_stops: enemies that do something bad at an HP threshold (e.g. "Split at 50%"). List {{enemy, ratio}}. Empty if none.
- strategy: BURST (kill fast before a big attack/scaling), TURTLE (survive a known nuke), RACE (enemy scales infinitely), NORMAL.

Output EXACTLY this JSON, nothing else:
{_HINT_SCHEMA}"""

    try:
        resp = ollama.chat(
            model=MODEL_NAME,
            messages=[{'role': 'user', 'content': prompt}],
            options={'temperature': 0.0, 'num_predict': 150},
        )
        m = re.search(r'\{.*\}', resp['message']['content'], re.DOTALL)
        hints = json.loads(m.group(0)) if m else {}
    except Exception as e:
        log.warning(f"combat hint LLM 실패: {e}")
        hints = {}

    _HINT_CACHE[key] = hints
    combat_log.info(f"combat hints {list(key)}: {hints}")
    return hints


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ 고위 전략가 LLM — 기믹/모호한 전투에서 플레이어 상태 고려해 태도 결정 ║
# ╚══════════════════════════════════════════════════════════════════════╝
# LLM은 카드 플레이어가 아니라 '고위 전략가'. 태도(ATTITUDE)만 정하면
# 결정적 카드 수학 엔진(score/DFS)이 그 태도에 맞춰 실행한다.
# (names, HP 구간)으로 캐시 → 매 턴 호출 방지, 단 HP가 크게 변하면 재판단.

_STRAT_CACHE = {}


def _llm_strategist(monsters, player, base_attitude):
    """Class 4(기믹) 등 모호한 전투에서 플레이어 상태를 고려해 태도를 정한다.
    return {'attitude': str, 'priority_target': int|None}."""
    alive = [(i, m) for i, m in enumerate(monsters) if _is_alive(m)]
    if not alive:
        return {'attitude': base_attitude}
    hp = player.get('current_hp', 0)
    max_hp = player.get('max_hp', 1) or 1
    hp_bucket = int(hp / max_hp * 4)   # 0~4 — HP 변화 반영하되 호출 횟수 제한
    key = (frozenset(m['name'] for _, m in alive), hp_bucket)
    if key in _STRAT_CACHE:
        return _STRAT_CACHE[key]

    enemy_lines = []
    for i, m in alive:
        info = _monster_db(m['name']) or {}
        enemy_lines.append(
            f"[{i}] {m['name']} HP:{m['current_hp']}/{m['max_hp']} "
            f"class:{_monster_class(m)} notes:{(info.get('logic_notes', '') or '')[:140]}"
        )

    prompt = f"""You are a high-level Slay the Spire strategist — NOT the card player.
Decide ONLY the ATTITUDE for this fight given the player's CURRENT state.
A deterministic card-math engine will execute whatever attitude you choose.

[Player] HP: {hp}/{max_hp} ({int(hp / max_hp * 100)}%)
[Enemies]
{chr(10).join(enemy_lines)}

[Attitudes]
- BURST: accept some damage to kill fast (scalers, or before a big incoming hit).
- TURTLE: defend hard, take minimal damage (predictable foes; when HP is low).
- RACE: preserve HP AND kill fast at once (high-threat elites/bosses; spend resources).
- GIMMICK: the gimmick (split / revive / timer / charge) is paramount — neutral defense, follow the gimmick.

Choose by THIS player's HP and the enemy gimmick:
- If HP is LOW, lean defensive (TURTLE) even against a scaler.
- If HP is healthy and delay is punished, lean aggressive (BURST/RACE).
- If a special gimmick dominates, pick GIMMICK.

Output EXACTLY this JSON, nothing else:
{{"attitude": "BURST|TURTLE|RACE|GIMMICK", "priority_target": <enemy index or null>, "reasoning": "<one sentence>"}}"""

    try:
        resp = ollama.chat(
            model=MODEL_NAME,
            messages=[{'role': 'user', 'content': prompt}],
            options={'temperature': 0.0, 'num_predict': 120},
        )
        m = re.search(r'\{.*\}', resp['message']['content'], re.DOTALL)
        out = json.loads(m.group(0)) if m else {'attitude': base_attitude}
    except Exception as e:
        log.warning(f"strategist LLM 실패: {e}")
        out = {'attitude': base_attitude}

    if out.get('attitude') not in _ATTITUDE_TUNING:
        out['attitude'] = base_attitude
    _STRAT_CACHE[key] = out
    combat_log.info(f"strategist {[m['name'] for _, m in alive]} hp%={int(hp / max_hp * 100)}: {out}")
    return out


def _decide_attitude(monsters, player, classes, base, sim_outcome):
    """3단계 전략 결정 → (attitude, priority_target_override).
    Stage 1: 결정적 필터 (킬각/죽을위기) → LLM 패스.
    Stage 1.5: 명확한 클래스(기믹 없음) → 결정적 base 태도.
    Stage 2: 기믹(Class 4) 전투 → LLM 전략가가 플레이어 상태 보고 태도 결정."""
    _, hp_after, can_lethal = sim_outcome
    hp = player.get('current_hp', 0)
    max_hp = player.get('max_hp', 1) or 1

    # ── Stage 1: 결정적 필터 ──
    if can_lethal:
        return 'BURST', None                      # 킬각 → 다 쏟아붓기
    if hp_after <= 0 or hp / max_hp < 0.2:
        return 'TURTLE', None                      # 죽을 위기 → 무조건 방어

    # ── Stage 1.5: 기믹 없는 명확한 클래스 → 결정적 ──
    if 4 not in classes:
        return base, None

    # ── Stage 2: 기믹 전투 → LLM 전략가 (플레이어 상태 고려) ──
    out = _llm_strategist(monsters, player, base)
    return out.get('attitude', base), out.get('priority_target')


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ 포션 — 적재적소 사용 (시뮬로 카드만으로 부족할 때만, 상황별 종류 매칭)║
# ╚══════════════════════════════════════════════════════════════════════╝

# 포션을 효과 유형으로 분류 (itemDB 설명 기반)
# DEFENSE: 생존(방어/회복/회피)  DAMAGE: 처치  DEBUFF: 적 약화
# ── 포션 분류: itemDB description을 키워드 스캔해 모든 포션을 자동 분류 ──
# DEFENSE(생존)  DAMAGE(처치/딜)  DEBUFF(적 약화)  UTILITY(카드 이득/기타)
# 분류 안 된 것도 위기 시 폴백으로 사용되지만, description 기반이라 거의 모두 잡힌다.

# 키워드 → 카테고리 (위에서부터 우선 매칭)
_POTION_RULES = [
    ('DEBUFF',  ('apply', 'vulnerable', 'weak', 'poison')),  # 적에게 디버프
    ('DEFENSE', ('block', 'heal', 'regen', 'plated armor', 'intangible',
                 'metallicize', 'artifact', 'would die', 'escape',
                 'max hp', 'dexterity', 'thorns')),           # 생존/방어
    ('DAMAGE',  ('damage', 'strength', 'energy', 'shiv',
                 'play the top', 'played twice')),            # 딜/공격 자원
    ('UTILITY', ('draw', 'add ', 'upgrade', 'discard', 'return',
                 'ritual', 'miracle', 'exhaust')),            # 카드 이득
]
# IronClad과 무관해 전투에서 의미 없는 포션 (Defect/Watcher 전용) → 미분류 유지
_POTION_IRRELEVANT = {'Focus Potion', 'Potion Of Capacity', 'Essence Of Darkness',
                      'Stance Potion', 'Ambrosia', 'Entropic Brew'}


def _classify_potions():
    """itemDB 포션 description을 읽어 카테고리별 이름 집합을 만든다."""
    cats = {'DEFENSE': set(), 'DAMAGE': set(), 'DEBUFF': set(), 'UTILITY': set()}
    try:
        with open(os.path.join(LOCAL_PATH, "db", "itemDB.json"), "r", encoding="utf-8") as f:
            potions = json.load(f).get('potions', [])
    except FileNotFoundError:
        return cats
    for p in potions:
        name, desc = p.get('name', ''), p.get('description', '').lower()
        if name in _POTION_IRRELEVANT:
            continue
        for cat, keywords in _POTION_RULES:
            if any(k in desc for k in keywords):
                cats[cat].add(name)
                break
    return cats


_POT_CATS = _classify_potions()
_POTION_DEFENSE = _POT_CATS['DEFENSE']
_POTION_DAMAGE = _POT_CATS['DAMAGE']
_POTION_DEBUFF = _POT_CATS['DEBUFF']
_POTION_UTILITY = _POT_CATS['UTILITY']


def _is_tough(alive):
    for m in alive:
        if (_monster_db(m['name']) or {}).get('type') in ('Elite', 'Boss'):
            return True
    return sum(m['current_hp'] for m in alive) >= 120 or max(m['max_hp'] for m in alive) >= 100


def _find_potion(potions, *name_sets, fallback_any=False):
    """우선순위 순으로 name_set들을 훑어 첫 매칭 포션 인덱스 반환. (인덱스 0 안전).
    fallback_any=True면 분류 매칭 실패 시 사용 가능한 아무 포션이나 반환
    (죽을 위기엔 분류 안 된 포션이라도 쓰는 게 죽는 것보다 낫다)."""
    for name_set in name_sets:
        for i, p in enumerate(potions):
            if p.get('id', 'Potion Slot') != 'Potion Slot' and p.get('can_use') \
               and p.get('name') in name_set:
                return i
    if fallback_any:
        for i, p in enumerate(potions):
            if p.get('id', 'Potion Slot') != 'Potion Slot' and p.get('can_use'):
                return i
    return None


def choose_potion(potions, player, monsters, sim_result):
    """카드만으로 부족할 때만 포션. 상황(위기/마무리)에 맞는 종류를 골라 사용.

    sim_result = (best_score, hp_after, can_lethal): 시뮬레이션이 카드만으로 낸 최선.
      - 카드만으로 안전하게 끝남 → 포션 불필요 (보존)
      - 카드로도 죽을 위기 → 생존/데미지 포션, 분류 안 됐으면 아무거나라도
      - 강적 마무리 직전인데 카드론 부족 → 데미지 포션
    """
    if not any(p.get('id', 'Potion Slot') != 'Potion Slot' and p.get('can_use') for p in potions):
        return None

    alive = [m for m in monsters if _is_alive(m)]
    if not alive:
        return None
    hp, max_hp = player.get('current_hp', 0), player.get('max_hp', 1)
    tough = _is_tough(alive)
    best_score, hp_after, can_lethal = sim_result

    # 카드만으로 이번 턴 전멸 가능 → 포션 절대 불필요
    if can_lethal:
        return None

    # 각 상황을 우선순위대로 시도. 매칭 포션을 '찾았을 때만' 반환하고,
    # 없으면 다음 상황으로 넘어간다 (강적전 손패 빈약 → UTILITY 같은 후순위까지 도달).
    hp_loss = max(0, hp - hp_after)
    danger = hp_after < max_hp * 0.25 or best_score < -10_000
    finish = tough and len(alive) == 1 and alive[0]['current_hp'] <= 60
    threatened = tough and hp / max_hp < 0.4 and hp_after < hp * 0.6

    candidates = []
    if danger:
        # 죽을 위기: 생존 → 데미지(빨리 죽여 위협 제거) → 약화, 그래도 없으면 아무거나라도
        candidates.append(((_POTION_DEFENSE, _POTION_DAMAGE, _POTION_DEBUFF), True))
    if hp_loss >= max_hp * 0.35:
        candidates.append(((_POTION_DEFENSE,), False))   # 큰 공격 예고 → 선제 방어
    if finish:
        candidates.append(((_POTION_DAMAGE, _POTION_DEBUFF), False))  # 강적 마무리 → 딜
    if threatened:
        candidates.append(((_POTION_DEFENSE, _POTION_DAMAGE), False))
    if tough and player.get('_hand_starved'):
        candidates.append(((_POTION_UTILITY,), False))   # 강적전 손패 빈약 → 카드 확보

    for sets, fb in candidates:
        idx = _find_potion(potions, *sets, fallback_any=fb)
        if idx is not None:
            return idx

    # 그 외(약적/여유) → 보존
    return None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ 메인 핸들러                                                            ║
# ╚══════════════════════════════════════════════════════════════════════╝

def battle_module(state, avail):
    combat = state.get('combat_state', {})
    player = combat.get('player', {})
    hand = enrich_hand(combat.get('hand', []))
    monsters = combat.get('monsters', [])
    energy = player.get('energy', 0)
    potions = state.get('potions', [])

    if "play" not in avail:
        if "end" in avail:
            print("end", flush=True)
            return -1
        print("wait", flush=True)
        return 0

    # 1. 기믹 힌트 (logic_notes 기반 hp_stops/priority_target, 전투당 1회 캐시)
    gimmick_hints = _combat_hints(monsters)

    # 2. 완벽한 타격용: 덱 전체(손패+뽑을더미+버린더미+소멸)의 'Strike' 이름 카드 수
    strike_count = sum(
        1 for pile in ('hand', 'draw_pile', 'discard_pile', 'exhaust_pile')
        for c in combat.get(pile, [])
        if 'Strike' in c.get('name', '')
    )

    # 3. 드로우 기댓값용: 앞으로 뽑힐 카드(뽑을더미+버린더미)의 평균 base_value
    avg_draw_value = _avg_pile_value(combat.get('draw_pile', []) + combat.get('discard_pile', []))

    # 4. ── 몬스터별 전략 (3단계 파이프라인) ──
    #    Stage 1.5(결정적): 클래스 기반 기본 태도로 먼저 시뮬.
    classes = {_monster_class(m) for m in monsters if _is_alive(m)}
    base_attitude = _fight_attitude(monsters)
    hints = dict(gimmick_hints)
    hints['strategy'] = base_attitude

    sim = SimState(hand, energy, monsters, player, hints, strike_count, avg_draw_value)
    best_score, action = search_best_action(sim)
    sim_outcome = _simulate_turn_outcome(sim, action)

    # Stage 1(결정적 필터: 킬각/죽을위기) + Stage 2(기믹 → LLM 전략가).
    can_lethal = sim_outcome[2]
    final_attitude, pt_override = _decide_attitude(monsters, player, classes, base_attitude, sim_outcome)
    # 킬각이면 어떤 태도든 lethal 라인(100000)이 지배 → 재시뮬 불필요.
    if not can_lethal and (final_attitude != hints['strategy'] or pt_override is not None):
        # 태도/우선타겟이 바뀌면 그 태도로 재시뮬 (결정적 실행)
        hints['strategy'] = final_attitude
        if pt_override is not None:
            hints['priority_target'] = pt_override
        sim = SimState(hand, energy, monsters, player, hints, strike_count, avg_draw_value)
        best_score, action = search_best_action(sim)
        sim_outcome = _simulate_turn_outcome(sim, action)
    combat_log.info(f"attitude: base={base_attitude} final={final_attitude} classes={sorted(classes)}")

    # 5. 포션 판단 — "카드만으로 부족할 때만" 적재적소 사용.
    # 손패 빈약 여부: 낼 카드가 1장 이하 & 에너지가 남음 → UTILITY 포션(드로우/카드생성) 가치
    player['_hand_starved'] = len(sim.playable_indices()) <= 1 and energy >= 2
    pi = choose_potion(potions, player, monsters, sim_outcome)
    if pi is not None:
        p = potions[pi]
        alive = _alive_indices(monsters)
        cmd = f"potion use {pi} {alive[0]}" if (p.get('requires_target') and alive) else f"potion use {pi}"
        log.info(f"🧪 포션: {p.get('name')} (카드만으론 부족)")
        print(cmd, flush=True)
        return 0

    if action is None:
        log.info(f"💤 더 나은 수 없음 → 턴 종료 (score={best_score})")
        print("end", flush=True)
        return -1

    ci, tgt = action
    card = hand[ci]
    if card.get('has_target') and tgt is not None:
        resolved = _resolve_target(tgt, monsters)
        cmd = f"play {ci+1} {resolved}" if resolved is not None else f"play {ci+1}"
    else:
        cmd = f"play {ci+1}"

    log.info(f"⚔️ Sim: [{ci}] {card.get('name')} → {tgt} | score={best_score}")
    combat_log.info(f"hand={[c.get('name') for c in hand]} energy={energy} "
                    f"incoming={_incoming_now(monsters)} chose={card.get('name')} score={best_score}")
    print(cmd, flush=True)
    return 0


def _resolve_target(target_idx, monsters):
    """죽은 적/범위 밖 타겟 → 첫 살아있는 적으로 폴백."""
    alive = _alive_indices(monsters)
    if not alive:
        return None
    return target_idx if target_idx in alive else alive[0]



def handle_hand_select(state, avail):
    ss = state.get("screen_state", {})
    selected = ss.get("selected", [])
    max_cards = ss.get("max_cards", 1)
    min_cards = ss.get("min_cards", 0)

    
    current_action = state.get("current_action", "")
    if not any(k in current_action for k in ("Armaments", "Exhaust", "DualWield")):
        log_raw_state(state)

    # 1. 이미 최대로 골랐다면 confirm
    if len(selected) >= max_cards:
        if "confirm" in avail:
            print("confirm", flush=True)
        else:
            print("wait 30", flush=True)
        return

    # 손패 정보 가져오기
    # 화면(screen_state)에 표시된 손패가 우선, 없으면 전투 상태(combat_state)의 손패
    hand = ss.get("hand", state.get("combat_state", {}).get("hand", []))

    # [Armaments 등 특정 액션에 대한 카드 강화 선택]
    if "Armaments" in current_action:
        best_score = -9999
        best_idx = -1

        for i, c in enumerate(hand):
            if i in selected:
                continue

            c_name = c.get("name", "")
            base_info = db_loader.get_card_info(c_name)
            upg_info = db_loader.get_card_info(c_name + "+")

            score = 0
            if base_info and upg_info:
                def safe_cost(val):
                    try: return float(val)
                    except: return 99.0

                cost_diff = safe_cost(base_info.get("cost", 99)) - safe_cost(upg_info.get("cost", 99))
                # 코스트가 줄어드는 카드는 최우선
                if cost_diff > 0:
                    score += 1000 + cost_diff * 100

                # 점수(base_value) 상승폭 반영
                val_diff = upg_info.get("base_value", 0) - base_info.get("base_value", 0)
                score += val_diff

            # 저주나 상태이상은 강화 대상에서 제외 (혹은 최하순위)
            c_type = str(c.get("type", c.get("card_type", ""))).upper()
            if c_type in ("STATUS", "CURSE"):
                score -= 10000

            if score > best_score:
                best_score = score
                best_idx = i

        if best_idx != -1:
            print(f"choose {best_idx}", flush=True)
            log.info(f"🔨 Armaments 강화 선택: idx={best_idx} ({hand[best_idx].get('name')}), score={best_score:.2f}")
            return

    # [ExhaustAction 등 소멸 효과에 대한 카드 선택]
    if current_action == "ExhaustAction":
        best_score = 9999
        best_idx = -1
        
        # 나쁜 카드 우선 소멸 (저주, 상태이상 등)
        bad_cards_indices = []
        for i, c in enumerate(hand):        
            if i in selected:
                continue
            c_type = str(c.get("type", c.get("card_type", ""))).upper()
            if c_type in ("STATUS", "CURSE"):
                bad_cards_indices.append(i)
                
        can_pick_zero = ss.get("can_pick_zero", False)
        
        # 1. 고를 수 있는 제한이 넉넉하거나 필수인 경우 나쁜 카드 소멸
        if bad_cards_indices and len(selected) < max_cards:
            print(f"choose {bad_cards_indices[0]}", flush=True)
            log.info(f"🔥 ExhaustAction 소멸 선택 (상태이상/저주): idx={bad_cards_indices[0]} ({hand[bad_cards_indices[0]].get('name')})")
            return
            
        # 2. 나쁜 카드가 없고 고르는게 자유라면 더 이상 소멸하지 않음 (confirm)
        if can_pick_zero and not bad_cards_indices:
            if "confirm" in avail:
                print("confirm", flush=True)
                log.info(f"🔥 ExhaustAction 선택 종료 (더 이상 소멸할 나쁜 카드가 없음)")
                return
                
        # 3. 필수로 골라야 하는데 나쁜 카드가 없다면 가장 가치 낮은 카드 소멸
        if not can_pick_zero and len(selected) < max_cards:
            for i, c in enumerate(hand):
                if i in selected:
                    continue

                c_name = c.get("name", "")
                base_info = db_loader.get_card_info(c_name)
                
                score = 0
                if base_info:
                    # 기본 밸류가 낮을수록 소멸 우선순위 높음
                    score += base_info.get("base_value", 50)
                else:
                    score += 50
                    
                # 타격(Strike), 수비(Defend) 기본 카드들은 소멸 1순위
                if "Strike" in c_name or "Defend" in c_name:
                    score -= 50

                if score < best_score:
                    best_score = score
                    best_idx = i

            if best_idx != -1:
                print(f"choose {best_idx}", flush=True)
                log.info(f"🔥 ExhaustAction 강제 소멸 선택: idx={best_idx} ({hand[best_idx].get('name')}), 가치 점수={best_score}")
                return

    # [DualWieldAction 등 카드 복제에 대한 선택]
    if "DualWield" in current_action:
        best_score = -9999
        best_idx = -1

        for i, c in enumerate(hand):
            if i in selected:
                continue

            c_name = c.get("name", "")
            base_info = db_loader.get_card_info(c_name)

            score = 0
            if base_info:
                score += base_info.get("base_value", 0)

                # 코스트가 낮아서 바로 쓰기 좋은 카드에 가산점
                cost = base_info.get("cost", 99)
                try:
                    cost_val = float(cost)
                    if cost_val == 0:
                        score += 20
                    elif cost_val == 1:
                        score += 10
                except ValueError:
                    pass

            c_type = str(c.get("type", c.get("card_type", ""))).upper()
            if c_type == "ATTACK":
                score += 10
            elif c_type == "POWER":
                score += 15

            if score > best_score:
                best_score = score
                best_idx = i

        if best_idx != -1:
            print(f"choose {best_idx}", flush=True)
            log.info(f"✨ DualWield 복제 선택: idx={best_idx} ({hand[best_idx].get('name')}), score={best_score:.2f}")
            return

    # 2. 우선적으로 고를 대상: 상태이상(Status) 또는 저주(Curse) 카드
    bad_cards_indices = []
    for i, c in enumerate(hand):        
        if i in selected:
            continue
        c_type = str(c.get("type", c.get("card_type", ""))).upper()
        if c_type in ("STATUS", "CURSE"):
            bad_cards_indices.append(i)
            
    if bad_cards_indices:
        print(f"choose {bad_cards_indices[0]}", flush=True)
        return

    # 3. 나쁜 카드는 없지만 무조건 더 골라야 하는 경우 (min_cards 불충족 또는 can_pick_zero가 False)
    can_pick_zero = ss.get("can_pick_zero", True)
    if len(selected) < min_cards or (not can_pick_zero and len(selected) < max_cards):
        log.warning(f"⚠️ 알 수 없는 액션({current_action}) 강제 선택: 첫 번째 카드 선택 (can_pick_zero={can_pick_zero}, min_cards={min_cards})")
        for i in range(len(hand)):
            if i not in selected:
                print(f"choose {i}", flush=True)
                return

    # 4. 필수 할당량을 채웠고 남은 나쁜 카드도 없다면 confirm
    if "confirm" in avail:
        print("confirm", flush=True)
    else:
        print("wait 30", flush=True)
