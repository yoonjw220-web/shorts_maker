"""내 자막 스타일 학습.

메모장에서 자막을 고칠 때마다 'AI가 만든 것 → 내가 고친 것'을 예시로 저장해 두고,
다음 영상의 자막을 만들 때 AI에게 그 예시와 규칙(style/rules.md)을 보여줘서
처음부터 내 방식대로 줄을 끊고 표현하게 만든다.
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
STYLE_DIR = os.path.join(BASE, "style")
RULES_PATH = os.path.join(STYLE_DIR, "rules.md")
EXAMPLES_PATH = os.path.join(STYLE_DIR, "examples.json")
MAX_EXAMPLES = 8          # 저장해 두는 예시 수 (오래된 것부터 지움)
PROMPT_EXAMPLES = 3       # AI에게 한 번에 보여주는 예시 수 (최신순)


def load_rules():
    try:
        return open(RULES_PATH, encoding="utf-8").read().strip()
    except Exception:
        return ""


def load_examples():
    try:
        data = json.load(open(EXAMPLES_PATH, encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _norm(subs):
    return [{"text": (x.get("text") or "").strip(), "context": (x.get("context") or "").strip(),
             "sfx": (x.get("sfx") or "").strip(),
             "start": round(float(x.get("start", 0)), 2), "end": round(float(x.get("end", 0)), 2)}
            for x in subs if (x.get("text") or "").strip()]


def record_edit(key, before, after, title=""):
    """AI 결과(before)와 내가 고친 결과(after)가 다르면 예시로 저장. 같은 영상(key)은 최신 수정본으로 덮어씀."""
    b, a = _norm(before), _norm(after)
    key3 = lambda L: [(x["text"], x["context"], x.get("sfx", "")) for x in L]
    if key3(b) == key3(a):
        return False
    ex = [e for e in load_examples() if e.get("key") != key]
    ex.append({"key": key, "title": title, "before": b, "after": a})
    ex = ex[-MAX_EXAMPLES:]
    os.makedirs(STYLE_DIR, exist_ok=True)
    json.dump(ex, open(EXAMPLES_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    changed = sum(1 for x, y in zip(b, a) if x["text"] != y["text"]) + abs(len(b) - len(a))
    print(f"   🧠 내 수정 습관을 학습 예시로 저장했어요 (약 {changed}줄 차이, 예시 총 {len(ex)}개)")
    return True


def _lines(subs, with_time=True, with_ctx=False):
    out = []
    for x in subs:
        t = f"[{x['start']:.2f}~{x['end']:.2f}] " if with_time else ""
        c = f"   〔상황: {x['context']}〕" if with_ctx and x.get("context") else ""
        out.append(f"{t}{x['text']}{c}")
    return "\n".join(out)


def segment_examples_text():
    """줄 나누기·표현 예시 (AI가 만든 것 → 내가 고친 것)."""
    ex = load_examples()[-PROMPT_EXAMPLES:]
    blocks = []
    for k, e in enumerate(reversed(ex), 1):
        blocks.append(f"### 예시 {k} ({e.get('title', '')})\n[AI가 처음 만든 자막]\n{_lines(e['before'])}\n\n"
                      f"[내가 고친 최종 자막]  ← 이 방식을 따라 해\n{_lines(e['after'])}")
    return "\n\n".join(blocks)


def context_examples_text():
    """상황 자막(초록) 예시: 내가 최종으로 남긴 것만."""
    ex = load_examples()[-PROMPT_EXAMPLES:]
    blocks = []
    for e in reversed(ex):
        if any(x.get("context") for x in e["after"]):
            blocks.append(f"### {e.get('title', '')}\n{_lines(e['after'], with_time=False, with_ctx=True)}")
    return "\n\n".join(blocks)


def sfx_examples_text():
    """효과음 예시: 내가 최종으로 남긴 효과음 위치."""
    ex = load_examples()[-PROMPT_EXAMPLES:]
    blocks = []
    for e in reversed(ex):
        if any(x.get("sfx") for x in e["after"]):
            blocks.append(f"### {e.get('title', '')}\n" + "\n".join(
                x["text"] + (f"   〔효과음: {x['sfx']}〕" if x.get("sfx") else "") for x in e["after"]))
    return "\n\n".join(blocks)
