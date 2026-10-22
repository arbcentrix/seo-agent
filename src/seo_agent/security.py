"""Flag untrusted text (search queries, page content) that looks like instructions to an AI.

Scripts only flag; nothing here acts on the text. Flags go to runs.jsonl (CLAUDE.md).
"""
import re

PATTERNS = [
    r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|all)\b.{0,20}\b(instructions?|rules?|prompts?)\b",
    r"\b(ignore|disregard|forget|override)\b.{0,30}\b(your|my|the|these|any)\b.{0,20}\b(instructions?|rules?|prompts?|guidelines)\b",
    r"\b(send|forward|email|e-mail)\b.{0,40}\b(everyone|everybody|all|the)\b.{0,30}\b(inbox|mailbox|emails?|mail|calendar|files|contacts)\b",
    r"\b(search|read|dump|exfiltrate|list)\b.{0,30}\b(inbox|mailbox|emails|calendar|teams messages)\b",
    r"\b(system prompt|developer message|jailbreak)\b",
    r"\byou are (now )?(an?|the) (ai|assistant|agent|llm|chatgpt|claude)\b",
    r"\b(send|forward|email)\b.{0,40}\b(to|@)\b.{0,60}@",
    r"\b(claude|chatgpt|gpt-?\d|llm|ai agent)\b.{0,40}\b(must|should|please|now)\b",
    r"<\s*/?\s*(system|instructions?|prompt)\s*>",
]
_RE = re.compile("|".join(f"(?:{p})" for p in PATTERNS), re.I)


def flag(texts, source: str, limit: int = 20) -> list[dict]:
    out, seen = [], set()
    for t in texts:
        if t and t not in seen and _RE.search(t):
            seen.add(t)
            out.append({"source": source, "text": t[:200]})
            if len(out) >= limit:
                break
    return out
