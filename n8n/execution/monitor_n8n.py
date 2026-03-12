#!/usr/bin/env python3
"""
monitor_n8n.py - مراقب تنفيذ Workflows في n8n
------------------------------------------------
يستعلم عن آخر executions ويعرض ملخصاً بالأخطاء والنجاحات.

الاستخدام:
  python execution/monitor_n8n.py              # آخر 20 تنفيذ
  python execution/monitor_n8n.py --errors     # الأخطاء فقط
  python execution/monitor_n8n.py --limit 50   # آخر 50 تنفيذ
  python execution/monitor_n8n.py --workflow "SwanArt - معالجة الطلبات"
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
import urllib.request
import urllib.error

BASE_DIR = Path(__file__).parent.parent
from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

N8N_BASE_URL  = os.getenv("N8N_BASE_URL", "https://babylonai.me")
N8N_API_KEY   = os.getenv("N8N_API_KEY", "")

# ─── ألوان للطرفية ───────────────────────────────────────────────
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


def api_get(path: str) -> dict:
    """استدعاء n8n REST API."""
    url = f"{N8N_BASE_URL}/api/v1{path}"
    headers = {
        "X-N8N-API-KEY": N8N_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (n8n-monitor/1.0)"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"لا يمكن الاتصال بـ n8n: {e.reason}")


def format_time(iso_str: str) -> str:
    """تحويل ISO timestamp إلى وقت محلي مقروء."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local = dt.astimezone()
        return local.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_str[:19]


def format_duration(ms) -> str:
    """تحويل milliseconds إلى نص مقروء."""
    if not ms:
        return "—"
    if ms < 1000:
        return f"{ms}ms"
    if ms < 60000:
        return f"{ms/1000:.1f}s"
    return f"{ms/60000:.1f}m"


def status_icon(status: str) -> str:
    icons = {
        "success":  f"{GREEN}✅ نجح{RESET}",
        "error":    f"{RED}❌ خطأ{RESET}",
        "running":  f"{YELLOW}⏳ يعمل{RESET}",
        "waiting":  f"{BLUE}⏸  انتظار{RESET}",
        "canceled": f"{YELLOW}⏹  ملغي{RESET}",
    }
    return icons.get(status, status)


def get_workflows() -> dict:
    """جلب قائمة الـ workflows وبناء dict: name → id."""
    data = api_get("/workflows?limit=100")
    return {wf["name"]: wf["id"] for wf in data.get("data", [])}


def get_executions(workflow_id=None, limit: int = 20, errors_only: bool = False) -> list:
    """جلب آخر executions."""
    params = f"?limit={limit}&includeData=false"
    if workflow_id:
        params += f"&workflowId={workflow_id}"
    if errors_only:
        params += "&status=error"
    data = api_get(f"/executions{params}")
    return data.get("data", [])


def get_execution_detail(exec_id: str) -> dict:
    """جلب تفاصيل execution واحد بما في ذلك الخطأ."""
    return api_get(f"/executions/{exec_id}?includeData=true")


_WF_CACHE = {}

def get_workflow_name(wf_id: str) -> str:
    """جلب اسم الـ workflow من ID مع cache."""
    if not wf_id:
        return "—"
    if wf_id not in _WF_CACHE:
        try:
            wf = api_get(f"/workflows/{wf_id}")
            _WF_CACHE[wf_id] = wf.get("name", wf_id)
        except Exception:
            _WF_CACHE[wf_id] = wf_id
    return _WF_CACHE[wf_id]


def print_executions_table(executions: list, detailed_errors: bool = False):
    """طباعة جدول التنفيذات."""
    if not executions:
        print(f"\n{YELLOW}لا توجد تنفيذات.{RESET}\n")
        return

    print(f"\n{BOLD}{'─'*80}{RESET}")
    print(f"{BOLD}{'ID':<12} {'الحالة':<20} {'الـ Workflow':<35} {'الوقت':<20} {'المدة'}{RESET}")
    print(f"{'─'*80}")

    errors_found = []

    for ex in executions:
        exec_id   = str(ex.get("id", ""))[:10]
        status    = ex.get("status", "—")
        wf_name   = (ex.get("workflowData", {}) or {}).get("name") or get_workflow_name(ex.get("workflowId", ""))
        wf_name   = wf_name[:33]
        started   = format_time(ex.get("startedAt", ""))
        duration  = format_duration(ex.get("stoppedAt") and ex.get("startedAt") and
                    int((datetime.fromisoformat(ex["stoppedAt"].replace("Z","+00:00")) -
                         datetime.fromisoformat(ex["startedAt"].replace("Z","+00:00"))).total_seconds() * 1000)
                    if ex.get("stoppedAt") and ex.get("startedAt") else None)

        print(f"{exec_id:<12} {status_icon(status):<28} {wf_name:<35} {started:<20} {duration}")

        if status == "error":
            errors_found.append(ex)

    print(f"{'─'*80}\n")

    # تفاصيل الأخطاء
    if errors_found and detailed_errors:
        print(f"\n{BOLD}{RED}═══ تفاصيل الأخطاء ═══{RESET}")
        for ex in errors_found[:5]:  # أول 5 أخطاء
            full       = get_execution_detail(str(ex["id"]))
            result     = (full.get("data") or {}).get("resultData") or {}
            error      = result.get("error") or {}
            last_node  = result.get("lastNodeExecuted") or "—"
            error_msg  = error.get("description") or error.get("message") or "لا توجد رسالة"
            error_node = (error.get("node") or {}).get("name") or last_node

            print(f"\n{RED}● Execution {ex['id']}{RESET}")
            print(f"  الـ Workflow : {ex.get('workflowData', {}).get('name', '—')}")
            print(f"  العقدة      : {error_node}")
            print(f"  الخطأ       : {error_msg}")
            print(f"  الوقت       : {format_time(ex.get('startedAt', ''))}")
            print(f"  الرابط      : {N8N_BASE_URL}/workflow/{ex.get('workflowId')}/executions/{ex['id']}")

    return errors_found


def main():
    if not N8N_API_KEY:
        print(f"\n{RED}❌ N8N_API_KEY غير موجود في .env{RESET}")
        print("أضف مفتاح API من: https://babylonai.me/settings/api")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="مراقب n8n Workflows")
    parser.add_argument("--errors",    action="store_true", help="أظهر الأخطاء فقط")
    parser.add_argument("--limit",     type=int, default=20, help="عدد التنفيذات (افتراضي: 20)")
    parser.add_argument("--workflow",  type=str, default=None, help="فلتر باسم الـ workflow")
    parser.add_argument("--workflows", action="store_true", help="أظهر قائمة الـ workflows")
    args = parser.parse_args()

    print(f"\n{BOLD}🔍 SwanArt — n8n Monitor{RESET}")
    print(f"الخادم: {N8N_BASE_URL}\n")

    # عرض قائمة الـ workflows
    if args.workflows:
        print(f"{BOLD}الـ Workflows المتاحة:{RESET}")
        try:
            wfs = get_workflows()
            for name, wid in wfs.items():
                print(f"  {BLUE}{wid}{RESET}  {name}")
        except RuntimeError as e:
            print(f"{RED}خطأ: {e}{RESET}")
        print()
        return

    # جلب executions
    workflow_id = None
    if args.workflow:
        try:
            wfs = get_workflows()
            workflow_id = wfs.get(args.workflow)
            if not workflow_id:
                print(f"{YELLOW}⚠️ لم يُعثر على workflow بالاسم: {args.workflow}{RESET}")
                print(f"الـ workflows المتاحة: {', '.join(wfs.keys())}\n")
                sys.exit(1)
        except RuntimeError as e:
            print(f"{RED}خطأ: {e}{RESET}")
            sys.exit(1)

    try:
        executions = get_executions(
            workflow_id=workflow_id,
            limit=args.limit,
            errors_only=args.errors
        )
        label = f"آخر {args.limit} تنفيذ"
        if args.errors:
            label = f"آخر {args.limit} تنفيذ فاشل"
        if args.workflow:
            label += f" — {args.workflow}"

        print(f"{BOLD}{label}:{RESET}")
        errors = print_executions_table(executions, detailed_errors=True)

        total   = len(executions)
        success = sum(1 for e in executions if e.get("status") == "success")
        error   = sum(1 for e in executions if e.get("status") == "error")

        print(f"{BOLD}الملخص:{RESET} المجموع {total} | {GREEN}نجح {success}{RESET} | {RED}خطأ {error}{RESET}")
        print()

    except RuntimeError as e:
        print(f"{RED}❌ {e}{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
