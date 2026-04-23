import sys
from db import db_loader
import random
import re
import logging

log = logging.getLogger("STS_AI")
def enrich_hand(hand):
    """
    [핵심 전처리 함수]
    DFS나 모듈 연산에 들어가기 전, 손패의 카드들에 DB 스탯을 딱 한 번만 채워 넣습니다.
    """
    for card in hand:
        # 이미 전처리가 끝난 카드는 스킵 (DFS 재귀에서 중복 처리 방지)
        if "damage" in card:
            continue
            
        name = card.get("name")
        info = db_loader.get_card_info(name)
        
        if info:
            card["damage"] = info.get("damage", 0)
            card["block"] = info.get("block", 0)
            card["draw"] = info.get("draw", 0) # V3에서 추가된 드로우
            
            # 시너지와 Base Value도 그대로 가져옵니다
            card["base_value"] = info.get("base_value", 0)
            card["synergy"] = info.get("synergy", {"requires": {}, "provides": {}})
        else:
            log.info(f"info 발견 x, {name}")
            
    return hand


def damage_dfs(current_hand, current_energy, is_vulnerable):
    # 💡 인자에서 CARD_DB 삭제, 내부 DB 호출 삭제 (순수 연산만 수행)
    max_dmg_here = 0    
    # log.info("bfs 실시")
    for i, card in enumerate(current_hand):
        cost = card.get("cost", 99)
        
        # 낼 수 있는 카드인가?
        if current_energy >= cost and card.get("is_playable", False):
            # 1. 이미 전처리된 데미지 가져오기
            base_dmg = card.get("damage", 0)
            if is_vulnerable:
                base_dmg = int(base_dmg * 1.5)
            
            next_vulnerable = is_vulnerable
            if "Vulnerable" in card.get("effects", {}):
                next_vulnerable = True
            
            # 재귀 호출
            remaining_hand = current_hand[:i] + current_hand[i+1:]
            extra_dmg = damage_dfs(remaining_hand, current_energy - cost, next_vulnerable)
            
            total = base_dmg + extra_dmg
            if total > max_dmg_here:
                max_dmg_here = total
                
    return max_dmg_here


def max_damage_expert(hand, energy, monster):
    is_vulnerable = any(p['id'] == 'Vulnerable' for p in monster.get('powers', []))
    
    best_dmg = -1
    next_card_idx = -1
    
    # 💡 1. 알고리즘 시작 전 손패 전처리 (딱 한 번만 실행됨)
    enriched_hand = enrich_hand(hand)
    
    # 첫 번째 수(Move) 결정
    for i, card in enumerate(enriched_hand):
        cost = card.get("cost", 99)
        
        if energy >= cost and card.get("is_playable", False): 
            current_dmg = card.get("damage", 0)
            if is_vulnerable: current_dmg = int(current_dmg * 1.5)
            
            next_vulnerable = is_vulnerable
            if "Vulnerable" in card.get("effects", {}): next_vulnerable = True
            
            remaining_hand = enriched_hand[:i] + enriched_hand[i+1:]
            
            total_potential = current_dmg + damage_dfs(remaining_hand, energy - cost, next_vulnerable)
            
            if total_potential > best_dmg:
                best_dmg = total_potential
                next_card_idx = i

    return next_card_idx


def defensive_expert(hand, energy, player_block, monsters):
    total_incoming_damage = 0

    for i, m in enumerate(monsters):
        if not m.get("is_gone", False) and not m.get("half_dead", False):
            intent = m.get("intent", "")
            if "ATTACK" in intent:
                hit_count = m.get("move_adjusted_damage", 0)
                times = m.get("move_hits", 1)
                total_incoming_damage += (hit_count * times)
                
    if total_incoming_damage <= player_block:
        return -1

    best_defense_value = -1
    next_card_idx = -1

    # 💡 전처리된 손패 사용
    enriched_hand = enrich_hand(hand)

    for i, card in enumerate(enriched_hand):
        cost = card.get("cost", 99)
        
        if energy >= cost and card.get("is_playable", False):
            defense_value = card.get("block", 0)
            if defense_value == 0:
                continue
                
            if defense_value > best_defense_value:
                best_defense_value = defense_value
                next_card_idx = i
                
    return next_card_idx


def lethal_expert(hand, energy, target_monster):
    target_hp = target_monster.get("current_hp", 0)
    if target_hp <= 0:
        return -1
        
    is_vulnerable = any(p['id'] == 'Vulnerable' for p in target_monster.get('powers', []))
    
    best_card_idx = -1
    min_cost_to_kill = 999
    min_damage = 999
    
    # 💡 전처리된 손패 사용 및 낡은 CARD_DB 직접 호출 방식 제거
    enriched_hand = enrich_hand(hand)
    
    for i, card in enumerate(enriched_hand):
        cost = card.get("cost", 99)
        base_dmg = card.get("damage", 0)
        
        if energy >= cost and card.get("is_playable", False):
            current_dmg = base_dmg
            if is_vulnerable:
                current_dmg = int(current_dmg * 1.5)
                
            if current_dmg >= target_hp:
                # 킬각일 때 가장 코스트가 적은 공격 선호 (동일 코스트라면 오버킬을 줄이거나 취향껏)
                if cost <= min_cost_to_kill and min_damage > current_dmg: 
                    min_cost_to_kill = cost
                    best_card_idx = i
                    min_damage = current_dmg
                    
    return best_card_idx





MATCH_AND_KEEP_MEMORY = {i: None for i in range(12)}
def reset_match_and_keep():
    global MATCH_AND_KEEP_MEMORY
    MATCH_AND_KEEP_MEMORY = {i: None for i in range(12)}

def evaluate_card_for_match(card_id):
    """
    [임시 카드 평가 함수]
    카드가 덱에 필요한지(GOOD), 피해야 하는 저주/쓰레기인지(BAD) 평가합니다.
    나중에 LLM RAG 등을 연결해서 덱 시너지 기반으로 고도화할 수 있습니다.
    """
    if card_id is None:
        return "UNKNOWN"
        
    # 슬더스의 대표적인 저주 카드들 (필요에 따라 추가)
    bad_cards = ["CurseOfTheBell", "AscendersBane", "Necronomicurse", 
                 "Normality", "Pain", "Regret", "Doubt", "Decay", "Writhe", "Shame", "Injury"]
                 
    # 카드 ID에 Curse가 포함되어 있거나, 나쁜 카드 목록에 있으면 무조건 회피
    if "Curse" in card_id or card_id in bad_cards:
        return "BAD"
        
    # 그 외의 카드는 일단 먹을 가치가 있다고 판단
    return "GOOD"


def match_and_keep_expert(available_commands, choices):
    """
커뮤니케이션 모드의 미구현으로 간략하게만 구현...
    """
    if "proceed" in available_commands:
        log.info("🚪 짝맞추기 이벤트 완료! 진행(proceed)합니다.")
        print("proceed", flush=True)
        return  
        
    elif "leave" in available_commands:
        log.info("🚪 짝맞추기 이벤트 완료! 나갑니다(leave).")
        print("leave", flush=True)
        return

    if "choose" in available_commands and choices:
        raw_pick = choices[0]
        
        # "card5" 같은 문자열에서 숫자만 추출
        match = re.search(r'\d+', str(raw_pick))
        if match:
            pick_index = match.group()
        else:
            pick_index = raw_pick
            
        print(f"choose {pick_index}", flush=True)
        return  # 🚨 여기서도 return!

    # 💡 3순위: 카드가 뒤집히는 애니메이션 중이거나 할 게 없을 땐 대기!
    print("wait 30", flush=True)
    return