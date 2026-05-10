import json
import re
import requests
from pathlib import Path
from typing import Optional

LLAMA_SERVER_URL = 'http://127.0.0.1:8080/v1/chat/completions'
_ROOT_DIR = Path(__file__).parent
_CONTEXT_DIR = _ROOT_DIR / 'ai_context'

# ──────────────────────────────────────────────
# 내부 유틸
# ──────────────────────────────────────────────

def _call(prompt: str) -> str:
    try:
        resp = requests.post(
            LLAMA_SERVER_URL,
            json={
                'model': 'local',
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.7,
                'max_tokens': 512,
            },
            timeout=600,
        )
        resp.raise_for_status()
        return resp.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        raise RuntimeError(f'llama-server 오류: {e}')

def _strip_code_block(text: str) -> str:
    return re.sub(r'```(?:json)?', '', text).strip()

# ──────────────────────────────────────────────
# 스타일 가이드 & 사례 로딩 (모듈 레벨 캐시)
# ──────────────────────────────────────────────

_style_guide_cache: Optional[str] = None
_examples_cache: Optional[list] = None

def _load_style_guide() -> str:
    """프롬프트 주입용 — antiegg_style_guide.md 로드 (AI_MANUAL.md는 사람용 참조 문서)."""
    global _style_guide_cache
    if _style_guide_cache is None:
        path = _CONTEXT_DIR / 'antiegg_style_guide.md'
        _style_guide_cache = path.read_text(encoding='utf-8') if path.exists() else ''
    return _style_guide_cache

def _load_examples() -> list:
    global _examples_cache
    if _examples_cache is None:
        path = _CONTEXT_DIR / 'reply_examples.json'
        if path.exists():
            data = json.loads(path.read_text(encoding='utf-8'))
            _examples_cache = data.get('examples', [])
        else:
            _examples_cache = []
    return _examples_cache

def _find_examples(inquiry_type: str, n: int = 2) -> list:
    """inquiry_type 일치 사례 우선 반환. 부족하면 다른 유형으로 채움."""
    all_examples = _load_examples()
    matched = [e for e in all_examples if e.get('inquiry_type') == inquiry_type]
    if len(matched) < n:
        others = [e for e in all_examples if e.get('inquiry_type') != inquiry_type]
        matched = matched + others[:n - len(matched)]
    return matched[:n]

def _build_examples_block(examples: list) -> str:
    if not examples:
        return '(사례 없음)'
    lines = []
    for i, ex in enumerate(examples, 1):
        lines.append(f'[사례 {i}]')
        lines.append(f'문의 유형: {ex.get("inquiry_type", "")}')
        lines.append(f'문의 요약: {ex.get("summary", "")}')
        lines.append('회신:')
        lines.append(ex.get('reply', ''))
        lines.append('')
    return '\n'.join(lines).strip()

# ──────────────────────────────────────────────
# 공개 함수
# ──────────────────────────────────────────────

def parse_email(email_body: str) -> dict:
    prompt = f"""아래 이메일에서 정보를 추출해 JSON으로만 응답하세요. 코드블록 없이 JSON만 출력하세요.
없는 정보는 "미상"으로 채우세요.

[예시 1]
이메일:
"안녕하세요, ABC 물류 김철수 과장입니다. 귀사 AI 재고관리 솔루션 도입을 검토 중입니다.
다음 달까지 도입을 목표로 하고 있습니다. 연락처: 010-1234-5678"

출력:
{{"company":"ABC 물류","contact_name":"김철수","contact_title":"과장","contact_phone":"010-1234-5678","email":"미상","inquiry_type":"도입문의","service_interest":"AI 재고관리 솔루션","summary":"ABC 물류 김철수 과장이 AI 재고관리 솔루션 도입 문의. 다음 달 도입 목표."}}

[예시 2]
이메일:
"파트너십 제안드립니다. XYZ 에이전시 대표 이영희입니다. 귀사와 공동 마케팅을 검토하고 싶습니다."

출력:
{{"company":"XYZ 에이전시","contact_name":"이영희","contact_title":"대표","contact_phone":"미상","email":"미상","inquiry_type":"파트너십","service_interest":"공동 마케팅","summary":"XYZ 에이전시 이영희 대표의 공동 마케팅 파트너십 제안."}}

[예시 3]
이메일:
"안녕하세요. DEF 테크 박민준 대리입니다. 가격 문의드립니다."

출력:
{{"company":"DEF 테크","contact_name":"박민준","contact_title":"대리","contact_phone":"미상","email":"미상","inquiry_type":"가격문의","service_interest":"미상","summary":"DEF 테크 박민준 대리의 가격 문의."}}

[실제 입력]
이메일:
{email_body}

출력:"""

    raw = _strip_code_block(_call(prompt))
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            'company': '미상', 'contact_name': '미상', 'contact_title': '미상',
            'contact_phone': '미상', 'email': '미상', 'inquiry_type': '기타',
            'service_interest': '미상', 'summary': email_body[:200],
        }

def generate_reply_draft(summary: str, contact_name: str = '', inquiry_type: str = '') -> str:
    style_guide = _load_style_guide()
    examples = _find_examples(inquiry_type)
    examples_block = _build_examples_block(examples)

    name_str = f'{contact_name}님' if contact_name and contact_name != '미상' else '담당자님'

    prompt = f"""[ANTIEGG 운영 스타일 가이드]
{style_guide}

[유사 회신 사례]
{examples_block}

[작성 지침]
- 250~350자 한국어
- 인사 + 감사 → 추가 질문 2~3개 → 마무리 구조
- 위 사례의 말투와 구조를 참고하되, 내용은 아래 문의에 맞게 작성
- 담당자 호칭: "{name_str}"

[실제 문의]
문의 유형: {inquiry_type or '기타'}
문의 요약: {summary}

회신 초안:"""

    return _call(prompt)

def generate_knock_draft(company: str, contact_name: str, stage: str) -> str:
    style_guide = _load_style_guide()
    name_str = f'{contact_name}님' if contact_name and contact_name != '미상' else '담당자님'

    if stage == 'KNOCK_REPLY':
        prompt = f"""[ANTIEGG 운영 스타일 가이드]
{style_guide}

[작성 지침]
- 1차 회신 후 7일간 응답 없는 고객에게 보내는 노크 메일
- 150~200자 한국어
- 부담 없이 확인 요청하는 톤, 재촉 금지
- 추가 문의 있으면 편히 연락달라는 내용 포함

[실제 정보]
회사명: {company}
담당자: {name_str}

노크 초안:"""
    else:
        prompt = f"""[ANTIEGG 운영 스타일 가이드]
{style_guide}

[작성 지침]
- 견적서 발송 후 7일간 응답 없는 고객에게 보내는 노크 메일
- 150~200자 한국어
- 견적 검토 결과 확인, 조건 조율 가능하다는 내용을 자연스럽게 포함
- 부드러운 톤, 재촉 금지

[실제 정보]
회사명: {company}
담당자: {name_str}

노크 초안:"""

    return _call(prompt)
