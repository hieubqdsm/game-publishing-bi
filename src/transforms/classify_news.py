"""
Phân loại tin tức thành 4 loại: ai / game / tech / steam_update.

Rule-based (không dùng model) — đủ tốt cho BI filtering, giải thích được,
và chạy offline. Nguyên tắc:

1. source_type 'ai_rss'      → 'ai'            (feed AI chuyên, chắc chắn)
2. source_type 'steam_news'  → 'steam_update'  (thông báo per-game từ Steam)
3. Báo game thuần (IGN, PCGamer, Eurogamer, RPS)
   → 'game' mặc định; chỉ flip thành 'ai' khi tiêu đề nhắc công ty/mô hình AI
   (OpenAI, GPT-6...) — tức tin AI thuần túy bị báo game cover.
   Tin "AI trong game" (NHL 27 dùng AI voiceover) vẫn giữ 'game'
   vì ngữ cảnh là ngành game.
4. Nguồn hỗn hợp (The Verge feed chính, Hacker News)
   → phân theo từ khóa tiêu đề: AI → 'ai', game → 'game', còn lại 'tech'.
"""
from __future__ import annotations

import re

CATEGORY_AI = "ai"
CATEGORY_GAME = "game"
CATEGORY_TECH = "tech"
CATEGORY_STEAM = "steam_update"

# Báo thuần về game — mặc định mọi tin là 'game'
GAMING_OUTLETS = {
    "IGN", "PCGamer", "Eurogamer", "Rock Paper Shotgun",
}

# Công ty / mô hình AI — tín hiệu mạnh trên mọi nguồn
AI_TERMS = re.compile(
    r"\b(openai|open ai|anthropic|claude|chatgpt|gpt-[\d.]|gemini|deepseek|"
    r"mistral|grok|copilot|llama\s?\d?|llm|llms|hugging face|midjourney|"
    r"sora|suno|character\.ai|perplexity)\b",
    re.IGNORECASE,
)

# Từ khóa AI rộng hơn — chỉ dùng cho nguồn hỗn hợp (Verge chính, HN),
# vì ở nguồn đó vắng tín hiệu công ty vẫn có thể là tin AI
AI_TERMS_BROAD = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|generative ai|ai-generated|"
    r"ai agent|machine learning|neural network|neural rendering)\b",
    re.IGNORECASE,
)

GAME_TERMS = re.compile(
    r"\b(game|games|gaming|gamer|gameplay|xbox|playstation|ps5|ps6|nintendo|"
    r"switch 2|steam deck|steam sale|valve|gta|grand theft auto|rockstar|"
    r"rpg|fps|mmorpg|console|esports|dlss|indie dev|video game|"
    # franchise lớn — hay xuất hiện trong tiêu đề mà không có từ "game"
    r"minecraft|pokemon|pok\xe9mon|zelda|mario|fortnite|valorant|"
    r"league of legends|dota|counter-strike|call of duty|elden ring|witcher|"
    r"cyberpunk|final fantasy|resident evil|monster hunter|dark souls|"
    r"baldur's gate|roblox)\b",
    re.IGNORECASE,
)


def classify_article(source_type: str, source_name: str,
                     title: str, summary: str | None = None) -> str:
    """Trả về category ('ai' | 'game' | 'tech' | 'steam_update') cho 1 bài tin."""
    text = f"{title} {summary or ''}"
    title_l = title.lower()

    if source_type == "ai_rss":
        return CATEGORY_AI
    if source_type == "steam_news":
        return CATEGORY_STEAM
    if AI_TERMS.search(text):
        return CATEGORY_AI

    if source_name in GAMING_OUTLETS:
        # Báo game: mặc định game (kể cả khi nhắc AI chung chung)
        return CATEGORY_GAME

    # Nguồn hỗn hợp (The Verge feed chính, Hacker News, nguồn lạ)
    if AI_TERMS_BROAD.search(title_l) or AI_TERMS.search(title_l):
        return CATEGORY_AI
    if GAME_TERMS.search(text):
        return CATEGORY_GAME
    return CATEGORY_TECH
