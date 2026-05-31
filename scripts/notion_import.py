#!/usr/bin/env python3
"""Notion 'B2B 대시보드 상세' → b2b.db deals 임포터.

기본은 --inspect (읽기 전용 진단). 실제 적재는 --apply.
토큰/DB ID는 .env의 NOTION_TOKEN / NOTION_DB_ID 사용.
"""
import os, sys, json, urllib.request, urllib.error, uuid, sqlite3
from collections import Counter

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(PROJECT, ".env")
DB = os.path.join(PROJECT, "b2b.db")
NOTION_VERSION = "2022-06-28"


def load_env():
    d = {}
    with open(ENV) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k] = v.strip().strip('"').strip("'")
    return d


def api(path, token, method="GET", body=None):
    url = "https://api.notion.com/v1/" + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Notion-Version", NOTION_VERSION)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def fetch_all(db_id, token):
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        d = api(f"databases/{db_id}/query", token, "POST", body)
        rows.extend(d["results"])
        if not d.get("has_more"):
            break
        cursor = d["next_cursor"]
    return rows


# ---- Notion property 값 추출 ----
def txt(arr):
    return "".join(t.get("plain_text", "") for t in (arr or [])).strip()


def prop(p):
    if p is None:
        return None
    t = p["type"]
    v = p[t]
    if t in ("rich_text", "title"):
        return txt(v)
    if t == "select":
        return v["name"] if v else None
    if t == "status":
        return v["name"] if v else None
    if t == "multi_select":
        return ", ".join(o["name"] for o in v)
    if t == "phone_number":
        return v
    if t == "number":
        return v
    if t == "checkbox":
        return v
    if t == "url":
        return v
    if t == "date":
        return v["start"] if v else None
    if t == "people":
        names = [x.get("name", "").strip() for x in v if x.get("name", "").strip()]
        return ", ".join(names) if names else None
    if t == "files":
        return len(v)
    if t == "formula":
        f = v[v["type"]]
        return f
    return None


def get(page, name):
    return prop(page["properties"].get(name))


# 현황(노션) → stage(deals).  None(미지정) → CLOSED_LOST (아카이브 처리)
STAGE_MAP = {
    "문의 인입": "REVIEWING",
    "회신 완료": "REPLIED",
    "의견 조율": "NEGOTIATING",
    "제안 예정": "REVIEWING",
    "제안 완료": "QUOTED",
    "담당 에디터 연계": "NEGOTIATING",
    "제휴 진행 중": "CONTRACTING",
    "진행 완료": "CLOSED_WON",
    "제안 거절": "CLOSED_LOST",
    None: "CLOSED_LOST",
}


def build_summary(g):
    """deals 스키마에 칼럼이 없는 노션 필드를 손실 없이 요약 텍스트로 묶는다."""
    lines = []
    pairs = [
        ("발행일", g("발행일")),
        ("공급가액(VAT별도)", g("공급가액(vat별도)")),
        ("입금", "완료" if g("입금 여부") else None),
        ("정산", "완료" if g("정산 여부") else None),
        ("세금계산서", g("세금계산서(승인번호)")),
        ("EDITOR", g("EDITOR")),
        ("PM", g("PM")),
        ("BD", g("BD")),
        ("견적서", g("견적서")),
        ("계약서", g("계약서")),
        ("Submission ID", g("Submission ID")),
    ]
    for k, v in pairs:
        if v not in (None, "", 0):
            lines.append(f"{k}: {v}")
    return "\n".join(lines)


def yyyymm(date_str):
    if date_str and len(date_str) >= 7:
        return date_str[:4] + date_str[5:7]
    return "000000"


def main():
    apply = "--apply" in sys.argv
    backfill = "--backfill" in sys.argv
    env = load_env()
    token = env.get("NOTION_TOKEN")
    db_id = env.get("NOTION_DB_ID")
    if not token or not db_id:
        print("NOTION_TOKEN / NOTION_DB_ID 누락"); sys.exit(1)

    rows = fetch_all(db_id, token)
    print(f"총 {len(rows)}행 수신\n")

    status_dist = Counter()
    no_company = 0
    mapped = []
    for pg in rows:
        company = get(pg, "제휴처")
        status = get(pg, "현황")
        status_dist[status] += 1
        if not company:
            no_company += 1
        mapped.append({
            "company": company,
            "contact_name": get(pg, "담당자명"),
            "contact_phone": get(pg, "담당자 연락처"),
            "email": get(pg, "담당자 이메일"),
            "service_interest": get(pg, "상품"),
            "service_option": get(pg, "상품 옵션"),
            "status_notion": status,
            "stage": STAGE_MAP.get(status, "REVIEWING"),
            "inquiry_date": get(pg, "문의일"),
            "amount": get(pg, "공급가액(vat별도)"),
            "quote_url": get(pg, "견적서"),
            "contract_url": get(pg, "계약서"),
            "outbound": get(pg, "아웃바운드"),
        })

    print("=== 현황(노션) 분포 → 매핑된 stage ===")
    for s, c in status_dist.most_common():
        print(f"  {c:3d}  {s!r:24s} → {STAGE_MAP.get(s, 'REVIEWING')}")
    print(f"\n제휴처(회사명) 비어있는 행: {no_company}")
    with_email = sum(1 for m in mapped if m["email"])
    print(f"이메일 있는 행: {with_email} / {len(mapped)}")

    print("\n=== 샘플 5건 ===")
    for m in mapped[:5]:
        print(f"  - {m['company']!r} | {m['contact_name']} | {m['email']} | "
              f"{m['status_notion']}→{m['stage']} | {m['service_interest']}")

    if backfill:
        # 비파괴 백필: 견적서가 쓰는 cond_service_name/desc를 노션 상품/상품옵션으로 채움.
        # notion_page_id로 매칭, 비어있는 칸만(수동 편집 보존), updated_at 등 다른 필드 무변경.
        conn = sqlite3.connect(DB, timeout=10)
        conn.execute("PRAGMA busy_timeout=10000")
        filled = 0
        for pg in rows:
            prod = get(pg, "상품")
            opt = get(pg, "상품 옵션")
            if prod and opt:
                name, desc = prod, opt
            elif prod:
                name, desc = prod, ""
            elif opt:
                name, desc = opt, ""
            else:
                continue
            cur = conn.execute(
                "UPDATE deals SET cond_service_name=?, cond_service_desc=? "
                "WHERE notion_page_id=? "
                "AND (cond_service_name IS NULL OR cond_service_name='')",
                (name, desc, pg["id"]),
            )
            filled += cur.rowcount
        conn.commit()
        total = conn.execute(
            "SELECT COUNT(*) FROM deals WHERE cond_service_name IS NOT NULL AND cond_service_name!=''"
        ).fetchone()[0]
        conn.close()
        print(f"\n[backfill] cond_service_name/desc 신규 채움: {filled}건")
        print(f"cond_service_name 채워진 총: {total}건")
        return

    if not apply:
        print("\n[inspect 모드] 적재 안 함. 실제 적재는 --apply (백필만 하려면 --backfill)")
        return

    # ---- 적재 ----
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    # 재동기화용 컬럼
    try:
        conn.execute("ALTER TABLE deals ADD COLUMN notion_page_id TEXT")
    except sqlite3.OperationalError:
        pass

    # 기존(테스트) 데이터 제거 — settings/errors/reply 사례는 보존
    before = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    conn.execute("DELETE FROM deals")
    conn.execute("DELETE FROM activities")
    conn.execute("DELETE FROM counter")
    print(f"\n기존 deals {before}건 삭제 + activities/counter 초기화")

    # 생성 문서 파일 정리
    out = os.path.join(PROJECT, "app", "services", "output")
    removed = 0
    if os.path.isdir(out):
        for f in os.listdir(out):
            if f.endswith((".docx", ".pdf", ".html")):
                os.remove(os.path.join(out, f)); removed += 1
    print(f"생성 문서 파일 {removed}건 삭제")

    # 날짜 오름차순 정렬 후 월별 시퀀스로 deal_id 부여
    def keyfn(pg):
        g = lambda n: prop(pg["properties"].get(n))
        return g("문의일") or g("발행일") or ""
    rows_sorted = sorted(rows, key=keyfn)

    seq = {}
    now = __import__("datetime").datetime.now().isoformat()
    ym_max = {}
    inserted = 0
    for pg in rows_sorted:
        g = lambda n: prop(pg["properties"].get(n))
        inq = g("문의일") or g("발행일")
        ym = yyyymm(inq)
        seq[ym] = seq.get(ym, 0) + 1
        ym_max[ym] = seq[ym]
        deal_id = f"AE-{ym}-{seq[ym]:03d}"
        status = g("현황")
        svc = g("상품") or ""
        opt = g("상품 옵션")
        if opt:
            svc = (svc + " / " + opt).strip(" /")
        amount = g("공급가액(vat별도)")
        rec = {
            "deal_id": deal_id,
            "company": g("제휴처") or "(미상)",
            "contact_name": g("담당자명"),
            "contact_phone": g("담당자 연락처"),
            "email": g("담당자 이메일"),
            "inquiry_type": "아웃바운드" if g("아웃바운드") else "인바운드",
            "service_interest": svc or None,
            "stage": STAGE_MAP.get(status, "REVIEWING"),
            "summary": build_summary(g) or None,
            "cond_unit_price": str(amount) if amount not in (None, "") else None,
            "created_at": inq or now,
            "updated_at": now,
            "notion_page_id": pg["id"],
        }
        conn.execute("""
            INSERT INTO deals (deal_id, company, contact_name, contact_phone, email,
                inquiry_type, service_interest, stage, summary, cond_unit_price,
                created_at, updated_at, notion_page_id)
            VALUES (:deal_id, :company, :contact_name, :contact_phone, :email,
                :inquiry_type, :service_interest, :stage, :summary, :cond_unit_price,
                :created_at, :updated_at, :notion_page_id)
        """, rec)
        inserted += 1

    # counter 동기화 — 이후 수동 딜이 같은 월에서 이어지도록
    for ym, n in ym_max.items():
        conn.execute("INSERT INTO counter (year_month, last_number) VALUES (?, ?)", (ym, n))

    conn.commit()
    print(f"\n적재 완료: {inserted}건")
    print("=== 적재 후 stage 분포 ===")
    for r in conn.execute("SELECT stage, COUNT(*) c FROM deals GROUP BY stage ORDER BY c DESC"):
        print(f"  {r['c']:3d}  {r['stage']}")
    conn.close()


if __name__ == "__main__":
    main()
