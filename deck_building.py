import ollama
import json
import re

from db_util import get_card_info
from collections import Counter


def choose_card_reward(current_deck, offered_cards):
    """
    현재 덱과 보상 카드들을 보고 LLM이 하나를 선택하는 함수
    :param current_deck: list of strings (e.g., ["Strike", "Strike", "Bash", "Cleave"])
    :param offered_cards: list of strings (e.g., ["Pommel Strike", "Shrug It Off", "Clash"])
    :return: 선택된 카드 이름 또는 "Skip"
    """
    
    # 1. 현재 덱 요약 (Counter를 써서 깔끔하게 압축)
    deck_counts = Counter(current_deck)
    deck_summary = ", ".join([f"{count}x {card}" for card, count in deck_counts.items()])

    # 2. RAG: 보상으로 나온 카드들의 정확한 스펙만 DB에서 추출
    reward_db_text = "[Offered Cards Info]\n"
    for c_name in offered_cards:
        info = get_card_info(c_name)
        if info:
            desc = info.get('description', '').replace('\n', ' ')
            reward_db_text += f"- {info['name']} (Type: {info.get('type')}, Cost: {info.get('cost')}): {desc}\n"

    # 3. 덱 빌딩 전용 프롬프트 (시너지와 덱 압축을 고려하도록 CoT 강제)
    prompt = f"""
    {reward_db_text}

    [Current Deck]
    {deck_summary}
    Total Cards: {len(current_deck)}

    [Task]
    You are an expert Slay the Spire player. 
    You are offered a card reward.
    You must choose ONE card to add to your deck, or choose "Skip" if none of the cards improve your deck.

    [Strategy Guidelines]
    - Do not bloat the deck if the deck is strong enough. If the offered cards do not synergize with the [Current Deck] or are just bad, choosing "Skip" is often the best play.
    - Consider what your deck lacks (e.g., AoE damage, scaling, card draw, or defense).
    - In Act1, since default cards(strike and defend) are very low value, you need to pick good stuff cards
    - After you get important cards(rare and uncommon card that has strong impact), you should plan your deck's key strategy and pick cards according to the plan afterward.

    Output EXACTLY in this format:
    Reasoning:
    1. Deck Needs: Briefly state what the Current Deck lacks.
    2. Synergy Analysis: Evaluate the offered cards against the deck.
    3. Conclusion: Why the selected option is the best.
    Selected Option: [Card Name from Offered Cards, or "Skip"]
    """

    print("덱 빌딩 전략을 구상하는 중...\n" + "="*50)

    # 4. LLM 호출 (객관적 판단을 위해 창의성 0)
    response = ollama.chat(
        model='my_sts_qwen', 
        messages=[
            {'role': 'system', 'content': 'You are a master Slay the Spire deck-builder.'},
            {'role': 'user', 'content': prompt}
        ],
        options={'temperature': 0.0, 'num_predict': 256}
    )

    content = response['message']['content']
    print(f"🤖 LLM의 고민:\n{content.strip()}\n")

    # 5. 파싱
    match = re.search(r"Selected Option:\s*(.+)", content, re.IGNORECASE)
    if match:
        selected_option = match.group(1).strip()
        
        # 유효성 검사
        if selected_option.lower() == "skip":
            return "Skip"
        
        for card in offered_cards:
            if card.lower() in selected_option.lower():
                return card
                
        print(f"🚨 LLM이 선택지에 없는 카드({selected_option})를 골랐습니다. 안전을 위해 Skip 처리합니다.")
        return "Skip"
    else:
        print("🚨 파싱 실패. 안전을 위해 Skip 처리합니다.")
        return "Skip"

