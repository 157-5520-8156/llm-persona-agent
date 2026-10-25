"""Authenticated, read-only browser shell for the owner World v2 Dashboard.

The browser reads one versioned owner snapshot and renders only fields already
authorized by that contract.  It never reads process health, raw World domain
refs, or a second World host, and it does not infer a room scene when the owner
reports stale or unavailable state.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlsplit

from .dashboard_explorer_ui import EXPLORER_CSS, EXPLORER_HTML, EXPLORER_JS


DASHBOARD_SESSION_COOKIE = "girl_agent_v2_dashboard_session"
DASHBOARD_SESSION_TTL_SECONDS = 8 * 60 * 60


def dashboard_http_origin(value: str) -> tuple[str, str, int] | None:
    """Parse the exact origin accepted by both dashboard HTTP hosts."""

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if (
        scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return None
    return scheme, host, port or (443 if scheme == "https" else 80)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


class DashboardSessionCodec:
    """Issue instance-bound sessions without placing the operator token in a cookie."""

    def __init__(self, *, operator_token: str, instance_secret: bytes) -> None:
        if not operator_token.strip():
            raise ValueError("dashboard session requires a configured operator token")
        if len(instance_secret) < 32:
            raise ValueError("dashboard session instance secret must be at least 32 bytes")
        self._key = hmac.new(
            instance_secret,
            b"world-v2-dashboard-session\0" + operator_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()

    def issue(self, *, now: int | None = None) -> str:
        issued_at = int(time.time()) if now is None else now
        expires_at = issued_at + DASHBOARD_SESSION_TTL_SECONDS
        nonce = _b64(secrets.token_bytes(18))
        body = f"v1.{expires_at}.{nonce}"
        signature = _b64(hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest())
        return f"{body}.{signature}"

    def verify(self, value: str | None, *, now: int | None = None) -> bool:
        if not value or len(value) > 512:
            return False
        parts = value.split(".")
        if len(parts) != 4 or parts[0] != "v1":
            return False
        try:
            expires_at = int(parts[1])
        except ValueError:
            return False
        current = int(time.time()) if now is None else now
        if expires_at <= current or expires_at > current + DASHBOARD_SESSION_TTL_SECONDS:
            return False
        body = ".".join(parts[:3])
        expected = _b64(hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest())
        return hmac.compare_digest(expected, parts[3])


LOGIN_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>知栀看板登录</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at 50% 18%,#e9efe2 0,#f4f4ed 62%);color:#263d36;font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif}
main{width:min(420px,calc(100% - 32px));padding:32px;background:#fffef9;border:1px solid #dce2d7;border-radius:20px;box-shadow:0 12px 36px rgba(38,61,54,.07)}
h1{font-size:21px;font-weight:600;margin:0 0 8px;letter-spacing:.03em}label,input,button{display:block;width:100%}label{font-size:12px;color:#687970;margin-top:16px}input,button{margin-top:8px;padding:12px 14px;font:inherit;font-size:13px;box-sizing:border-box;border-radius:10px}input{border:1px solid #dce2d7;background:#fafbf6;color:#263d36}button{margin-top:18px;background:#315b4a;color:#fffef9;border:0;font-weight:500;cursor:pointer}p{line-height:1.7;font-size:13px;color:#687970;margin:0}
</style></head><body><main><h1>知栀看板</h1>
<p>请输入本机配置的 operator token。凭证只通过本次 POST 提交，不会写入 URL、页面脚本或浏览器存储。</p>
<form method="post" action="/world-v2/dashboard/session" autocomplete="off">
<label for="operator-token">Operator token</label><input id="operator-token" name="operator_token" type="password" required autocomplete="current-password">
<button type="submit">打开看板</button></form></main></body></html>"""


UNAVAILABLE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>知栀看板还没准备好</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f4ed;color:#263d36;font-family:"PingFang SC",system-ui,sans-serif}main{max-width:420px;padding:28px 32px;background:#fffef9;border:1px solid #dce2d7;border-radius:18px;box-shadow:0 8px 28px rgba(38,61,54,.05)}h1{font-size:19px;margin:0 0 8px}p{margin:0;font-size:13px;line-height:1.7;color:#687970}</style></head><body>
<main><h1>看板还没准备好</h1><p>只读 World v2 host 尚未初始化。不会回退到旧运行时。</p></main>
</body></html>"""


DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>沈知栀 · 生活现场</title><style>
:root{font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;color:#263d36;background:#f4f4ed;font-synthesis:none;--ink:#263d36;--muted:#66786e;--line:#dce2d7;--line-strong:#c9d4c2;--paper:#fffef9;--paper-tint:#f7f9f2;--green:#315b4a;--green-deep:#234537;--soft:#e7eee2;--amber:#835e25;--shadow:0 6px 24px rgba(38,61,54,.045)}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 50% 0,#e9efe2 0,#f4f4ed 520px);color:var(--ink)}button,select,input{font:inherit}button,a,select{-webkit-tap-highlight-color:transparent}button,select{cursor:pointer}button:focus-visible,a:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #a4b89a;outline-offset:3px}[hidden]{display:none!important}
.bar{position:sticky;top:0;z-index:40;background:rgba(244,244,237,.86);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-bottom:1px solid rgba(201,212,194,.75);padding:16px 36px;display:flex;align-items:center;justify-content:space-between;gap:24px}.bar>*{max-width:1440px}.identity{display:flex;gap:14px;align-items:center}.monogram{display:grid;place-items:center;width:42px;height:42px;background:linear-gradient(145deg,#396855,#284c3d);color:#fffef2;border-radius:13px;font-size:21px;font-family:"Songti SC","STSong",serif;box-shadow:0 3px 10px rgba(49,91,74,.2)}.bar h1{font-size:18px;font-weight:600;letter-spacing:.04em;margin:0}.eyebrow{margin:4px 0 0;color:var(--muted);font-size:10px;letter-spacing:.18em;font-weight:500}.bar-actions{display:flex;align-items:center;gap:10px}.button{border:1px solid var(--line-strong);background:var(--paper);border-radius:10px;padding:8px 14px;font-size:12px;font-weight:500;color:var(--ink);box-shadow:0 1px 2px rgba(38,61,54,.04)}.button.primary{background:var(--green);color:#fffef9;border-color:var(--green);box-shadow:0 2px 6px rgba(49,91,74,.18)}.logout{margin:0}.logout button{background:transparent;border:1px solid transparent;border-radius:10px;padding:8px 12px;color:var(--muted);font:12px inherit;cursor:pointer}.logout button:hover{background:rgba(38,61,54,.05);color:var(--ink)}
.status-bar{max-width:1400px;margin:18px auto 0;padding:10px 18px;background:rgba(255,254,249,.82);border:1px solid var(--line);border-radius:14px;display:flex;align-items:center;justify-content:space-between;gap:16px;font-size:12px;color:var(--muted);box-shadow:0 2px 10px rgba(38,61,54,.025)}.status-meta{display:flex;align-items:center;gap:14px;flex-wrap:wrap}.capture-state{display:inline-flex;align-items:center;gap:7px;color:var(--green);background:var(--soft);padding:4px 11px;border-radius:999px;font-weight:500;font-size:11px}.capture-state:before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}.capture-state.error{color:#914b38;background:#f5e4dc}.header-clock{margin:0;font-variant-numeric:tabular-nums;color:var(--ink);font-weight:500}.readonly{letter-spacing:.03em;font-size:11px;color:var(--muted)}.capture-notice{max-width:1400px;margin:14px auto 0;padding:12px 18px;background:#f6ead7;border:1px solid #e8d5b4;border-radius:12px;color:#754e29;font-size:13px;line-height:1.6}.recording-tools{max-width:1400px;margin:14px auto 0;padding:10px 18px;background:#eef3e9;border:1px solid #d4dfd0;border-radius:12px;display:flex;align-items:center;gap:12px;font-size:12px;color:var(--ink)}.recording-tools select{background:var(--paper);border:1px solid var(--line-strong);border-radius:8px;padding:6px 28px 6px 12px;color:var(--ink)}
.wrap{max-width:1440px;margin:auto;padding:18px 0 52px;display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:20px;align-items:stretch}.wrap>*{grid-column:1/-1}.status-bar,.capture-notice,.recording-tools,.wrap{width:calc(100% - 56px)}.panel,.section-card{background:var(--paper);border:1px solid var(--line);border-radius:20px;box-shadow:var(--shadow);overflow:hidden;min-width:0}
.now-panel{grid-column:1/-1;display:grid;grid-template-columns:minmax(210px,245px) minmax(0,1fr);padding:22px 26px;gap:24px;background:linear-gradient(125deg,#e5ede0 0%,#f5f8ef 48%,#fffef9 100%);border-color:#d1dccb;align-items:stretch}.now-panel>div:first-child{display:flex;flex-direction:column;justify-content:space-between;padding-right:18px;border-right:1px solid rgba(49,91,74,.12)}.hero-label{display:inline-block;font-size:10px;font-weight:600;letter-spacing:.16em;color:var(--green);background:rgba(49,91,74,.09);padding:4px 10px;border-radius:999px;margin:0 0 10px;width:fit-content}.now-panel h2{font-size:30px;font-weight:600;letter-spacing:.03em;margin:0;color:var(--green-deep)}.hero-note{margin:8px 0 0;color:var(--muted);line-height:1.7;font-size:12px}.world-clock{font-size:11.5px;color:var(--green-deep);background:rgba(255,254,249,.78);border:1px solid rgba(49,91,74,.14);border-radius:10px;padding:7px 11px;line-height:1.45;margin:14px 0 0;font-variant-numeric:tabular-nums;font-weight:500}.now-story{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;align-content:stretch}.now-line{min-width:0;background:rgba(255,254,249,.9);border:1px solid #d7e1d1;border-top:3px solid #7c9a82;border-radius:13px;padding:12px 15px;display:flex;flex-direction:column;justify-content:flex-start;box-shadow:0 2px 6px rgba(38,61,54,.025)}.now-line .k{display:block;font-size:10.5px;font-weight:600;letter-spacing:.04em;color:#556b5e}.now-line .v{display:block;margin-top:5px;font-size:13.5px;font-weight:550;color:var(--ink);line-height:1.5;overflow-wrap:anywhere;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden}.now-line .d{display:block;margin-top:5px;padding-top:5px;border-top:1px dashed #e5ece0;color:var(--muted);font-size:11.5px;line-height:1.5;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}.now-line.missing{border-top-color:#cbd4c5;background:rgba(250,251,246,.65)}.now-line.missing .v{color:var(--muted);font-size:12.5px;font-weight:400}
#agendaPanel{grid-column:1/-1}.domain-details{grid-column:1/-1;min-width:0}.footer{grid-column:1/-1}
.section-grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:20px;align-items:stretch}.section-card{grid-column:span 4;padding:22px 24px;display:flex;flex-direction:column;min-height:0}.section-card[data-focus-area="life"],.section-card[data-focus-area="pending"]{grid-column:1/-1}.section-head{display:flex;justify-content:space-between;gap:12px;align-items:center;padding-bottom:12px;border-bottom:1px solid #eaefe5}.section-head h2{font-size:17px;font-weight:600;margin:0;letter-spacing:.02em;color:var(--green-deep)}.section-state{border-radius:999px;padding:4px 10px;background:var(--soft);color:var(--green);font-size:11px;font-weight:500;white-space:nowrap}.section-state[data-state="unavailable"],.section-state[data-state="degraded"],.section-state[data-state="stale"]{background:#f4e3d7;color:#91523a}.section-state[data-state="empty"],.section-state[data-state="disabled"]{background:#eeeee7;color:var(--muted)}.section-body{padding-top:14px;display:flex;flex-direction:column;gap:14px;flex:1;min-height:0}.section-note{font-size:12px;color:var(--muted);line-height:1.65;margin:0}.empty-state{padding:24px 16px;border:1px dashed var(--line-strong);border-radius:12px;background:#fafbf6;color:var(--muted);font-size:12.5px;line-height:1.7;text-align:center}.section-subtitle{font-size:12px;color:#4f6558;font-weight:600;letter-spacing:.04em;margin:0 0 10px}.life-columns{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px;align-items:stretch}.life-column{min-width:0;background:#fafbf6;border:1px solid #e6ece0;border-radius:14px;padding:16px;display:flex;flex-direction:column}.activity-list,.highlight-list{display:grid;gap:10px;max-height:420px;overflow-y:auto;padding-right:4px;align-content:start}.section-card[data-focus-area="relationships"] .highlight-list{grid-template-columns:repeat(2,minmax(0,1fr))}.section-card[data-focus-area="relationships"] .highlight-list>.highlight:first-child{grid-column:1/-1}.section-card[data-focus-area="pending"] .highlight-list{grid-template-columns:repeat(auto-fill,minmax(260px,1fr));max-height:280px}.highlight{padding:13px 14px;background:var(--paper);border:1px solid #e1e8dc;border-radius:12px;min-width:0;box-shadow:0 1px 3px rgba(38,61,54,.02)}.highlight-top{display:flex;justify-content:space-between;align-items:baseline;gap:8px}.kind-tag{color:var(--green);background:var(--soft);padding:2px 7px;border-radius:5px;font-size:10px;font-weight:500;letter-spacing:.03em}.when{font-size:10.5px;color:var(--muted);font-variant-numeric:tabular-nums}.highlight h3{font-size:13.5px;line-height:1.55;font-weight:550;margin:6px 0 0;overflow-wrap:anywhere;color:var(--ink)}.status-pill{display:inline-block;margin-top:6px;border-radius:5px;padding:2px 7px;background:var(--soft);font-size:10px;font-weight:500;color:var(--green)}.highlight[data-status="planned"] .status-pill,.highlight[data-status="pending"] .status-pill{background:#f2ead8;color:var(--amber)}.highlight .detail{font-size:11.5px;line-height:1.65;color:var(--muted);margin:6px 0 0;overflow-wrap:anywhere}.chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}.chip{font-size:10px;line-height:1.35;color:#54675c;background:#eef2e9;border:1px solid #dfe6d9;border-radius:5px;padding:2px 6px;overflow-wrap:anywhere}.timeline{list-style:none;padding:0;margin:0;display:grid;gap:10px}.timeline li{display:grid;grid-template-columns:10px minmax(0,1fr);gap:12px;align-items:start}.timeline-dot{margin-top:18px;width:8px;height:8px;border-radius:50%;background:#6e8d77;box-shadow:0 0 0 3px #e6eee2}.timeline .highlight{background:var(--paper);border:1px solid #e3e9dd;padding:12px 14px;border-radius:12px}.timeline h3{margin-top:6px}.timeline-caption{font-size:11.5px;color:var(--muted);margin:0 0 10px;line-height:1.7}
.metric-grid,.signal-grid,.notice-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px}.metric,.signal,.notice{padding:10px 12px;border:1px solid #e2e8dc;border-radius:10px;background:#fafbf6;min-width:0}.metric span,.signal span,.notice span{display:block;color:var(--muted);font-size:10px;line-height:1.35}.metric strong,.signal strong,.notice strong{display:block;margin-top:4px;font-size:18px;font-weight:600;color:var(--green-deep);font-variant-numeric:tabular-nums}.signal strong,.notice strong{font-size:12px;line-height:1.5;font-weight:500}.zero-metrics,.section-contract{border-top:1px dashed var(--line);padding-top:10px;margin-top:auto}.zero-metrics summary,.section-contract summary{font-size:11px;color:var(--muted);cursor:pointer;user-select:none}.zero-metrics .metric-grid{margin-top:8px}.truncated{font-size:11px;color:var(--muted);margin:auto 0 0;line-height:1.6}.operator-details{grid-column:1/-1;padding:16px 22px;background:var(--paper);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}.operator-details>summary{font-size:13px;font-weight:550;color:var(--ink);cursor:pointer;display:flex;align-items:center;justify-content:space-between}.operator-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;padding-top:16px}.operator-grid .section-card{grid-column:span 1;box-shadow:none;background:#fafbf6}.meta{display:grid;grid-template-columns:auto 1fr;gap:8px 18px;font-size:11.5px;color:var(--muted);margin:0}.meta dt{font-weight:500;color:var(--ink)}.meta dd{margin:0;overflow-wrap:anywhere}.footer{font-size:11.5px;color:var(--muted);display:flex;justify-content:space-between;gap:16px;line-height:1.8;padding:4px 6px}.footer a{color:inherit}
body.recording .operator-only{display:none!important}body.recording .bar{padding-top:22px}body.recording .wrap{gap:28px}body.recording .hero-note,body.recording .section-note{font-size:14px}body.recording .now-line .v{font-size:18px}body.recording .highlight h3{font-size:19px}body.recording .highlight .detail{font-size:14px}body.recording .kind-tag,body.recording .when,body.recording .chip,body.recording .status-pill{font-size:12px}body.recording .section-head h2{font-size:22px}body.recording[data-focus]:not([data-focus="all"]) .wrap,body.recording[data-focus]:not([data-focus="all"]) .section-grid{grid-template-columns:1fr}body.recording[data-focus]:not([data-focus="all"]) .section-card,body.recording[data-focus]:not([data-focus="all"]) .run-explorer,body.recording[data-focus]:not([data-focus="all"]) .evidence-explorer{grid-column:1/-1}body.recording[data-focus]:not([data-focus="all"]) .highlight-list{grid-template-columns:repeat(2,minmax(0,1fr));max-height:none}body.recording .section-card{padding:28px}
.agenda-controls,.agenda-navigation{display:flex;align-items:center;flex-wrap:wrap;gap:8px}.agenda-controls{justify-content:space-between;background:#f6f8f1;border:1px solid #e1e8dc;border-radius:13px;padding:10px 14px}.agenda-range{font-size:13.5px;margin-left:8px;font-weight:600;color:var(--green-deep);font-variant-numeric:tabular-nums}.agenda-filter{font:inherit;font-size:12px;padding:7px 12px;border:1px solid var(--line-strong);border-radius:9px;background:var(--paper);color:var(--ink)}.agenda-layout{display:grid;grid-template-columns:minmax(0,1fr) 340px;border:1px solid var(--line-strong);border-radius:16px;overflow:hidden;background:var(--paper);box-shadow:0 2px 12px rgba(38,61,54,.03)}.agenda-scroll{overflow:auto;max-height:540px;background:#fffefa}.agenda-calendar{min-width:760px;display:grid;grid-template-columns:repeat(7,minmax(0,1fr))}.agenda-day-heading{padding:10px 8px;font-size:11.5px;font-weight:600;text-align:center;border-bottom:1px solid var(--line);border-left:1px solid #e6ece0;background:#f4f7ee;color:var(--ink);white-space:nowrap;position:sticky;top:0;z-index:10}.agenda-day-heading:first-child,.agenda-windows:nth-child(8),.agenda-day:nth-child(15){border-left:0}.agenda-day-heading[data-today="true"]{color:var(--green-deep);font-weight:700;background:#dfece0;box-shadow:inset 0 -2px 0 var(--green)}.agenda-windows{padding:6px;display:flex;flex-direction:column;gap:5px;border-left:1px solid #e6ece0;border-bottom:1px solid var(--line);min-height:36px;background:#fafbf6}.agenda-window,.agenda-event{font:inherit;text-align:left;color:#2f4c3c;background:#e6efe2;border:1px solid #c2d5b9;border-left:3px solid #4e7a60;border-radius:8px;cursor:pointer;min-width:0;overflow:hidden;padding:7px 9px;display:flex;flex-direction:column;gap:3px;box-shadow:0 1px 2px rgba(38,61,54,.04)}.agenda-window{font-size:10.5px}.agenda-window[data-kind="outlook"]{color:#715332;background:#f6efe0;border:1px dashed #c7a76d;border-left:3px solid #b58d47}.agenda-window[data-kind="expectation"],.agenda-window[data-kind="revisit"],.agenda-window[data-kind="commitment"]{background:#efeaf5;border-color:#cfc2de;border-left:3px solid #7d6591;color:#584463}.agenda-window[data-kind="planning"],.agenda-event[data-kind="planning"]{background:#f5efe2;color:#6e542b;border-color:#dcd0b2;border-left:3px solid #a68346}.agenda-event[data-kind="experience"]{background:#e1eee4;color:#2c543d;border-color:#b6d1bc;border-left:3px solid #3c6e52}.agenda-event[data-kind="world"]{background:#e4eef2;color:#2f5264;border-color:#b8d1dc;border-left:3px solid #46748a}.agenda-event:hover,.agenda-event:focus-visible,.agenda-event[aria-pressed="true"]{box-shadow:0 4px 12px rgba(38,61,54,.12)}.agenda-event[aria-pressed="true"],.agenda-window[aria-pressed="true"]{outline:2px solid var(--green);outline-offset:-1px}.agenda-day{border-left:1px solid #e6ece0;padding:8px;display:flex;flex-direction:column;gap:6px;min-height:380px;background:linear-gradient(180deg,#fffefa 0,#fafbf6 100%)}.agenda-event{position:relative;width:100%;box-sizing:border-box;font-size:11.5px;flex-shrink:0}.agenda-event-meta{display:flex;align-items:center;justify-content:space-between;gap:6px}.agenda-entry-time{font-size:10px;font-weight:600;opacity:.85;line-height:1.1;font-variant-numeric:tabular-nums}.agenda-entry-kind{font-size:9.5px;opacity:.78;background:rgba(255,254,249,.65);padding:1px 5px;border-radius:4px}.agenda-entry-title{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;line-height:1.4;font-weight:500;overflow-wrap:anywhere}.agenda-detail{background:#f7f9f2;border-left:1px solid var(--line);padding:18px 20px;min-width:0;max-height:540px;overflow-y:auto;display:flex;flex-direction:column;gap:6px}.agenda-detail h3{font-size:15px;line-height:1.55;font-weight:600;color:var(--green-deep);overflow-wrap:anywhere;margin:4px 0 2px}.agenda-detail-time{font-size:11px;line-height:1.6;color:var(--muted);font-variant-numeric:tabular-nums;margin:0}.agenda-detail-body{font-size:12.5px;line-height:1.75;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--ink);background:var(--paper);border:1px solid #e3e9dd;border-radius:11px;padding:11px 13px;margin:4px 0 0}.agenda-related{display:inline-flex;align-items:center;font:inherit;font-size:11.5px;font-weight:500;text-align:left;line-height:1.5;color:var(--green);background:#e7efe2;border:1px solid #c8d8c0;border-radius:8px;padding:6px 10px;margin-top:4px;text-decoration:none;cursor:pointer;width:fit-content}.agenda-warning{font-size:11.5px;color:#8d5c31;background:#f6ecdc;border:1px solid #e5d2b2;border-radius:8px;padding:7px 10px;margin:0}.agenda-event:focus-visible,.agenda-window:focus-visible{outline:3px solid #283f31;z-index:9}@media(max-width:1180px){.agenda-layout{grid-template-columns:1fr}.agenda-detail{border-left:0;border-top:1px solid var(--line);max-height:none}.agenda-scroll{max-height:520px}.section-card{grid-column:span 6}.operator-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:960px){.now-panel{grid-template-columns:1fr;gap:20px}.now-panel>div:first-child{border-right:0;border-bottom:1px solid rgba(49,91,74,.12);padding-right:0;padding-bottom:16px}.now-story{grid-template-columns:repeat(2,minmax(0,1fr))}.life-columns{grid-template-columns:1fr}}@media(max-width:680px){.bar{padding:14px 18px;gap:8px}.status-bar,.capture-notice,.recording-tools,.wrap{width:calc(100% - 32px)}.bar h1{font-size:15px}.monogram{width:36px;height:36px}.eyebrow,.readonly{display:none}.status-bar{padding:10px 14px;align-items:start}.status-meta{align-items:start;flex-direction:column;gap:6px}.wrap{padding:16px 0 40px;gap:16px}.section-grid,.operator-grid,.life-columns,.now-story{grid-template-columns:1fr}.section-card{grid-column:1/-1;padding:20px}.now-panel{padding:20px}.bar-actions{gap:6px}.button{font-size:11px;padding:7px 10px}body.recording[data-focus]:not([data-focus="all"]) .highlight-list{grid-template-columns:1fr}.footer{flex-direction:column;gap:4px}}
@media(prefers-reduced-motion:no-preference){.button,.now-line,.highlight{transition:background .15s,border-color .15s,transform .15s,box-shadow .15s}.button:hover{filter:brightness(.97)}.now-line:hover{border-color:#b6c8ae;transform:translateY(-1px)}}
</style></head><body><header class="bar"><div class="identity"><span class="monogram" aria-hidden="true">栀</span><div><h1>沈知栀 · 生活现场</h1><p class="eyebrow">A LIFE, CONTINUING</p></div></div><div class="bar-actions"><button id="recordingToggle" class="button primary" type="button" aria-pressed="false">录制模式</button><form class="logout operator-only" method="post" action="/world-v2/dashboard/logout"><button type="submit">退出登录</button></form></div></header>
<div class="status-bar"><div class="status-meta"><span id="captureState" class="capture-state" role="status">正在读取</span><p id="headerClock" class="header-clock"></p></div><span class="readonly">只读 · 不改变她的生活</span></div>
<p id="captureNotice" class="capture-notice" role="status" hidden></p>
<div id="recordingTools" class="recording-tools" hidden><label for="recordingFocus">聚焦区域</label><select id="recordingFocus"><option value="all">完整视图</option><option value="now">这一刻</option><option value="life">生活进展</option><option value="memory">记忆</option><option value="emotion">情绪</option><option value="relationships">关系</option><option value="pending">未完成事项</option></select><span>仅改变显示；同步状态始终保留 · Esc 退出</span></div>
<main class="wrap"><section class="panel now-panel" data-focus-area="now"><div><p class="hero-label">THE PRESENT</p><h2>这一刻</h2><p class="hero-note">生活有自己的时间。<br>这里是已经记录下来的状态。</p><p id="worldClock" class="world-clock"></p></div><div id="nowStory" class="now-story"><p class="section-note">正在读取生活状态…</p></div></section><div id="sectionGrid" class="section-grid"></div><footer class="footer"><span>只展示经过授权的摘要，私密反思不在此呈现。</span><span class="operator-only">本机 owner 视图</span></footer></main>
<script src="/world-v2/dashboard/app.js?v=world-v2-dashboard-home.1-ui7" defer></script></body></html>"""

DASHBOARD_APP_JS = """'use strict';
const DashboardHomeClient=(()=>{
  const DATA_URL='/world-v2/dashboard/home';
  const SCHEMA_VERSION='world-v2-dashboard-home.1';
  const ROOM_MESSAGE_TYPE='pixel-home-state';
  const ROOM_MESSAGE_VERSION=2;
  const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  function validateSnapshot(payload){
    if(!record(payload)||payload.schema_version!==SCHEMA_VERSION)throw new Error('unsupported dashboard snapshot');
    if(typeof payload.snapshot_hash!=='string'||!/^[0-9a-f]{64}$/.test(payload.snapshot_hash))throw new Error('invalid dashboard snapshot hash');
    if(!record(payload.cursor)||!record(payload.sections)||!record(payload.sections.room))throw new Error('dashboard room section unavailable');
    return payload;
  }
  async function capture(fetchSnapshot,etag=null,{timeoutMs=10000,week=null,page=null}={}){
    const headers={Accept:'application/json'};
    if(etag)headers['If-None-Match']=etag;
    const controller=new AbortController();
    let timer;
    const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>{
      controller.abort();reject(new Error('dashboard snapshot timed out'));
    },timeoutMs);});
    const load=async()=>{
      const response=await fetchSnapshot(DATA_URL+((week!==null||page!==null)?'?'+[...(week?['week='+encodeURIComponent(week)]:[]),...(page!==null?['page='+encodeURIComponent(page)]:[])].join('&'):''),{credentials:'same-origin',headers,signal:controller.signal});
      if(response.status===304)return{kind:'not_modified',etag,snapshot:null};
      if(!response.ok)throw new Error('dashboard snapshot unavailable ('+response.status+')');
      const snapshot=validateSnapshot(await response.json());
      return{kind:'snapshot',etag:response.headers.get('etag'),snapshot};
    };
    try{return await Promise.race([load(),deadline]);}finally{clearTimeout(timer);}
  }
  function snapshotFromCapture(result,previousSnapshot=null){
    if(result&&result.kind==='snapshot')return validateSnapshot(result.snapshot);
    if(result&&result.kind==='not_modified'&&previousSnapshot)return validateSnapshot(previousSnapshot);
    throw new Error('dashboard snapshot cache unavailable');
  }
  function roomState(value){
    if(value==='ready'||value==='stale')return value;
    return'unavailable';
  }
  function unavailableRoomMessage(state='unavailable',logicalTime=null){
    return{
      type:ROOM_MESSAGE_TYPE,
      v:ROOM_MESSAGE_VERSION,
      state:roomState(state),
      route:null,
      logical_time:typeof logicalTime==='string'?logicalTime:null,
    };
  }
  function roomMessageFrom(payload,overrideState=null){
    const snapshot=validateSnapshot(payload);
    const room=snapshot.sections.room;
    const state=roomState(overrideState||room.state);
    const logicalTime=typeof snapshot.logical_time==='string'?snapshot.logical_time:null;
    if(state!=='ready')return unavailableRoomMessage(state,logicalTime);
    const route=record(room.render_state)&&record(room.render_state.route)?room.render_state.route:null;
    if(!route||typeof route.scene_id!=='string'||!route.scene_id||typeof route.action_id!=='string'||!route.action_id||typeof route.availability!=='string'||!route.availability||route.availability==='unavailable')return unavailableRoomMessage('unavailable',logicalTime);
    return{
      type:ROOM_MESSAGE_TYPE,
      v:ROOM_MESSAGE_VERSION,
      state:'ready',
      route:{
        scene_id:route.scene_id,
        action_id:route.action_id,
        availability:route.availability,
      },
      logical_time:logicalTime,
    };
  }
  const STATE_LABELS={
    ready:'正常',
    empty:'暂无',
    unavailable:'暂时看不到',
    degraded:'不太稳',
    stale:'数据有点旧',
    disabled:'未启用',
    warming:'还在热身',
    busy:'忙着',
  };
  const SECTION_HEADINGS={
    overview_life:'生活',
    facts_memory_inner:'记忆与情绪',
    relationship_lifecycle:'身边的人',
    operations:'说话和行动',
    perception_media:'看见的和照片',
    authority_privacy:'权限和隐私',
    ledger_qualification:'账本核对',
    runtime_operations:'系统现在怎么样',
  };
  const KIND_HEADINGS={
    plan:'活动与计划',
    location:'人在哪',
    affect_episode:'心情',
    attention:'注意力',
    interaction_bid:'互动期待',
    response_expectation:'回应期待',
    private_impression:'对你的印象',
    relationship_state:'和你',
    expression_plan:'最近说话',
    action:'行动',
    resource:'身子',
    goal:'目标',
    npc:'人物',
    life_ecology_schedule:'她的日子',
  };
  const GENERIC_TITLES={
    '位置状态已更新':true,
    '目标状态':true,
    '世界事件':true,
    '表达计划':true,
    '表达节拍':true,
    '媒体检查':true,
    '承诺':true,
    '生平坐标':true,
    '情感 episode':true,
    '生活生态调度':true,
    '对你说的话':true,
  };
  const META_LABELS={
    logical_time:'世界时间',
    generated_at:'页面生成',
    world_id:'世界',
    schema_version:'快照版本',
    policy_version:'展示策略',
    snapshot_hash:'快照校验',
    owner:'实例',
    cursor:'账本游标',
  };
  const DATA_FIELD_LABELS={
    relationship_state_count:'关系状态',
    commitment_count:'关系承诺',
    interaction_act_count:'互动行为',
    typed_change_candidate_count:'待处理的关系变化',
    typed_change_lookup_count:'已核对的关系变化',
    typed_change_terminal_count:'已结束的关系变化',
    typed_change_rejected_count:'被拒绝的关系变化',
    typed_change_stale_count:'过期的关系变化',
    typed_change_unsettled_count:'还没落地的关系变化',
    ledger_schema_version:'账本版本',
    reducer_bundle_version:'投影版本',
    field_policy_count:'字段策略',
    typed_summary_field_count:'摘要字段',
    count_only_field_count:'只计数的字段',
    metadata_field_count:'元数据字段',
    intentionally_withheld_field_count:'故意不展示的字段',
  };
  function sectionLabel(sectionId,section){
    if(record(section)&&typeof section.label==='string'&&section.label.trim())return section.label;
    return sectionId;
  }
  function sectionHeading(sectionId,section){
    return SECTION_HEADINGS[sectionId]||sectionLabel(sectionId,section);
  }
  function stateLabel(state){
    if(typeof state!=='string'||!state)return STATE_LABELS.unavailable;
    return STATE_LABELS[state]||STATE_LABELS.unavailable;
  }
  function captureStateLabel(state){
    if(state==='ready')return '已同步';
    if(state==='stale')return '数据有点旧';
    return '暂时连不上';
  }
  function roomOverlayText(state){
    if(state==='stale')return '房间画面有点旧';
    if(state==='ready')return '';
    return '房间暂时看不到';
  }
  function formatClock(value){
    if(typeof value!=='string'||!value)return '';
    const date=new Date(value);
    if(Number.isNaN(date.getTime()))return value;
    const parts=new Intl.DateTimeFormat('zh-CN',{
      timeZone:'Asia/Shanghai',
      month:'numeric',
      day:'numeric',
      hour:'2-digit',
      minute:'2-digit',
      hour12:false,
    }).formatToParts(date);
    const read=type=>(parts.find(part=>part.type===type)||{}).value;
    return Number(read('month'))+'月'+Number(read('day'))+'日 '+read('hour')+':'+read('minute');
  }
  function formatRelative(value,nowMs){
    if(typeof value!=='string'||!value)return '';
    const then=new Date(value).getTime();
    if(Number.isNaN(then))return formatClock(value);
    const delta=Math.round(((nowMs==null?Date.now():nowMs)-then)/1000);
    if(delta<45)return '刚刚';
    if(delta<3600)return Math.floor(delta/60)+' 分钟前';
    if(delta<86400)return Math.floor(delta/3600)+' 小时前';
    if(delta<86400*7)return Math.floor(delta/86400)+' 天前';
    return formatClock(value);
  }
  function formatScalar(key,value,valueLabel){
    if(typeof valueLabel==='string'&&valueLabel.trim())return valueLabel;
    if(typeof value==='boolean')return value?'是':'否';
    if(value==null)return '';
    if(typeof key==='string'&&key.endsWith('_bp')&&typeof value==='number')return Math.round(value/100)+'%';
    if(typeof value==='string'&&/^\\d{4}-\\d{2}-\\d{2}T/.test(value))return formatClock(value);
    return String(value);
  }
  function highlightHeadline(item){
    const title=typeof item.title==='string'?item.title.trim():'';
    const status=typeof item.status_label==='string'?item.status_label.trim():'';
    const kindLabel=typeof item.kind_label==='string'?item.kind_label.trim():'';
    const generic=!title||title===kindLabel||GENERIC_TITLES[title]===true;
    if(generic)return status||title||kindLabel;
    return title;
  }
  function highlightView(item){
    if(!record(item)||item.privacy_class==='withhold'||item.kind==='private_impression')return null;
    let detail=typeof item.detail==='string'?item.detail:'';
    const values=[];
    if(Array.isArray(item.values)){
      for(const entry of item.values){
        if(!record(entry))continue;
        if(entry.key==='intention'&&typeof entry.value==='string'){detail=entry.value;continue;}
        const text=formatScalar(entry.key,entry.value,entry.value_label);
        if(!text)continue;
        values.push({
          label:typeof entry.label==='string'?entry.label:String(entry.key||''),
          text:text,
        });
      }
    }
    const headline=highlightHeadline(item);
    const status=typeof item.status_label==='string'?item.status_label.trim():'';
    return{
      kind:typeof item.kind==='string'?item.kind:'',
      kindLabel:KIND_HEADINGS[item.kind]||item.kind_label||'记录',
      statusCode:typeof item.status_code==='string'?item.status_code:'',
      title:headline,
      status:status&&status!==headline?status:'',
      detail:detail,
      when:typeof item.occurred_at==='string'?item.occurred_at:'',
      values:values,
    };
  }
  function sectionData(snapshot,sectionId){
    const section=record(snapshot)&&record(snapshot.sections)?snapshot.sections[sectionId]:null;
    if(!record(section)||section.state==='unavailable')return null;
    return record(section.data)?section.data:null;
  }
  function visibleHighlights(snapshot,sectionId,kinds=null){
    const data=sectionData(snapshot,sectionId);
    if(!data||!Array.isArray(data.highlights))return [];
    return data.highlights.filter(item=>record(item)&&(!kinds||kinds.includes(item.kind)))
      .map(highlightView).filter(Boolean);
  }
  function newestFirst(items){
    return items.slice().sort((a,b)=>{
      const first=Date.parse(a.when),second=Date.parse(b.when);
      return (Number.isFinite(second)?second:0)-(Number.isFinite(first)?first:0);
    });
  }
  function lifeView(snapshot){
    const section=record(snapshot)&&record(snapshot.sections)?snapshot.sections.overview_life:null;
    const state=record(section)&&STATE_LABELS[section.state]?section.state:'unavailable';
    if(state==='unavailable')return {state,activities:[],timeline:[]};
    return {
      state,
      activities:newestFirst(visibleHighlights(snapshot,'overview_life',['plan'])),
      timeline:newestFirst(visibleHighlights(snapshot,'overview_life',[
        'world_occurrence','outcome_observation','experience','life_arc','aspiration','biographical_coordinate',
      ])),
    };
  }
  function shiftWeek(week,days){
    const at=new Date(week+'T12:00:00Z');at.setUTCDate(at.getUTCDate()+days);
    return at.toISOString().slice(0,10);
  }
  function agendaKey(entry){return JSON.stringify([entry.index,entry.kind,entry.title,entry.starts_at,entry.ends_at,entry.detail]);}
  function agendaView(snapshot,filter='all',status='all'){
    const data=sectionData(snapshot,'overview_life');
    const agenda=data&&data.agenda;
    if(!record(agenda)||agenda.state==='unavailable')return null;
    const start=Date.parse(agenda.week_start+'T00:00:00+08:00');
    if(!Number.isFinite(start)||!Array.isArray(agenda.entries))return null;
    const entries=agenda.entries.filter(item=>record(item)&&item.content_state!=='withheld');
    const shown=entries.filter(item=>(status==='all'||item.status===status)&&(filter==='all'||item.kind===filter||filter==='intentions'&&['commitment','revisit','expectation'].includes(item.kind)));
    const coveredWorld=new Set();
    if(filter==='all'){
      for(const item of shown){
        if(item.kind==='experience'&&Array.isArray(item.related)){
          for(const rel of item.related)coveredWorld.add(rel);
        }
      }
    }
    const days=Array.from({length:7},(_,day)=>{
      const from=start+day*86400000,to=from+86400000;
      const windows=[],timed=[];
      for(const entry of shown){
        if(filter==='all'&&entry.kind==='world'&&(coveredWorld.has(entry.index)||entry.title==='世界事件'))continue;
        if(status==='all'&&entry.kind==='plan'&&entry.status==='已放弃')continue;
        const a=Date.parse(entry.starts_at),b=Date.parse(entry.ends_at);
        if(!Number.isFinite(a)||!Number.isFinite(b)||!(a<to&&(b>from||a===b&&a>=from)))continue;
        const segment={entry,start:Math.max(a,from),end:Math.min(b,to),lane:0,lanes:1};
        if(['period','window'].includes(entry.time_kind)||['outlook','commitment','revisit','expectation'].includes(entry.kind))windows.push(segment);
        else{
          segment.minute=(segment.start-from)/60000;
          segment.endMinute=Math.min(1440,Math.max((segment.end-from)/60000,segment.minute+36));
          timed.push(segment);
        }
      }
      timed.sort((a,b)=>a.minute-b.minute||a.endMinute-b.endMinute);
      let group=[],laneEnds=[],groupEnd=-1;
      const close=()=>{for(const segment of group)segment.lanes=laneEnds.length;group=[];laneEnds=[];};
      for(const segment of timed){
        if(segment.minute>=groupEnd)close();
        let lane=laneEnds.findIndex(end=>end<=segment.minute);
        if(lane<0)lane=laneEnds.length;
        segment.lane=lane;laneEnds[lane]=segment.endMinute;group.push(segment);
        groupEnd=Math.max(segment.endMinute,...laneEnds);
      }
      close();
      return {date:shiftWeek(agenda.week_start,day),windows,timed};
    });
    return {agenda,entries,shown,days};
  }
  function pendingView(snapshot){
    return [
      {sectionId:'relationship_lifecycle',label:'事项与承诺',
        kinds:['thread','commitment','relationship_commitment','interaction_bid']},
      {sectionId:'operations',label:'回访与回应',
        kinds:['revisit_intention','response_expectation']},
    ].map(group=>{
      const section=record(snapshot)&&record(snapshot.sections)?snapshot.sections[group.sectionId]:null;
      return {
        sectionId:group.sectionId,label:group.label,
        state:record(section)&&STATE_LABELS[section.state]?section.state:'unavailable',
        items:visibleHighlights(snapshot,group.sectionId,group.kinds).filter(item=>!(['response_expectation','revisit_intention'].includes(item.kind)&&['fulfilled','superseded','expired'].includes(item.statusCode))),
      };
    });
  }
  function nowStory(snapshot){
    const lines=[];
    const add=(label,sectionId,kinds,empty,unavailable)=>{
      const data=sectionData(snapshot,sectionId);
      const items=newestFirst(visibleHighlights(snapshot,sectionId,kinds));
      const view=items.find(v=>v.title&&!['进行中','一项活动','世界事件','已经落进世界的事'].includes(v.title))||items[0];
      lines.push({
        label,
        text:view?[view.title,view.status].filter(Boolean).join(' · '):(data?empty:unavailable),
        detail:view?view.detail:'',
        missing:!view,
      });
    };
    add('活动与计划','overview_life',['plan'],'暂无可展示的活动或计划','生活状态暂时不可用');
    if(!visibleHighlights(snapshot,'overview_life',['location']).length&&visibleHighlights(snapshot,'overview_life',['world_occurrence']).length){
      add('周围环境','overview_life',['world_occurrence'],'暂无可展示的环境记录','环境状态暂时不可用');
    }else{
      add('人在哪','overview_life',['location'],'暂无可展示的位置记录','位置状态暂时不可用');
    }
    if(!visibleHighlights(snapshot,'overview_life',['resource']).length&&visibleHighlights(snapshot,'overview_life',['experience']).length){
      add('最近经历','overview_life',['experience'],'暂无可展示的经历记录','经历记录暂时不可用');
    }else{
      add('身体状态','overview_life',['resource'],'暂无可展示的身体状态','身体状态暂时不可用');
    }
    add('情绪记录','facts_memory_inner',['affect_episode'],'暂无可展示的情绪记录','情绪记录暂时不可用');
    add('关系状态','relationship_lifecycle',['relationship_state'],'暂无可展示的关系记录','关系状态暂时不可用');
    add('互动状态','operations',['response_expectation'],'暂无可展示的互动状态','互动状态暂时不可用');
    return lines;
  }
  function isNoiseMetric(metric){
    const key=typeof metric.key==='string'?metric.key:'';
    const label=typeof metric.label==='string'?metric.label:'';
    return /proposal_ids$|_transitions$|_proposals$/.test(key)||label.includes('提议标识')||label.endsWith('变更')||label.endsWith('提议');
  }
  function metaLabel(key){
    return META_LABELS[key]||key;
  }
  function dataFieldLabel(key){
    if(key==='typed_change_terminals'||key==='qualification_status'||key==='qualification_label'||key==='notice_count'||key==='expression_episode'||key==='semantic_recall')return null;
    return DATA_FIELD_LABELS[key]||null;
  }
  function metricGroups(metrics){
    const grouped={active:[],zero:[]};
    if(!Array.isArray(metrics))return grouped;
    for(const metric of metrics){
      if(!record(metric)||isNoiseMetric(metric))continue;
      (metric.count===0?grouped.zero:grouped.active).push(metric);
    }
    return grouped;
  }
  return{
    DATA_URL,
    ROOM_MESSAGE_TYPE,
    ROOM_MESSAGE_VERSION,
    SCHEMA_VERSION,
    capture,
    captureStateLabel,
    dataFieldLabel,
    formatClock,
    formatRelative,
    highlightView,
    lifeView,
    agendaView,
    agendaKey,
    shiftWeek,
    visibleHighlights,
    metaLabel,
    nowStory,
    pendingView,
    roomMessageFrom,
    roomOverlayText,
    sectionHeading,
    sectionLabel,
    metricGroups,
    snapshotFromCapture,
    stateLabel,
    unavailableRoomMessage,
    validateSnapshot,
  };
})();
if(typeof module!=='undefined'&&module.exports)module.exports=DashboardHomeClient;
if(typeof window!=='undefined')window.DashboardHomeClient=DashboardHomeClient;
if(typeof document!=='undefined'){
  const byId=id=>document.getElementById(id);
  const isRecord=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const sectionGrid=byId('sectionGrid');
  let lastSnapshot=null;
  let snapshotEtag=null;
  let loading=false;
  let recording=false;
  let focusArea='all';
  let previousScroll=0;
  const focusAreas=['all','now','mechanism','evidence','life','memory','emotion','relationships','pending'];
  let displayPaused=false;
  let resumePending=false;
  const explorer=DashboardExplorer.mount(DashboardHomeClient,{
    onShowEvidence(){if(recording){focusArea='evidence';applyPresentation();}},
  });
  const element=(tag,className,text)=>{
    const node=document.createElement(tag);
    if(className)node.className=className;
    if(text!==undefined)node.textContent=String(text);
    return node;
  };
  function setCaptureState(state){
    const badge=byId('captureState');
    if(badge){
      badge.textContent=DashboardHomeClient.captureStateLabel(state);
      badge.classList.toggle('error',state!=='ready');
    }
    const notice=byId('captureNotice');
    if(notice){
      notice.hidden=state==='ready';
      notice.textContent=state==='stale'
        ?'连接暂时中断。以下保留上次同步的记录，可能已不是最新状态。'
        :state==='unavailable'?'暂时无法读取生活状态，请稍后重试。':'';
    }
  }
  function applyPresentation(){
    document.body.classList.toggle('recording',recording);
    document.body.dataset.focus=focusArea;
    byId('recordingToggle').textContent=recording?'退出录制模式':'录制模式';
    byId('recordingToggle').setAttribute('aria-pressed',String(recording));
    byId('recordingTools').hidden=!recording;
    byId('recordingFocus').value=focusArea;
    for(const panel of document.querySelectorAll('[data-focus-area]')){
      panel.hidden=recording&&focusArea!=='all'&&panel.dataset.focusArea!==focusArea;
    }
    const domains=byId('domainDetails');
    if(recording&&['memory','emotion','relationships','pending'].includes(focusArea))domains.open=true;
    domains.hidden=recording&&['now','mechanism','evidence','life'].includes(focusArea);
  }
  function toggleRecording(){
    if(!recording)previousScroll=window.scrollY||0;
    recording=!recording;
    if(!recording)focusArea='all';
    applyPresentation();
    window.scrollTo(0,recording?0:previousScroll);
  }
  byId('recordingToggle').addEventListener('click',toggleRecording);
  byId('pauseDisplay').addEventListener('click',()=>{
    displayPaused=!displayPaused;
    byId('pauseDisplay').textContent=displayPaused?'恢复同步':'暂停画面';
    byId('pauseDisplay').setAttribute('aria-pressed',String(displayPaused));
    byId('pauseLabel').hidden=!displayPaused;
    if(!displayPaused){
      if(loading)resumePending=true;
      else loadDashboardHome();
    }
  });
  byId('recordingFocus').addEventListener('change',event=>{
    focusArea=focusAreas.includes(event.target.value)?event.target.value:'all';
    applyPresentation();
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&recording)toggleRecording();
  });
  function renderClocks(snapshot){
    const clock=DashboardHomeClient.formatClock(snapshot.logical_time);
    const generated=DashboardHomeClient.formatClock(snapshot.generated_at);
    byId('headerClock').textContent=clock?'世界时间 '+clock:'世界时间暂无记录';
    byId('worldClock').textContent=generated?'快照生成于 '+generated:'';
  }
  function renderNowStory(snapshot){
    const parent=byId('nowStory');
    parent.replaceChildren();
    for(const line of DashboardHomeClient.nowStory(snapshot)){
      const card=element('div','now-line'+(line.missing?' missing':''));
      card.append(element('span','k',line.label),element('span','v',line.text));
      if(line.detail&&!line.text.startsWith(line.detail)&&line.label!=='活动与计划')card.appendChild(element('span','d',line.detail));
      // Full intention belongs to the life card; the present stays a summary.
      parent.appendChild(card);
    }
  }
  function empty(parent,text){parent.appendChild(element('p','empty-state',text));}
  function appendMetricGrid(parent,metrics){
    const groups=DashboardHomeClient.metricGroups(metrics);
    function grid(items){
      const tiles=element('div','metric-grid');
      for(const metric of items){
        if(typeof metric.count!=='number'||typeof metric.label!=='string')continue;
        const tile=element('div','metric');
        tile.append(element('span','',metric.label),element('strong','',metric.count));
        if(typeof metric.count_note==='string')tile.append(element('small','',metric.count_note));
        tiles.appendChild(tile);
      }
      return tiles;
    }
    if(groups.active.length)parent.appendChild(grid(groups.active));
    if(groups.zero.length){
      const details=element('details','zero-metrics');
      details.append(element('summary','','其余为零的计数 ('+groups.zero.length+')'),grid(groups.zero));
      parent.appendChild(details);
    }
  }
  function appendHighlight(parent,view){
    const card=element('article','highlight');
    card.dataset.status=view.statusCode;
    const top=element('div','highlight-top');
    top.appendChild(element('span','kind-tag',view.kindLabel));
    if(view.when)top.appendChild(element('time','when',DashboardHomeClient.formatClock(view.when)));
    card.append(top,element('h3','',view.title||view.kindLabel));
    if(view.status)card.appendChild(element('span','status-pill',view.status));
    if(view.detail&&view.detail!==view.title)card.appendChild(element('p','detail',view.detail));
    if(view.values.length){
      const chips=element('div','chips');
      for(const value of view.values)chips.appendChild(element('span','chip',value.label+' '+value.text));
      card.appendChild(chips);
    }
    parent.appendChild(card);
  }
  function appendHighlights(parent,views){
    const list=element('div','highlight-list');
    for(const view of views)appendHighlight(list,view);
    parent.appendChild(list);
  }
  function sectionCard(title,section,area){
    const card=element('section','section-card');
    if(area)card.dataset.focusArea=area;
    const head=element('div','section-head');
    const state=section&&typeof section.state==='string'?section.state:'unavailable';
    const badge=element('span','section-state',DashboardHomeClient.stateLabel(state));
    badge.dataset.state=state;
    head.append(element('h2','',title),badge);
    const body=element('div','section-body');
    card.append(head,body);
    return {card,body};
  }
  function appendCoverage(parent,section){
    if(isRecord(section&&section.coverage)&&section.coverage.truncated){
      parent.appendChild(element('p','truncated','这里只展示部分摘要，未列出的记录不代表不存在。'));
    }
  }
  let agendaWeek=null,agendaPage=null,agendaFilter='all',agendaStatus='all',agendaSelection=null;
  const agendaLabels={planning:'客观规划',experience:'角色经历',plan:'角色计划',world:'实际事件',outlook:'命运候选',commitment:'约定',revisit:'再次考虑',expectation:'回应期待'};
  function renderAgenda(body,snapshot){
    const view=DashboardHomeClient.agendaView(snapshot,agendaFilter,agendaStatus);
    if(!view){empty(body,'周课表暂时不可用。');return;}
    const {agenda,entries,shown,days}=view;
    if(!shown.some(entry=>DashboardHomeClient.agendaKey(entry)===agendaSelection))agendaSelection=shown[0]?DashboardHomeClient.agendaKey(shown[0]):null;
    const controls=element('div','agenda-controls');
    const navigation=element('div','agenda-navigation');
    const changeWeek=value=>{
      if(displayPaused||loading)return;
      agendaWeek=value;agendaPage=null;agendaSelection=null;snapshotEtag=null;loadDashboardHome();
    };
    for(const [label,offset] of [['上一周',-7],['本周',null],['下一周',7]]){
      const button=element('button','button',label);button.type='button';
      button.disabled=displayPaused;
      button.addEventListener('click',()=>changeWeek(offset===null?null:DashboardHomeClient.shiftWeek(agenda.week_start,offset)));
      navigation.appendChild(button);
    }
    navigation.appendChild(element('strong','agenda-range',days[0].date+' — '+days[6].date));
    if(agenda.page_count>1){
      for(const [label,target] of [['上一页',agenda.page-1],['当前时间',null],['下一页',agenda.page+1]]){
        const button=element('button','button',label);button.type='button';
        button.disabled=displayPaused||(target!==null&&(target<0||target>=agenda.page_count));
        button.addEventListener('click',()=>{
          if(displayPaused||loading)return;
          agendaPage=target;agendaSelection=null;snapshotEtag=null;loadDashboardHome();
        });
        navigation.appendChild(button);
      }
      navigation.appendChild(element('span','section-note','第 '+(agenda.page+1)+' / '+agenda.page_count+' 页'));
    }

    const filter=element('select','agenda-filter');filter.setAttribute('aria-label','筛选当前页课表内容');
    for(const [value,label] of [['all','全部内容'],['planning','客观规划'],['experience','角色经历'],['plan','角色计划'],['world','实际事件'],['outlook','命运候选'],['intentions','约定与惦记']]){
      const option=element('option','',label);option.value=value;filter.appendChild(option);
    }
    filter.value=agendaFilter;
    filter.addEventListener('change',()=>{agendaFilter=filter.value;renderLifePanel();});
    const statusFilter=element('select','agenda-filter');statusFilter.setAttribute('aria-label','筛选课表状态');
    for(const value of ['all',...new Set([...entries.map(entry=>entry.status),...(agendaStatus==='all'?[]:[agendaStatus])])]){
      const option=element('option','',value==='all'?'全部状态':value);option.value=value;statusFilter.appendChild(option);
    }
    statusFilter.value=agendaStatus;
    statusFilter.addEventListener('change',()=>{agendaStatus=statusFilter.value;renderLifePanel();});
    controls.append(navigation,filter,statusFilter);body.appendChild(controls);
    body.appendChild(element('p','section-note','北京时间 · 经历记录已发生的事；规划是客观安排，计划是她自己的打算；命运候选仍待发生。空白处表示没有记录。'));
    if(agenda.planning_state==='unavailable')body.appendChild(element('p','agenda-warning','客观规划暂时无法读取，当前课表不完整。'));
    if(agenda.outlook_state==='unavailable')body.appendChild(element('p','agenda-warning','命运候选暂时无法读取，当前课表不完整。'));
    if(agenda.outlook_state==='empty')body.appendChild(element('p','section-note','命运机制尚无可展示的展望。'));
    if(agenda.truncated)body.appendChild(element('p','agenda-warning','本周至少有 '+agenda.known_count+' 条记录，当前只显示其中 '+entries.length+' 条。未展示不代表不存在。'));
    if(agenda.withheld_count)body.appendChild(element('p','section-note','另有受可见范围限制的记录未展示。'));
    const layout=element('div','agenda-layout'),scroll=element('div','agenda-scroll');
    scroll.setAttribute('tabindex','0');scroll.setAttribute('aria-label','一周课表，可横向滚动');
    const calendar=element('div','agenda-calendar');
    calendar.style.gridTemplateColumns=days.map(day=>(day.timed.length||day.windows.length)?'minmax(215px,2.8fr)':'minmax(64px,.5fr)').join(' ');
    days.forEach((day,index)=>{
      const header=element('div','agenda-day-heading',('周'+'一二三四五六日'[index])+' '+day.date.slice(5));
      if(snapshot.logical_time&&DashboardHomeClient.formatClock(snapshot.logical_time).startsWith(Number(day.date.slice(5,7))+'月'+Number(day.date.slice(8))+'日'))header.dataset.today='true';
      calendar.appendChild(header);
    });
    const select=entry=>{agendaSelection=DashboardHomeClient.agendaKey(entry);renderLifePanel();};
    const makeButton=(entry,className,timeLabel='')=>{
      const button=element('button',className);button.type='button';button.dataset.kind=entry.kind;button.dataset.agendaIndex=String(entry.index);
      button.setAttribute('aria-pressed',String(agendaSelection===DashboardHomeClient.agendaKey(entry)));
      button.setAttribute('aria-label',agendaLabels[entry.kind]+'，'+entry.title+'，'+entry.status);
      const meta=element('div','agenda-event-meta');
      if(timeLabel)meta.appendChild(element('span','agenda-entry-time',timeLabel));
      meta.appendChild(element('span','agenda-entry-kind',agendaLabels[entry.kind]));
      button.append(meta,element('span','agenda-entry-title',entry.title));
      button.title=entry.title+' · '+entry.status;button.addEventListener('click',()=>select(entry));
      return button;
    };
    if(days.some(day=>day.windows.length)){
      for(const day of days){
        const windows=element('div','agenda-windows');
        for(const segment of day.windows)windows.appendChild(makeButton(segment.entry,'agenda-window','时间窗口'));
        calendar.appendChild(windows);
      }
    }
    for(const day of days){
      const column=element('div','agenda-day');
      for(const segment of day.timed){
        const label=DashboardHomeClient.formatClock(new Date(segment.start).toISOString()).split(' ').pop();
        column.appendChild(makeButton(segment.entry,'agenda-event',label));
      }
      calendar.appendChild(column);
    }
    scroll.appendChild(calendar);layout.appendChild(scroll);
    const detail=element('aside','agenda-detail');detail.setAttribute('aria-live','polite');
    const selected=shown.find(entry=>DashboardHomeClient.agendaKey(entry)===agendaSelection)||shown[0];
    if(selected){
      detail.append(element('span','kind-tag',agendaLabels[selected.kind]),element('h3','',selected.title),element('span','status-pill',selected.status));
      detail.appendChild(element('p','agenda-detail-time',DashboardHomeClient.formatClock(selected.starts_at)+' — '+DashboardHomeClient.formatClock(selected.ends_at)));
      detail.appendChild(element('p','agenda-detail-body',selected.detail));
      if(selected.content_state==='unavailable')detail.appendChild(element('p','section-note','详情来源暂缺，未根据标题补写。'));
      if(selected.truncated)detail.appendChild(element('p','section-note','正文较长，这里显示节选。'));
      for(const relation of selected.related||[]){
        const related=entries.find(entry=>entry.index===relation);
        if(!related)continue;
        const button=element('button','agenda-related','已记录的关联：'+related.title);button.type='button';
        button.addEventListener('click',()=>{agendaFilter='all';select(related);});detail.appendChild(button);
      }
    }else empty(detail,'本周此分类没有已记录的内容。');
    layout.appendChild(detail);body.appendChild(layout);
  }
  function renderLifePanel(){
    if(lastSnapshot){renderLife(lastSnapshot);applyPresentation();}
  }
  function renderLife(snapshot){
    const host=byId('agendaPanel');
    const scroll=host.querySelector('.agenda-scroll');
    const position=scroll?{left:scroll.scrollLeft,top:scroll.scrollTop}:null;
    const focus=document.activeElement;
    const focusIndex=focus&&focus.dataset.agendaIndex;
    const filterFocused=focus&&focus.classList.contains('agenda-filter');
    const section=snapshot.sections.overview_life;
    const {card,body}=sectionCard('一周生活',section,'life');
    renderAgenda(body,snapshot);
    host.replaceChildren(card);
    const nextScroll=host.querySelector('.agenda-scroll');
    if(nextScroll){
      if(position){nextScroll.scrollLeft=position.left;nextScroll.scrollTop=position.top;}
      else{
        const view=DashboardHomeClient.agendaView(snapshot,agendaFilter,agendaStatus);
        const minutes=view?view.days.flatMap(d=>d.timed.map(s=>s.minute)):[];
        const firstMin=minutes.length?Math.min(...minutes):420;
        nextScroll.scrollTop=Math.max(0,Math.round(firstMin/1440*1320)-24);
      }
    }
    if(focusIndex!==undefined)host.querySelector('[data-agenda-index="'+focusIndex+'"]')?.focus({preventScroll:true});
    else if(filterFocused)host.querySelector('.agenda-filter')?.focus({preventScroll:true});
  }
  function renderDomain(snapshot,{title,sectionId,area,kinds,metricKeys,note}){
    const section=snapshot.sections[sectionId];
    const {card,body}=sectionCard(title,section,area);
    body.appendChild(element('p','section-note',note));
    const data=isRecord(section&&section.data)?section.data:null;
    if(!data||section.state==='unavailable')empty(body,title+'记录暂时不可用。');
    else{
      const metrics=Array.isArray(data.metrics)?data.metrics:[];
      appendMetricGrid(body,metrics.filter(item=>isRecord(item)&&(!metricKeys||metricKeys.includes(item.key))));
      const views=DashboardHomeClient.visibleHighlights(snapshot,sectionId,kinds);
      if(views.length)appendHighlights(body,views);
      else empty(body,'暂无可展示的'+title+'摘要。');
      appendCoverage(body,section);
    }
    sectionGrid.appendChild(card);
  }
  function renderPending(snapshot){
    const groups=DashboardHomeClient.pendingView(snapshot);
    const available=groups.filter(group=>group.state!=='unavailable');
    const state=!available.length?'unavailable':available.length!==groups.length?'degraded'
      :groups.some(group=>group.items.length)?'ready':'empty';
    const {card,body}=sectionCard('未完成事项',{state},'pending');
    body.appendChild(element('p','section-note','待续事项及相关记录，保留原有状态；不据时间推断此刻仍在等待。'));
    const columns=element('div','life-columns');
    for(const group of groups){
      const column=element('div','life-column');
      column.appendChild(element('h3','section-subtitle',group.label));
      if(group.state==='unavailable')empty(column,'这部分事项记录暂时不可用。');
      else if(!group.items.length)empty(column,'暂无可展示的事项记录。');
      else appendHighlights(column,group.items);
      appendCoverage(column,snapshot.sections[group.sectionId]);
      columns.appendChild(column);
    }
    body.appendChild(columns);
    sectionGrid.appendChild(card);
  }
  function renderOperations(snapshot){
    const details=element('details','operator-details operator-only');
    details.appendChild(element('summary','','运维与其他记录'));
    const grid=element('div','operator-grid');
    for(const id of ['operations','perception_media','authority_privacy','ledger_qualification','runtime_operations']){
      const section=snapshot.sections[id];
      if(!isRecord(section))continue;
      const {card,body}=sectionCard(DashboardHomeClient.sectionHeading(id,section),section,null);
      const data=isRecord(section.data)?section.data:{};
      if(section.state==='unavailable')empty(body,'这部分记录暂时不可用。');
      else{
        if(typeof data.qualification_label==='string')body.appendChild(element('p','section-note',data.qualification_label));
        appendMetricGrid(body,data.metrics);
        appendHighlights(body,DashboardHomeClient.visibleHighlights(snapshot,id));
        for(const signal of Array.isArray(data.signals)?data.signals:[]){
          if(!isRecord(signal))continue;
          body.appendChild(element('p','section-note',String(signal.label||'状态')+' · '+DashboardHomeClient.stateLabel(signal.state)));
        }
        for(const notice of Array.isArray(data.notices)?data.notices:[]){
          if(!isRecord(notice))continue;
          body.appendChild(element('p','section-note',String(notice.signal_label||notice.label||'提示')+' · '+String(notice.reason_label||'暂时不可用')));
        }
        for(const label of [data.expression_episode?.mode_label,data.semantic_recall?.semantic_embedding_label]){
          if(typeof label==='string')body.appendChild(element('p','section-note',label));
        }
        for(const [key,value] of Object.entries(data)){
          const label=DashboardHomeClient.dataFieldLabel(key);
          if(label&&typeof value==='number')body.appendChild(element('p','section-note',label+' · '+value));
        }
        appendCoverage(body,section);
      }
      const coverage=section.coverage;
      if(isRecord(coverage)){
        const info=element('details','section-contract');
        info.append(element('summary','','技术细节'),element('p','section-note','已展示 '+coverage.included_count+' / 已知 '+coverage.known_count+' 条摘要'));
        body.appendChild(info);
      }
      grid.appendChild(card);
    }
    details.appendChild(grid);
    sectionGrid.appendChild(details);
  }
  function renderSnapshot(snapshot){
    renderClocks(snapshot);
    renderNowStory(snapshot);
    explorer.update(snapshot);
    sectionGrid.replaceChildren();
    renderLife(snapshot);
    renderDomain(snapshot,{
      title:'记忆',sectionId:'facts_memory_inner',area:'memory',
      kinds:['fact','memory_candidate','character_core'],metricKeys:['facts','memory_candidates'],
      note:'候选、保留与遗忘分别记录。记忆候选不等于已确认事实。',
    });
    renderDomain(snapshot,{
      title:'情绪',sectionId:'facts_memory_inner',area:'emotion',
      kinds:['affect_episode','affect_baseline','appraisal'],metricKeys:['appraisals','affect_episodes'],
      note:'这里是已记录的情绪与情境评估摘要，不是完整内心独白。',
    });
    renderDomain(snapshot,{
      title:'关系',sectionId:'relationship_lifecycle',area:'relationships',kinds:null,
      metricKeys:['relationship_states','relationship_commitments','npcs','private_impressions'],
      note:'承诺、关系与人物各有记录。私人印象只显示数量。',
    });
    renderPending(snapshot);
    renderOperations(snapshot);
    applyPresentation();
    setCaptureState('ready');
  }
  async function loadDashboardHome(){
    if(loading||displayPaused)return;
    loading=true;
    try{
      const result=await DashboardHomeClient.capture(window.fetch.bind(window),snapshotEtag,{week:agendaWeek,page:agendaPage});
      // A response already in flight must not replace a deliberately frozen view.
      if(displayPaused)return;
      const snapshot=DashboardHomeClient.snapshotFromCapture(result,lastSnapshot);
      if(result.kind==='not_modified'){
        setCaptureState('ready');
        return;
      }
      snapshotEtag=result.etag;
      lastSnapshot=snapshot;
      renderSnapshot(snapshot);
    }catch(error){
      if(displayPaused)return;
      // The payload and upstream error never become visible page content.
      setCaptureState(lastSnapshot?'stale':'unavailable');
      if(!lastSnapshot){
        explorer.update({sections:{}});
        renderNowStory({sections:{}});
        sectionGrid.replaceChildren();
        empty(sectionGrid,'尚未取得可验证的生活快照。');
      }
    }finally{loading=false;if(resumePending){resumePending=false;loadDashboardHome();}}
  }
  explorer.update({sections:{}});
  loadDashboardHome();
  setInterval(loadDashboardHome,15000);
}
"""

# Keep the explorer's presentation and pure view model separate from the
# authenticated shell. Both ship as Python package code, with no asset-loader
# or build-time dependency and no new browser data route.
DASHBOARD_HTML = (
    DASHBOARD_HTML.replace("</style>", EXPLORER_CSS + "</style>")
    .replace('<div id="sectionGrid" class="section-grid"></div>', '<div id="agendaPanel"></div>' + EXPLORER_HTML + '<details id="domainDetails" class="domain-details" open><summary>按领域查看摘要与运维详情</summary><div id="sectionGrid" class="section-grid"></div></details>')
    .replace('<option value="now">这一刻</option>', '<option value="now">这一刻</option><option value="mechanism">运行机制</option><option value="evidence">记录浏览</option>')
    .replace('<span class="readonly">只读 · 不改变她的生活</span>', '<div class="view-tools"><span id="pauseLabel" class="pause-label" hidden>画面已暂停 · 不暂停角色进程</span><button id="pauseDisplay" type="button" class="button" aria-pressed="false">暂停画面</button><span class="readonly">只读 · 不改变她的生活</span></div>')
    .replace('world-v2-dashboard-home.1-ui7', 'world-v2-dashboard-home.1-ui7')
)
DASHBOARD_APP_JS = DASHBOARD_APP_JS.replace(
    "if(typeof module!=='undefined'&&module.exports)",
    EXPLORER_JS + "\nif(typeof module!=='undefined'&&module.exports)",
)


__all__ = [
    "DASHBOARD_APP_JS",
    "DASHBOARD_HTML",
    "DASHBOARD_SESSION_COOKIE",
    "DASHBOARD_SESSION_TTL_SECONDS",
    "DashboardSessionCodec",
    "LOGIN_HTML",
    "UNAVAILABLE_HTML",
]
