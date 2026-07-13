import json

import httpx

from .config import (
    ANTHROPIC_API_KEY,
    GITHUB_MODELS_ENDPOINT,
    GITHUB_TOKEN,
    LLM_MODEL,
    LLM_PROVIDER,
)

SYSTEM_PROMPT = (
    "당신은 러닝 코치입니다. 사용자의 실제 훈련 데이터 분석(JSON: 볼륨/ACWR/페이스·심박/목표 격차)과 "
    "규칙 기반으로 생성된 다음 주 계획(JSON), 그리고 사용자의 목표(JSON)를 받습니다. "
    "한국어로 다음 3개 섹션을 작성하세요. 마크다운 소제목(**굵게**)을 쓰세요.\n"
    "**현재 상태 분석**: 데이터에 근거해 최근 볼륨 추세, ACWR(부하), 페이스·심박 흐름, 롱런을 2~4문장으로 평가.\n"
    "**목표까지의 격차**: goal_status/pace_gap을 근거로 목표 달성 가능성과 남은 과제를 2~3문장으로.\n"
    "**이번 주 포커스**: 계획의 각 핵심 세션에서 주의할 점을 구체적 수치(페이스/심박존)와 함께 3~4문장으로. "
    "계획 표 자체를 새로 만들지는 마세요. 숫자는 반드시 주어진 데이터에서만 인용하세요."
)


def _user_content(analysis: dict, plan: dict, goal: dict) -> str:
    return json.dumps(
        {"training_analysis": analysis, "next_week_plan": plan, "goal": goal},
        ensure_ascii=False,
    )


def _github_chat(system: str, user_content: str, max_tokens: int = 600, json_mode: bool = False) -> str:
    """GitHub Models(OpenAI 호환 API)로 GPT-4o 호출."""
    payload = {
        "model": LLM_MODEL,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    resp = httpx.post(
        f"{GITHUB_MODELS_ENDPOINT.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _github_commentary(user_content: str) -> str:
    return _github_chat(SYSTEM_PROMPT, user_content)


def _anthropic_commentary(user_content: str) -> str:
    from anthropic import Anthropic

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=600,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return "".join(block.text for block in resp.content if block.type == "text")


COACH_SYSTEM = (
    "당신은 데이터 기반 러닝 코치입니다. 사용자의 목표(goal), 훈련 분석(analysis), "
    "최근 14일 날짜별 타임라인(recent_days: 각 날짜가 rest=true면 그날 쉰 것, false면 그날 뛴 세션), "
    "마지막 러닝 후 경과일(analysis.days_since_last_run), 오늘 날짜(today)와 훈련 단계(phase)를 받습니다. "
    "recent_days로 최근 며칠을 이미 쉬었는지/연속으로 뛰었는지 반드시 파악하세요. "
    "예: 어제(오늘 하루 전)가 rest였고 그제도 쉬었다면 오늘 또 rest를 넣지 말고 가벼운 회복 조깅을 배치하세요. "
    "반대로 최근 며칠 연속으로 뛰었으면 회복일을 넣으세요. "
    "실제 데이터에 근거해 오늘(today)부터 다음 7일 훈련 계획을 직접 설계하세요. 반드시 아래 JSON만 출력하세요:\n"
    '{\n'
    '  "analysis": "현재 상태·목표 격차를 2~4문장으로 요약한 한국어 텍스트(**굵게** 허용)",\n'
    '  "plan": [\n'
    '    {"date":"YYYY-MM-DD","type":"easy|tempo|threshold|vo2|long_run|rest",'
    '"title":"세션명","detail":"거리·페이스·심박존을 담은 구체 지시"}\n'
    "  ],\n"
    '  "focus": "이번 주 핵심 한 줄"\n'
    "}\n"
    "규칙: plan은 today부터 7일. ACWR가 높으면(1.3+) 회복/rest를 넣어 부하를 낮추고, "
    "목표 페이스 격차(pace_gap_sec)가 크면 그 페이스 구간을 threshold/vo2에 구체적으로 배치하세요. "
    "롱런은 주 1회, 최근 최장거리에서 급격히 늘리지 마세요. 페이스·거리는 사용자 실제 기록 범위에서 현실적으로.\n"
    "★ user_comment가 비어있지 않으면 그 요청을 최우선으로 반영하세요 "
    "(예: 부상/통증 → 강도 낮추고 회복 위주, 일정/시간 제약 → 세션 길이 조정, 대회 준비 → 특정 세션 강화). "
    "analysis 텍스트 첫 문장에 코멘트를 어떻게 반영했는지 한 줄로 밝히세요."
)


def _parse_plan(text: str) -> dict:
    import re

    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", t).strip()
    return json.loads(t)


def generate_plan(analysis: dict, goal: dict, recent_days: list, today: str, phase: str,
                  user_comment: str = "") -> dict:
    """GPT-4o가 실제 세션 데이터(+사용자 코멘트)로 다음 7일 계획을 직접 설계. 실패 시 {'error':...}."""
    user_content = json.dumps(
        {
            "goal": goal,
            "analysis": analysis,
            "recent_days": recent_days,
            "today": today,
            "phase": phase,
            "user_comment": (user_comment or "").strip(),
        },
        ensure_ascii=False,
    )
    try:
        if LLM_PROVIDER == "github":
            if not GITHUB_TOKEN:
                return {"error": "GITHUB_TOKEN 미설정"}
            raw = _github_chat(COACH_SYSTEM, user_content, max_tokens=1200, json_mode=True)
        elif LLM_PROVIDER == "anthropic":
            if not ANTHROPIC_API_KEY:
                return {"error": "ANTHROPIC_API_KEY 미설정"}
            from anthropic import Anthropic

            client = Anthropic(api_key=ANTHROPIC_API_KEY)
            resp = client.messages.create(
                model="claude-sonnet-5", max_tokens=1200, system=COACH_SYSTEM,
                messages=[{"role": "user", "content": user_content}],
            )
            raw = "".join(b.text for b in resp.content if b.type == "text")
        else:
            return {"error": f"알 수 없는 LLM_PROVIDER={LLM_PROVIDER!r}"}
        return _parse_plan(raw)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def generate_commentary(analysis: dict, plan: dict, goal: dict) -> str:
    user_content = _user_content(analysis, plan, goal)
    try:
        if LLM_PROVIDER == "github":
            if not GITHUB_TOKEN:
                return "(GITHUB_TOKEN 미설정 — LLM 코칭 코멘트 생략)"
            return _github_commentary(user_content)
        if LLM_PROVIDER == "anthropic":
            if not ANTHROPIC_API_KEY:
                return "(ANTHROPIC_API_KEY 미설정 — LLM 코칭 코멘트 생략)"
            return _anthropic_commentary(user_content)
        return f"(알 수 없는 LLM_PROVIDER={LLM_PROVIDER!r})"
    except Exception as e:  # noqa: BLE001 - 대시보드가 코멘트 실패로 죽지 않게
        return f"(코칭 코멘트 생성 실패: {type(e).__name__}: {e})"
