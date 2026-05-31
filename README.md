# ANTIEGG B2B ADMIN

ANTIEGG의 B2B 문의 접수·제안·계약 전 과정을 운영하는 AI 기반 어드민.
**"판단은 사람, 실행은 AI"** 원칙 — AI는 초안·문서를 만들고, 발송·단계 전환은 사람이 확정한다. (1인 디렉터, 월 5건 규모 기준)

공개 주소: **https://antiegg-b2b.tail4297cb.ts.net** (Tailscale Funnel)

---

## 핵심 기능

- **문의 수집** — Gmail IMAP으로 B2B 라벨 메일을 폴링해 딜로 적재 (1시간 주기)
- **AI 답장 초안** — Qwen2.5-7B(few-shot)가 회신 초안 생성 → Gmail 임시보관함에 저장. 사람이 검토·발송
- **견적서·계약서** — HTML 미리보기 → 브라우저 인쇄(⌘P)로 PDF
- **전자서명** — UUID 토큰 기반 `/sign/{token}` 웹페이지
- **파이프라인 관리** — 인박스(오늘 할 일) · 칸반 파이프라인 · 회사별 이력
- **AI 사례 자동 학습** — 보낸편지함의 ANTIEGG 회신을 분류해 few-shot 사례로 자동 축적 (매일 03:00)

> Slack 알림은 코드만 있고 현재 **보류**(워크스페이스 앱 한도). 활성화 시 세팅에서 webhook 입력 예정.

## 스택

| 영역 | 사용 |
|------|------|
| 웹/어드민 | FastAPI + Jinja2 + HTMX |
| DB | SQLite (WAL 모드) |
| AI | llama.cpp(llama-server) + **Qwen2.5-7B-Instruct Q4_K_M**, CPU 모드 (M2 / macOS 13) |
| 메일 | Gmail **IMAP** — 수신 + 초안(Draft) 저장. SMTP 미사용(앱 비밀번호) |
| 스케줄러 | APScheduler |
| 외부 노출 | **Tailscale Funnel** (userspace, 무료·상시 HTTPS) |
| 서명 | 자체 UUID 토큰 + `/sign/{token}` |

> ⚠️ Qwen 7B는 CPU 추론이라 호출당 약 3~5분 소요. Metal GPU는 해당 환경에서 크래시가 있어 CPU 전용.

## 딜 단계 흐름

```
REVIEWING → REPLIED → NEGOTIATING → QUOTED → CONTRACTING → SIGNED → CLOSED_WON
REPLIED/QUOTED ─(7일 무응답)→ KNOCK_* ─(7일)→ CLOSED_LOST
```
단계 전환은 전부 **수동**(자동 전환 없음). 트리거 상태: `IDLE → PENDING → PROCESSING → DONE | DRAFT | ERROR`.

---

## 실행 / 배포

두 개의 LaunchAgent로 자동 기동된다(로그인 시 시작, KeepAlive).

| LaunchAgent | 역할 |
|-------------|------|
| `com.antiegg.b2b` | `scripts/server.sh` → llama-server(:8080) + uvicorn(:8000) + watchdog |
| `com.antiegg.tailscaled` | Tailscale 데몬(userspace) → Funnel로 :8000 공개 |

```bash
# 수동 시작
cd ~/antiegg-b2b && ./scripts/start.sh

# 서버 재시작 (라우트 변경 반영)
launchctl unload ~/Library/LaunchAgents/com.antiegg.b2b.plist
launchctl load   ~/Library/LaunchAgents/com.antiegg.b2b.plist
```

> **`main.py` 라우트를 바꾼 뒤엔 반드시 reload.** Jinja 템플릿은 디스크에서 hot-reload 되지만 라우트는 프로세스 시작 시 고정된다 — reload 없이는 신규 라우트가 404, 새 템플릿만 보이는 mixed-signal 상태가 된다. 재기동 시 llama 콜드 로딩 ~1분.

### 외부 노출 (Tailscale Funnel)

`APP_BASE_URL`(.env)에 ts.net 주소가 고정되어 있고, Funnel 설정은 tailscaled statedir에 영구 저장돼 데몬 재시작 시 자동 복구된다. 인증서는 자동(Let's Encrypt).

```bash
tailscale --socket=/opt/homebrew/var/run/tailscaled.socket funnel status
```
> 이 맥에서 직접 `curl https://...ts.net`은 로컬 DNS 특성상 실패할 수 있다. 외부 도달 확인은 다른 망(LTE 등)에서. 커스텀 도메인(b2b.antiegg.kr)은 Funnel로는 불가 — 필요 시 Cloudflare Tunnel/유료 터널로 전환해야 한다.

## 자동 운영 잡 (APScheduler)

| 시각 | 작업 |
|------|------|
| 매시 | Gmail 인박스 폴링 → 신규 딜 |
| 5분 | 트리거 처리(reply / quote / contract / knock) |
| 09:00 | 무응답 7일 → 노크 / 노크 후 7일 → CLOSED_LOST |
| 03:00 | Gmail 보낸편지함 few-shot 사례 자동 수집 |
| 03:30 | SQLite + ai_context 백업 (iCloud) |
| 03:45 | 30일 지난 에러 로그 정리 |
| 03:50 | 로그 회전 (copytruncate, 1MB↑, 14일 보관) |

### llama-server watchdog
`scripts/server.sh`의 `llama_watchdog`이 60초 주기로 `kill -0`로 프로세스 생존을 확인, 죽으면 자동 재시작. (헬스 200은 판정에 안 씀 — CPU 추론 3~5분 busy를 죽음으로 오인 방지)

## 데이터 임포트

`scripts/notion_import.py` — 노션 "B2B 대시보드 상세" DB를 deals로 적재.
```bash
python3 scripts/notion_import.py          # 진단(읽기 전용)
python3 scripts/notion_import.py --apply  # 기존 wipe 후 적재
```
`.env`의 `NOTION_TOKEN` / `NOTION_DB_ID` 사용. `notion_page_id` 컬럼으로 재동기화 대비.

---

## 운영 문서

종합 인수인계: [`docs/HANDOVER.md`](docs/HANDOVER.md)

비밀·데이터는 git에서 제외된다 — `.env`(토큰·앱 비밀번호), `b2b.db`(고객 데이터)는 `.gitignore`.
