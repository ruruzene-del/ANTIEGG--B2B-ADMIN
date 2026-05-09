import json
import re
import requests

LLAMA_SERVER_URL = 'http://127.0.0.1:8080/v1/chat/completions'

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

def parse_email(email_body: str) -> dict:
    prompt = f"""아래 이메일에서 정보를 추출해 JSON으로만 응답하세요. 코드블록 없이 JSON만 출력하세요.
없는 정보는 "미상"으로 채우세요.

urgency 필드는 반드시 아래 4가지 중 하나만 사용하세요:
긴급(1주내) | 보통(1개월내) | 여유(1개월+) | 미상

[예시 1]
이메일:
"안녕하세요, ABC 물류 김철수 과장입니다. 귀사 AI 재고관리 솔루션 도입을 검토 중입니다.
현재 직원 50명 규모이며 다음 달까지 도입을 목표로 하고 있습니다. 연락처: 010-1234-5678"

출력:
{{"company":"ABC 물류","contact_name":"김철수","contact_title":"과장","contact_phone":"010-1234-5678","email":"미상","inquiry_type":"도입문의","service_interest":"AI 재고관리 솔루션","scale":"중규모(10-100명)","urgency":"긴급(1주내)","summary":"ABC 물류 김철수 과장이 AI 재고관리 솔루션 도입 문의. 50명 규모, 다음 달 도입 목표."}}

[예시 2]
이메일:
"파트너십 제안드립니다. XYZ 에이전시 대표 이영희입니다. 귀사와 공동 마케팅을 검토하고 싶습니다."

출력:
{{"company":"XYZ 에이전시","contact_name":"이영희","contact_title":"대표","contact_phone":"미상","email":"미상","inquiry_type":"파트너십","service_interest":"공동 마케팅","scale":"미상","urgency":"미상","summary":"XYZ 에이전시 이영희 대표의 공동 마케팅 파트너십 제안."}}

[예시 3]
이메일:
"안녕하세요. DEF 테크 박민준 대리입니다. 가격 문의드립니다. 저희 회사는 200명 규모입니다."

출력:
{{"company":"DEF 테크","contact_name":"박민준","contact_title":"대리","contact_phone":"미상","email":"미상","inquiry_type":"가격문의","service_interest":"미상","scale":"대규모(100명+)","urgency":"미상","summary":"DEF 테크 박민준 대리의 가격 문의. 200명 규모 회사."}}

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
            'service_interest': '미상', 'scale': '미상', 'urgency': '미상',
            'summary': email_body[:200],
        }

def generate_reply_draft(summary: str, contact_name: str = '') -> str:
    prompt = f"""아래 B2B 문의 요약에 대한 1차 회신 초안을 작성하세요.
- 250~350자 한국어
- 감사 인사 후, 미팅 전 파악이 필요한 추가 질문 2~3개 포함
- 부드럽고 전문적인 톤
- 이름을 알면 "OOO님" 형태로 호칭

[예시 1]
문의 요약: ABC 물류 김철수 과장, AI 재고관리 솔루션 도입 문의, 50명 규모, 긴급
회신 초안:
안녕하세요 김철수 과장님, ANTIEGG입니다. 소중한 문의 감사드립니다.
보다 정확한 안내를 드리기 위해 몇 가지 여쭤봐도 될까요?
1. 현재 사용 중인 재고관리 시스템이 있으신가요?
2. 가장 우선적으로 개선하고 싶으신 부분이 무엇인가요?
3. 예산 범위를 대략적으로 공유해 주실 수 있으신가요?
확인 후 빠르게 안내드리겠습니다. 감사합니다.

[예시 2]
문의 요약: XYZ 에이전시 이영희 대표, 공동 마케팅 파트너십 제안
회신 초안:
안녕하세요 이영희 대표님, ANTIEGG입니다. 파트너십 제안 주셔서 감사합니다.
구체적인 협의를 위해 몇 가지 확인드려도 될까요?
1. 어떤 형태의 공동 마케팅을 구상하고 계신가요?
2. 희망하시는 협력 시작 시점이 있으신가요?
3. 기존에 유사한 파트너십 진행 경험이 있으신지요?
검토 후 연락드리겠습니다. 감사합니다.

[실제 입력]
문의 요약: {summary}
회신 초안:"""

    return _call(prompt)

def generate_knock_draft(company: str, contact_name: str, stage: str) -> str:
    name_str = f'{contact_name}님' if contact_name and contact_name != '미상' else '담당자님'

    if stage == 'KNOCK_REPLY':
        prompt = f"""1차 회신 후 7일간 응답이 없는 고객에게 보낼 노크 메일 초안을 작성하세요.
- 150~200자 한국어
- 부담 없이 확인 요청하는 톤
- 추가 문의 있으면 편히 연락달라는 내용

회사명: {company}
담당자: {name_str}
노크 초안:"""
    else:
        prompt = f"""견적서 발송 후 7일간 응답이 없는 고객에게 보낼 노크 메일 초안을 작성하세요.
- 150~200자 한국어
- 견적 검토 결과 확인, 조건 조율 가능하다는 내용
- 부드러운 톤

회사명: {company}
담당자: {name_str}
노크 초안:"""

    return _call(prompt)
