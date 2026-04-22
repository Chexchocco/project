# Slay the Spire AI Agent

An autonomous AI agent that plays **Slay the Spire**, a roguelike deck-building game, using a hybrid approach combining rule-based heuristics for combat and LLM-based reasoning for strategic decisions.

## 📚 Resources
For more detailed information that is not directly related to run this program, check the notion link below.

* [Project Wiki (Notion)](https://peat-beryllium-8dc.notion.site/Project-Dashboard-34a48fa0b95f8015b140dbcf4631f523?pvs=73)

## Overview

This project implements an intelligent game controller that communicates with a Slay the Spire game engine via stdin/stdout, making real-time gameplay decisions across multiple domains:


- **Combat**: Rule-based AI experts (lethal, defensive, max damage)
- **Deck Building**: LLM-powered card selection with RAG (Retrieval-Augmented Generation)
- **Event Resolution**: LLM reasoning with event spoiler hints
- **Map Navigation**: Currently basic routing (enhancement pending)
- **Shop Management**: Automated card purchasing logic

## Architecture

### Hybrid Decision System

TBD 

### Project Structure

TBD

## Prerequisites

### System Requirements
- Python 3.13+
- [Ollama](https://ollama.ai) (for local LLM inference)
- Qwen2.5-7B-Instruct model (~4.6GB quantized)

### Python Dependencies
```bash
pip install ollama
```

## Installation & Setup

TBD

## Usage
TBD

## Key Features

### Combat AI (Deterministic)
- **Lethal Expert**: Finds minimum-cost killing blow
- **Defensive Expert**: Selects optimal defense when needed
- **Max Damage Expert**: DFS lookahead to maximize damage output
- **Status Effects**: Considers Vulnerable debuff (50% damage boost)

### Deck Building AI (LLM-based)
- Analyzes current deck composition
- Retrieves card specs from database (RAG)
- Uses Chain-of-Thought reasoning
- Considers synergies and deck strategy

### Event Resolution (LLM-based)
- Evaluates event consequences with spoiler hints
- Uses game state (HP, gold, deck) for context-aware decisions
- Structured JSON output for reliability

## Logging

All agent decisions are logged to `agent_log.txt` with timestamps and emoji commentary for easy debugging.

## Database Schema

