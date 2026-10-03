"""Explanation helpers for result cards ("What AI Sees", search understanding).

These functions only *explain* results that already came from vector search.
They are never used for retrieval (no keyword/ LIKE / TF-IDF matching).
"""
from __future__ import annotations

import re

_STOP = {
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "with", "by", "from", "into", "is", "are", "was", "were", "be", "been",
    "being", "it", "its", "this", "that", "these", "those", "as", "so", "his",
    "her", "their", "his/her", "someone", "somebody", "something", "there",
    "here", "when", "while", "who", "whom", "which", "what", "how", "then",
    "than", "up", "down", "out", "over", "under", "again", "very", "can",
    "will", "just", "about", "across", "through", "during", "before", "after",
    "above", "below", "between", "one", "two", "you", "your", "we", "they",
}

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]+|[0-9]+")


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _WORD_RE.findall(text or "")]


def extract_concepts(text: str, limit: int = 8) -> list[str]:
    """Salient words/phrases from a real description or query (explanation only)."""
    raw = text or ""
    words = _WORD_RE.findall(raw)
    lower = [w.lower() for w in words]

    bigrams: list[str] = []
    for i in range(len(lower) - 1):
        a, b = lower[i], lower[i + 1]
        if a in _STOP or b in _STOP:
            continue
        bigrams.append(f"{words[i]} {words[i + 1]}")

    seen: set[str] = set()
    out: list[str] = []
    # prefer informative bigrams first, then single salient words
    for phrase in bigrams:
        key = phrase.lower()
        if key not in seen:
            seen.add(key)
            out.append(phrase)
        if len(out) >= limit:
            return out
    for i, w in enumerate(lower):
        if w in _STOP or len(w) < 3 or w in seen:
            continue
        seen.add(w)
        out.append(words[i])
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
# Query Understanding Contract (ARCHITECTURE §8).
# Buckets below only *structure* the query for retrieval-side filtering and
# the UI; they never replace embedding-based retrieval.
# --------------------------------------------------------------------------- #

_ENTITIES = {
    "person", "people", "man", "woman", "men", "women", "boy", "girl", "child",
    "children", "kid", "kids", "baby", "family", "crowd", "team", "chef", "couple",
    "dog", "cat", "animal", "horse", "bird", "puppy", "kitten",
    "car", "truck", "bus", "bike", "bicycle", "motorcycle", "train", "plane",
    "airplane", "boat", "ship",
    "city", "street", "road", "highway", "traffic", "building", "house", "room",
    "office", "kitchen", "beach", "ocean", "sea", "forest", "mountain", "park",
    "field", "desert", "river", "sky", "sun", "moon", "star", "stars", "cloud",
    "clouds", "rain", "snow", "storm", "fire", "water", "wave", "waves",
    "laptop", "phone", "camera", "computer", "keyboard", "screen", "book",
    "food", "coffee", "meal", "drink", "ball", "flower", "flowers", "tree",
    "trees", "window", "door", "chair", "table", "bed", "light", "shadow",
}

_ACTIONS = {
    "walking", "run", "running", "driving", "riding", "typing", "talking",
    "speaking", "laughing", "smiling", "crying", "cooking", "dancing", "playing",
    "working", "jumping", "eating", "drinking", "cheering", "clapping", "flying",
    "swimming", "building", "reading", "writing", "looking", "staring", "standing",
    "sitting", "kissing", "hugging", "fighting", "chasing", "falling", "climbing",
    "waving", "pointing", "pouring", "cutting", "washing", "cleaning", "opening",
    "closing", "searching", "holding", "carrying", "throwing", "catching",
    "celebrating", "meeting", "greeting", "shopping", "traveling", "waiting",
}

_ENVIRONMENT = {
    "indoor", "outdoor", "night", "day", "morning", "evening", "afternoon",
    "midnight", "dark", "bright", "rainy", "snowy", "sunny", "cloudy", "foggy",
    "cityscape", "countryside", "suburban", "urban", "studio", "classroom",
    "stadium", "garden", "yard", "rooftop", "bridge", "market", "restaurant",
    "gym", "beachfront", "wilderness",
}

_CONTEXT = {
    "work", "school", "party", "wedding", "game", "match", "concert", "holiday",
    "birthday", "travel", "vacation", "rush", "commute", "interview", "exam",
    "protest", "parade", "festival", "sport", "race", "war", "rescue", "news",
    "family time", "late night", "early morning", "weekend",
}

_MOOD = {
    "happy", "sad", "lonely", "tired", "angry", "calm", "excited", "nervous",
    "afraid", "scared", "fear", "love", "romantic", "mysterious", "dramatic",
    "funny", "serious", "warm", "cold", "cozy", "melancholy", "hopeful",
    "stress", "stressed", "joy", "peaceful", "energetic", "quiet", "loud",
    "nostalgic", "dreamy", "chaotic", "relaxed", "anxious", "free", "trapped",
}

_BUCKETS = (
    ("entities", _ENTITIES),
    ("actions", _ACTIONS),
    ("environment", _ENVIRONMENT),
    ("context", _CONTEXT),
    ("mood", _MOOD),
)


def understand(query: str, filters: dict | None = None) -> dict:
    """Structured internal query representation (ARCHITECTURE §8).

    UI labels may change without touching retrieval code: downstream consumers
    read these keys, not the wording of the query.
    """
    toks = _tokens(query)
    out: dict = {
        "query": query,
        "entities": [],
        "actions": [],
        "environment": [],
        "context": [],
        "mood": [],
        "filters": dict(filters or {}),
    }
    bucketed: set[str] = set()
    for name, lexicon in _BUCKETS:
        hits: list[str] = []
        for t in toks:
            if t in lexicon and t not in hits:
                hits.append(t)
        # multi-word context phrases ("late night", "family time")
        phrase = " ".join(toks)
        for term in lexicon:
            if " " in term and term in phrase and term not in hits:
                hits.append(term)
        out[name] = hits[:8]
        bucketed.update(hits)

    # Unbucketed salient words fall back to entities so nothing is lost.
    for c in extract_concepts(query, limit=8):
        t = c.lower()
        if t in bucketed or t in _STOP or t in out["entities"]:
            continue
        out["entities"].append(t)
        if len(out["entities"]) >= 6:
            break
    return out


def describe_query(query: str, filters: dict | None = None) -> dict:
    """Search understanding panel (embedding retrieval stays untouched)."""
    toks = _tokens(query)
    understanding = understand(query, filters=filters)
    return {
        "concepts": extract_concepts(query, limit=8),
        "word_count": len(toks),
        "is_question": query.strip().endswith("?"),
        **understanding,
    }

