"""Stylesheet for the owner Dashboard "stage" presentation.

Dark, cinematic and projector-friendly.  Each domain owns one accent hue so
the information hierarchy reads at a glance: jade = life, rose = bonds,
lavender = fate, amber = feeling, sky = perception.  All motion respects
``prefers-reduced-motion``.
"""

STAGE_CSS = r"""
:root{
  --bg:#080b11;--bg2:#0d121a;--panel:rgba(255,255,255,.035);--panel2:rgba(255,255,255,.06);
  --line:rgba(255,255,255,.08);--line2:rgba(255,255,255,.14);
  --ink:#eef1f4;--ink2:#b3bdc7;--ink3:#78838f;--ink4:#4f5966;
  --cream:#f4ead5;--jade:#7ee8c6;--rose:#f6a6c1;--violet:#c6b4ff;--amber:#ffcf86;--sky:#86ccff;--coral:#ff9f8c;
  --serif:"Noto Serif SC","Source Han Serif SC","Songti SC","STSong",serif;
  --sans:"PingFang SC","Noto Sans SC","HarmonyOS Sans SC","Microsoft YaHei",system-ui,sans-serif;
  --mono:"SF Mono","JetBrains Mono","Menlo",ui-monospace,monospace;
  --r:22px;--ease:cubic-bezier(.22,.8,.2,1);
  --k-planning:#86ccff;--k-plan:#7ee8c6;--k-experience:#ffcf86;--k-world:#c6b4ff;--k-expectation:#f6a6c1;--k-revisit:#ffb27a;--k-commitment:#d7f08c;--k-outlook:#c6b4ff;
}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{scroll-behavior:smooth;background:var(--bg)}
body{margin:0;min-height:100vh;color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.6;background:var(--bg);-webkit-font-smoothing:antialiased;overflow-x:hidden}
button,input,select{font:inherit;color:inherit}
a{color:inherit}
::selection{background:rgba(246,166,193,.35)}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.muted{color:var(--ink3)}.center{text-align:center}
.empty{color:var(--ink3);font-size:13px;margin:8px 0;padding:14px 16px;border:1px dashed var(--line2);border-radius:14px}

/* backdrop -------------------------------------------------------------- */
.backdrop{position:fixed;inset:0;z-index:-1;overflow:hidden;pointer-events:none}
.aurora{position:absolute;width:70vmax;height:70vmax;border-radius:50%;filter:blur(90px);opacity:.22;animation:drift 38s ease-in-out infinite alternate}
.a1{background:#1f6f63;left:-20vmax;top:-30vmax}
.a2{background:#4b2f7a;right:-25vmax;top:20vh;animation-duration:46s}
.a3{background:#6b2f4a;left:20vw;bottom:-45vmax;animation-duration:54s;opacity:.15}
@keyframes drift{to{transform:translate(8vmax,6vmax) scale(1.12)}}
.grain{position:absolute;inset:-50%;opacity:.06;background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>")}

/* topbar ---------------------------------------------------------------- */
.topbar{position:sticky;top:0;z-index:40;display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:20px;padding:12px clamp(16px,3vw,40px);background:rgba(8,11,17,.62);backdrop-filter:blur(18px) saturate(1.4);-webkit-backdrop-filter:blur(18px) saturate(1.4);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:10px;text-decoration:none}
.gardenia{width:34px;height:34px;fill:var(--cream);filter:drop-shadow(0 0 10px rgba(244,234,213,.45));animation:bloom 18s linear infinite}
.gardenia circle{fill:#e9c46a}
@keyframes bloom{to{transform:rotate(360deg)}}
.brand-text{display:grid;line-height:1.15}
.brand-text strong{font-family:var(--serif);font-size:18px;letter-spacing:.12em}
.brand-text small{font-size:9.5px;letter-spacing:.32em;color:var(--ink3)}
.nav{position:relative;display:flex;justify-content:center;gap:2px;overflow-x:auto;scrollbar-width:none}
.nav::-webkit-scrollbar{display:none}
.nav a{position:relative;z-index:1;padding:7px 13px;border-radius:999px;text-decoration:none;font-size:13px;color:var(--ink2);white-space:nowrap;border:1px solid transparent;transition:color .3s,background .4s,border-color .4s}
.nav a.active{background:var(--panel2);border-color:var(--line2)}
.nav a:hover,.nav a.active{color:var(--ink)}
.nav-ink{display:none}
.top-tools{display:flex;align-items:center;gap:8px}
.sync{display:flex;align-items:center;gap:7px;padding:5px 11px;border-radius:999px;background:var(--panel);border:1px solid var(--line);font-size:12px;color:var(--ink2)}
.sync-dot{width:7px;height:7px;border-radius:50%;background:var(--ink3)}
.sync[data-state="ready"] .sync-dot{background:var(--jade);box-shadow:0 0 0 0 rgba(126,232,198,.6);animation:ping 2.4s infinite}
.sync[data-state="stale"] .sync-dot{background:var(--amber)}
.sync[data-state="unavailable"] .sync-dot{background:var(--coral)}
@keyframes ping{70%{box-shadow:0 0 0 8px rgba(126,232,198,0)}100%{box-shadow:0 0 0 0 rgba(126,232,198,0)}}
.sync-ago{color:var(--ink3)}
.top-clock{font-family:var(--mono);font-size:12px;color:var(--ink3);white-space:nowrap}
.tool{padding:7px 13px;border-radius:999px;border:1px solid var(--line2);background:var(--panel);color:var(--ink);font-size:12.5px;cursor:pointer;transition:all .25s var(--ease);white-space:nowrap}
.tool:hover{background:var(--panel2);border-color:rgba(255,255,255,.25)}
.tool:disabled{opacity:.4;cursor:not-allowed}
.tool.primary{background:linear-gradient(135deg,var(--cream),#e8d6ae);color:#1b1a16;border-color:transparent;font-weight:600}
.tool.primary:hover{filter:brightness(1.06);box-shadow:0 6px 24px rgba(244,234,213,.25)}
.tool.ghost{background:transparent}
.tool.sm{padding:3px 10px}
.tool.wide{width:100%;padding:11px}
.logout{margin:0}
.capture-notice,.pause-label{position:sticky;top:61px;z-index:39;margin:0;padding:8px 20px;text-align:center;font-size:12.5px;background:rgba(255,207,134,.12);color:var(--amber);border-bottom:1px solid rgba(255,207,134,.2)}

/* layout ---------------------------------------------------------------- */
.wrap{max-width:1480px;margin:0 auto;padding:0 clamp(16px,3vw,40px) 60px}
.scene{padding:72px 0 8px;scroll-margin-top:70px}
.scene-head{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;margin-bottom:26px;flex-wrap:wrap}
.eyebrow{margin:0 0 10px;font-size:11px;letter-spacing:.28em;color:var(--ink3);text-transform:uppercase;display:flex;align-items:center;gap:10px}
.eyebrow .idx{font-family:var(--mono);letter-spacing:0;color:var(--cream);padding:2px 7px;border:1px solid var(--line2);border-radius:6px}
.scene-head h2{margin:0;font-family:var(--serif);font-size:clamp(28px,3.2vw,44px);font-weight:600;letter-spacing:.04em;line-height:1.15;background:linear-gradient(120deg,#fff 20%,var(--cream) 60%,#c9b98f);-webkit-background-clip:text;background-clip:text;color:transparent}
.lede{margin:10px 0 0;max-width:640px;color:var(--ink2);font-size:14px}
.scene-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.card{position:relative;background:linear-gradient(180deg,rgba(255,255,255,.045),rgba(255,255,255,.02));border:1px solid var(--line);border-radius:var(--r);padding:22px;overflow:hidden;isolation:isolate}
.card::before{content:"";position:absolute;inset:0;z-index:-1;background:radial-gradient(420px circle at var(--mx,-999px) var(--my,-999px),rgba(244,234,213,.07),transparent 60%);transition:opacity .3s}
.card-title{margin:0 0 14px;font-size:15px;font-weight:600;letter-spacing:.04em;display:flex;align-items:baseline;gap:8px}
.card-title small{font-weight:400;font-size:11.5px;color:var(--ink3)}
.chip{display:inline-flex;align-items:center;gap:6px;padding:4px 11px;border-radius:999px;font-size:12px;background:var(--panel2);border:1px solid var(--line);color:var(--ink2);white-space:nowrap}
.chip.link{cursor:pointer;color:var(--cream);background:transparent;border-color:rgba(244,234,213,.3)}
.chip.link:hover{background:rgba(244,234,213,.08)}
.chip.status{color:var(--jade);border-color:rgba(126,232,198,.35);background:rgba(126,232,198,.08)}
.chip.lane{border-color:var(--lane);color:var(--lane)}
.chip.q{color:var(--violet);border-color:rgba(198,180,255,.4)}
.chip.warm{color:var(--amber)}.chip.cool{color:var(--sky)}.chip.ok{color:var(--jade)}
.reveal{opacity:0;transform:translateY(28px);transition:opacity 1s var(--ease),transform 1s var(--ease)}
.reveal.in{opacity:1;transform:none}
[role="button"]{cursor:pointer}
:focus-visible{outline:2px solid var(--cream);outline-offset:3px}

/* 01 hero --------------------------------------------------------------- */
.hero{position:relative;isolation:isolate;margin-top:24px;padding:56px clamp(22px,4vw,64px) 30px;border-radius:32px;overflow:hidden;border:1px solid var(--line2);min-height:640px;display:flex;flex-direction:column;justify-content:space-between;scroll-margin-top:90px}
.sky{position:absolute;inset:0;z-index:-1;transition:background 2s}
body[data-phase="night"] .sky{background:radial-gradient(120% 90% at 80% 0%,#1d2a5c 0%,#0e1430 45%,#070912 100%)}
body[data-phase="dawn"] .sky{background:radial-gradient(120% 90% at 80% 10%,#f2a28a 0%,#7a4a86 38%,#1b1838 80%)}
body[data-phase="day"] .sky{background:radial-gradient(120% 90% at 80% 0%,#6fb7c9 0%,#1f5f6b 45%,#0b1d24 100%)}
body[data-phase="dusk"] .sky{background:radial-gradient(120% 90% at 80% 10%,#f08a5d 0%,#7b3d6d 40%,#1a1230 85%)}
.sky::after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(6,8,14,.72) 0%,rgba(6,8,14,.25) 60%,rgba(6,8,14,.1)),linear-gradient(0deg,rgba(6,8,14,.75),transparent 40%)}
.sky-stars{position:absolute;inset:0;opacity:0;transition:opacity 2s;background-image:radial-gradient(1.2px 1.2px at 12% 18%,#fff,transparent),radial-gradient(1px 1px at 32% 42%,#fff,transparent),radial-gradient(1.4px 1.4px at 58% 12%,#fff,transparent),radial-gradient(1px 1px at 72% 34%,#fff,transparent),radial-gradient(1.2px 1.2px at 88% 22%,#fff,transparent),radial-gradient(1px 1px at 45% 70%,#fff,transparent),radial-gradient(1px 1px at 66% 58%,#fff,transparent),radial-gradient(1.3px 1.3px at 92% 64%,#fff,transparent),radial-gradient(1px 1px at 22% 62%,#fff,transparent),radial-gradient(1px 1px at 80% 82%,#fff,transparent);animation:twinkle 6s ease-in-out infinite alternate}
body[data-phase="night"] .sky-stars,body[data-phase="dawn"] .sky-stars{opacity:.85}
@keyframes twinkle{50%{opacity:.45}}
.sky-sun{position:absolute;right:12%;top:-80px;width:320px;height:320px;border-radius:50%;background:radial-gradient(circle,rgba(255,236,200,.55),rgba(255,236,200,0) 65%);filter:blur(4px)}
body[data-phase="night"] .sky-sun{background:radial-gradient(circle,rgba(200,215,255,.35),rgba(200,215,255,0) 60%)}
.sky-haze{position:absolute;left:0;right:0;bottom:0;height:45%;background:linear-gradient(0deg,rgba(126,232,198,.08),transparent)}
.petals i{position:absolute;top:-30px;left:calc(var(--i)*11% + 3%);width:12px;height:16px;border-radius:12px 1px 12px 1px;background:linear-gradient(135deg,#fffaf0,#efe1c2);opacity:.0;animation:fall calc(16s + var(--i)*2.3s) linear infinite;animation-delay:calc(var(--i)*-3.1s)}
@keyframes fall{0%{transform:translate(0,0) rotate(0);opacity:0}10%{opacity:.55}90%{opacity:.4}100%{transform:translate(-120px,720px) rotate(540deg);opacity:0}}
.hero-grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(320px,.9fr);gap:40px;align-items:center}
.hero-name{margin:0 0 30px;display:flex;align-items:baseline;gap:18px;flex-wrap:wrap}
.hero-name span{font-family:var(--serif);font-size:clamp(46px,6vw,84px);font-weight:700;letter-spacing:.12em;line-height:1;color:#fff;text-shadow:0 0 40px rgba(244,234,213,.25)}
.hero-name em{font-style:normal;font-family:var(--serif);font-size:15px;color:var(--ink2);letter-spacing:.12em}
.hero-kicker{margin:0 0 8px;font-size:12px;letter-spacing:.2em;color:var(--jade)}
.hero-activity{margin:0;font-family:var(--serif);font-size:clamp(20px,2.1vw,28px);line-height:1.6;font-weight:500;color:#fff;max-width:760px;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.hero-activity[data-long="true"]{font-size:clamp(18px,1.8vw,24px)}
.hero-meta{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0 26px}
.hero-context{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;max-width:820px}
.ctx{padding:14px 16px;border-radius:16px;background:rgba(8,11,17,.45);border:1px solid var(--line2);backdrop-filter:blur(10px);transition:transform .35s var(--ease),border-color .35s}
.ctx:hover{transform:translateY(-3px);border-color:rgba(244,234,213,.35)}
.ctx-label{font-size:11px;letter-spacing:.14em;color:var(--ink3)}
.ctx-text{margin:6px 0 0;font-size:13px;line-height:1.65;color:var(--ink)}
.ctx.wait{border-color:rgba(246,166,193,.35);background:linear-gradient(160deg,rgba(246,166,193,.12),rgba(8,11,17,.5))}
.ctx.wait .ctx-label{color:var(--rose)}
.ctx-foot{display:flex;align-items:center;gap:8px;margin-top:10px;font-size:11px;color:var(--ink3)}
.ctx-bar{flex:1;height:3px;border-radius:3px;background:rgba(255,255,255,.1);overflow:hidden}
.ctx-bar i{display:block;height:100%;background:var(--rose);box-shadow:0 0 8px var(--rose)}
.hero-dial{display:grid;place-items:center}
.day-dial{width:min(420px,100%)}
.dial-svg{width:100%;height:auto;overflow:visible}
.dial-rim{fill:rgba(8,11,17,.35);stroke:var(--line2)}
.dial-daylight{fill:none;stroke:rgba(255,207,134,.14);stroke-width:12}
.dial-tick{stroke:rgba(255,255,255,.18)}.dial-tick.mid{stroke:rgba(255,255,255,.32)}.dial-tick.major{stroke:rgba(255,255,255,.7);stroke-width:1.5}
.dial-hour{fill:var(--ink3);font-size:10px;text-anchor:middle;font-family:var(--mono)}.dial-hour.major{fill:var(--ink);font-size:12px}
.dial-elapsed{fill:none;stroke:url(#dialElapsed);stroke-width:2.5;stroke-linecap:round}
.dial-seg{fill:none;stroke-linecap:round;stroke-width:12;opacity:.9;transition:opacity .2s,stroke-width .2s}
.dial-seg:hover{opacity:1;stroke-width:15}
.dial-seg.thin{stroke-width:3.5}.dial-seg.thin:hover{stroke-width:5}
.dial-seg.abandoned{stroke-dasharray:3 4;opacity:.45}
.dial-seg.k-planning{stroke:var(--k-planning);stroke-width:8}
.dial-seg.k-plan{stroke:var(--k-plan)}.dial-seg.k-experience{stroke:var(--k-experience)}
.dial-seg.k-expectation{stroke:var(--k-expectation)}.dial-seg.k-revisit{stroke:var(--k-revisit)}.dial-seg.k-commitment{stroke:var(--k-commitment)}
.dial-dot{fill:var(--k-world)}
.dial-hand{stroke:rgba(255,255,255,.55);stroke-width:1.2;stroke-dasharray:2 3}
.dial-orb{fill:#fff3d6}.dial-orb-glow{fill:#ffd88a;opacity:.8;animation:breathe 3s ease-in-out infinite}
.dial-orb.night{fill:#e3ebff}.dial-orb-glow.night{fill:#9db6ff}
@keyframes breathe{50%{opacity:.35}}
.dial-time{fill:#fff;font-family:var(--serif);font-size:52px;font-weight:600;text-anchor:middle;letter-spacing:.04em}
.dial-sub{fill:var(--ink2);font-size:12px;text-anchor:middle;letter-spacing:.14em}.dial-sub.dim{fill:var(--ink3);font-size:11px}
.dial-legend{display:flex;justify-content:center;gap:14px;flex-wrap:wrap;margin-top:8px}
.lg{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--ink3)}
.lg::before{content:"";width:10px;height:4px;border-radius:2px;background:currentColor}
.lg.k-planning{color:var(--k-planning)}.lg.k-plan{color:var(--k-plan)}.lg.k-experience{color:var(--k-experience)}.lg.k-expectation{color:var(--k-expectation)}.lg.k-world{color:var(--k-world)}
.odometer{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:0;margin-top:44px;border-top:1px solid var(--line2);padding-top:22px}
.odo{padding:0 18px;border-left:1px solid var(--line)}
.odo:first-child{border-left:0;padding-left:0}
.odo-num{display:block;font-family:var(--mono);font-size:clamp(20px,2.2vw,30px);font-weight:500;color:#fff;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.odo-label{display:block;font-size:12.5px;color:var(--ink);margin-top:2px}
.odo-note{display:block;font-size:11px;color:var(--ink3)}

/* 02 inner -------------------------------------------------------------- */
.live-pills{display:flex;gap:8px;flex-wrap:wrap}
.live-pill{display:inline-flex;align-items:center;gap:8px;padding:7px 14px;border-radius:999px;border:1px solid color-mix(in srgb,var(--hue) 50%,transparent);background:color-mix(in srgb,var(--hue) 12%,transparent);color:var(--hue);font-size:13px}
.live-pill .pulse{width:8px;height:8px;border-radius:50%;background:var(--hue);box-shadow:0 0 12px var(--hue);animation:breathe 2.4s infinite}
.inner-grid{display:grid;grid-template-columns:minmax(320px,.85fr) minmax(0,1.15fr);gap:22px;align-items:stretch}
.phone{padding:0;display:flex;flex-direction:column;max-height:620px;border-radius:30px;background:linear-gradient(180deg,#121823,#0b1018)}
.phone-head{display:flex;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--line)}
.phone-head strong{display:block;font-size:14px}.phone-head small{color:var(--ink3);font-size:11.5px}
.phone-avatar{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;font-family:var(--serif);font-size:17px;color:#1b1a16;background:linear-gradient(135deg,var(--cream),#e3c996);box-shadow:0 0 0 3px rgba(244,234,213,.12)}
.phone-hint{margin-left:auto;font-size:11px;color:var(--ink3)}
.phone-body{flex:1;overflow-y:auto;padding:16px 16px 22px;display:flex;flex-direction:column;gap:6px;scrollbar-width:thin;scrollbar-color:var(--line2) transparent}
.turn{display:flex;flex-direction:column;gap:5px;padding:10px;border-radius:18px;border:1px solid transparent;transition:background .3s,border-color .3s}
.turn:hover{background:rgba(255,255,255,.03)}
.turn.sel{background:rgba(244,234,213,.05);border-color:rgba(244,234,213,.22);box-shadow:inset 3px 0 0 var(--cream)}
.turn-time{align-self:center;font-size:10.5px;color:var(--ink4);letter-spacing:.06em;margin-bottom:2px}
.turn-sys{align-self:center;font-size:11px;color:var(--amber);padding:2px 10px;border-radius:999px;background:rgba(255,207,134,.08)}
.turn-sys.muted{color:var(--ink3);background:transparent}
.bubble{max-width:82%;padding:9px 14px;border-radius:18px;font-size:13.5px;line-height:1.55;animation:pop .5s var(--ease) both}
.bubble.her{align-self:flex-start;background:#1d2633;border-top-left-radius:6px}
.bubble.you{align-self:flex-end;background:linear-gradient(135deg,#7ee8c6,#5dc9b8);color:#0b1712;border-top-right-radius:6px}
@keyframes pop{from{opacity:0;transform:translateY(6px) scale(.97)}}
.thought{display:flex;flex-direction:column;gap:18px;padding:32px 34px;background:radial-gradient(120% 100% at 0% 0%,rgba(255,207,134,.10),transparent 55%),linear-gradient(180deg,rgba(255,255,255,.045),rgba(255,255,255,.015))}
.thought-head{display:flex;align-items:center;gap:12px}
.thought-badge{font-family:var(--serif);font-size:13px;padding:3px 12px;border-radius:999px;color:#1b1a16;background:var(--amber)}
.thought-when{font-size:12px;color:var(--ink3)}
.thought-main{margin:0;font-family:var(--serif);font-size:clamp(17px,1.5vw,21px);line-height:1.95;color:#fff;min-height:5em;white-space:pre-wrap;position:relative;padding-left:22px;border-left:2px solid rgba(255,207,134,.5)}
.thought-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.thought-label{font-size:11px;letter-spacing:.16em;color:var(--ink3);margin-right:4px;display:block}
.thought-chip{font-size:12.5px;padding:5px 12px;border-radius:12px;background:rgba(255,255,255,.05);border:1px solid var(--line);color:var(--ink)}
.thought-chip.warm{border-color:rgba(255,159,140,.35);color:var(--coral)}
.thought-pair{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.thought-pair>div{padding:14px 16px;border-radius:16px;background:rgba(246,166,193,.06);border:1px solid rgba(246,166,193,.16)}
.thought-pair p{margin:6px 0 0;font-size:13.5px;color:var(--ink);line-height:1.7}
.thought-foot{margin-top:auto;font-size:11px;color:var(--ink4);font-family:var(--mono)}
.inner-lower{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:22px}
.mini-panel h3{margin:0 0 14px;font-size:14px;font-weight:600}.mini-panel h3 small{color:var(--ink3);font-weight:400;margin-left:6px}
.appraisal-list,.impression-list{display:grid;gap:10px;max-height:330px;overflow-y:auto;scrollbar-width:thin}
.appraisal{padding:12px 14px;border-radius:14px;background:rgba(255,255,255,.03);border:1px solid var(--line)}
.ap-head{font-size:11px;color:var(--ink3);margin-bottom:6px;font-family:var(--mono)}
.hyp{display:grid;grid-template-columns:1fr 80px 38px;gap:10px;align-items:center;padding:4px 0}
.hyp-text{margin:0;font-size:13px;line-height:1.6}
.hyp-bar{height:4px;border-radius:4px;background:rgba(255,255,255,.08);overflow:hidden}
.hyp-bar i{display:block;height:100%;background:linear-gradient(90deg,var(--sky),var(--violet))}
.hyp-w{font-family:var(--mono);font-size:11.5px;color:var(--ink2);text-align:right}
.impression{padding:14px 16px;border-radius:14px;background:linear-gradient(160deg,rgba(255,207,134,.06),transparent);border:1px solid rgba(255,207,134,.14)}
.imp-when{font-size:11px;color:var(--ink3);font-family:var(--mono)}
.imp-text{margin:6px 0 0;font-family:var(--serif);font-size:13.5px;line-height:1.85;color:var(--ink)}

/* 03 fate --------------------------------------------------------------- */
.seg{display:inline-flex;padding:3px;border-radius:999px;background:var(--panel);border:1px solid var(--line)}
.seg-btn{padding:6px 14px;border:0;border-radius:999px;background:transparent;cursor:pointer;font-size:12.5px;color:var(--ink2)}
.seg-btn[aria-pressed="true"]{background:var(--violet);color:#16121f;font-weight:600}
.chapters{margin-bottom:16px}
.chapter-track{position:relative;height:58px;border-radius:16px;background:rgba(255,255,255,.025);border:1px solid var(--line)}
.chapter{position:absolute;top:6px;bottom:6px;display:flex;flex-direction:column;justify-content:center;align-items:flex-start;padding:0 12px;border:0;border-right:1px solid var(--line);background:transparent;cursor:pointer;text-align:left;overflow:hidden;transition:background .3s;min-width:0}
.chapter:hover{background:rgba(198,180,255,.08)}
.chapter.current{background:linear-gradient(90deg,rgba(198,180,255,.24),rgba(198,180,255,.06));border-radius:12px;box-shadow:inset 0 0 0 1px rgba(198,180,255,.45)}
.ch-no{font-family:var(--mono);font-size:9.5px;color:var(--ink3);letter-spacing:.1em}
.ch-title{font-size:12px;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.chapter.current .ch-title{color:#fff;font-weight:600}
.chapter-now{position:absolute;top:-6px;bottom:-6px;width:2px;background:var(--cream);box-shadow:0 0 12px var(--cream);pointer-events:none}
.chapter-now b{position:absolute;top:-18px;left:-16px;font-size:9px;letter-spacing:.2em;color:var(--cream)}
.route-wrap{display:grid;grid-template-columns:220px minmax(0,1fr);padding:0;background:radial-gradient(100% 120% at 30% 0%,rgba(198,180,255,.10),transparent 60%),linear-gradient(180deg,#0e1220,#0a0d16);border-color:rgba(198,180,255,.18)}
.route-gutter{position:relative;border-right:1px solid var(--line);background:rgba(8,10,18,.6);z-index:2}
.lane-card{position:absolute;left:12px;right:12px;height:60px;display:grid;grid-template-columns:28px 1fr;grid-template-rows:auto auto;column-gap:10px;align-items:center;padding:8px 10px;border-radius:14px;border:1px solid color-mix(in srgb,var(--lane) 35%,transparent);background:color-mix(in srgb,var(--lane) 7%,transparent);cursor:pointer;text-align:left;transition:all .3s var(--ease)}
.lane-card:hover{background:color-mix(in srgb,var(--lane) 16%,transparent);transform:translateX(3px)}
.lane-letter{grid-row:span 2;width:28px;height:28px;border-radius:9px;display:grid;place-items:center;font-family:var(--mono);font-weight:700;color:#14121a;background:var(--lane);box-shadow:0 0 14px color-mix(in srgb,var(--lane) 60%,transparent)}
.lane-title{font-size:12px;line-height:1.35;color:var(--ink);overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.lane-status{font-size:10.5px;color:var(--ink3)}
.route-viewport{overflow-x:auto;overflow-y:hidden;cursor:grab;scrollbar-width:thin;scrollbar-color:rgba(198,180,255,.3) transparent;outline:none}
.route-viewport.dragging{cursor:grabbing;user-select:none}
.route-svg{display:block}
.route-weekend{fill:rgba(255,255,255,.018)}
.route-dayline{stroke:rgba(255,255,255,.045)}.route-dayline.today{stroke:rgba(244,234,213,.25)}
.route-date{fill:var(--ink2);font-size:11px;font-family:var(--mono)}.route-date.today{fill:var(--cream);font-weight:700}
.route-wd{fill:var(--ink4);font-size:10px}
.route-stage{fill:rgba(198,180,255,.25)}.route-stage.current{fill:var(--violet)}
.route-past{fill:rgba(0,0,0,.22)}
.route-review line{stroke:var(--amber);stroke-dasharray:3 5;opacity:.7}.route-review text{fill:var(--amber);font-size:11px}
.lane-past{stroke-width:3;stroke-linecap:round;opacity:.95}
.lane-glow{stroke-width:7;opacity:.35}
.lane-future{stroke-width:2;stroke-dasharray:2 7;stroke-linecap:round;opacity:.55;animation:march 2.2s linear infinite}
@keyframes march{to{stroke-dashoffset:-18}}
.recap rect{fill:#0d1120;stroke-width:1.4}.recap text{fill:var(--ink);font-size:11px;text-anchor:middle}
.recap:hover rect{fill:#171c30}
.fork path{fill:none;stroke-width:1.4;stroke-dasharray:3 5;opacity:.5}
.question rect{fill:#0d1120;stroke-width:1.6}.question text{fill:#fff;font-weight:700;font-size:14px;text-anchor:middle}
.question:hover rect{fill:#1f1a33}
.node-window{opacity:.16}
.junction{fill:none;stroke-width:2;opacity:.65;stroke-dasharray:1 0}
.junction.out{stroke-dasharray:3 5;opacity:.45}
.prereq{fill:none;stroke:var(--cream);stroke-width:1.4;opacity:.7}
.route-now text{fill:#14121a;font-size:10px;font-weight:700;text-anchor:middle;letter-spacing:.15em}
.now-pill{fill:var(--cream)}
.now-pulse{fill:none;stroke:var(--cream);opacity:0;animation:nowpulse 2.6s ease-out infinite;transform-box:fill-box;transform-origin:center}
@keyframes nowpulse{0%{opacity:.7;transform:scale(.6)}100%{opacity:0;transform:scale(2.4)}}
.rnode{transition:transform .3s}
.rnode-halo{opacity:0;transition:opacity .3s}
.rnode:hover .rnode-halo,.rnode.sel .rnode-halo{opacity:.25}
.rnode.sel .rnode-halo{animation:breathe 2s infinite}
.rnode-ring{fill:#0b0f1a;stroke-width:2.6}
.rnode.candidate .rnode-ring{stroke-dasharray:4 3}
.rnode.ended{opacity:.5}
.rnode-date{font-family:var(--mono)}
.rnode-pill{fill:rgba(12,15,26,.88);stroke:rgba(255,255,255,.12)}
.rnode.sel .rnode-pill{stroke:var(--cream)}
.rnode-title{fill:var(--ink);font-size:11.5px}
.rnode-title .rnode-date{fill:var(--ink3);font-size:10px}
.galbox{position:relative;margin-top:-1px;padding:26px 30px 22px;border-radius:0 0 var(--r) var(--r);background:linear-gradient(180deg,rgba(18,16,34,.92),rgba(12,11,24,.96));border:1px solid rgba(198,180,255,.28);box-shadow:inset 0 1px 0 rgba(255,255,255,.06),0 20px 50px rgba(0,0,0,.35)}
.galbox::before{content:"";position:absolute;inset:6px;border:1px solid rgba(198,180,255,.12);border-radius:16px;pointer-events:none}
.galbox-name{position:absolute;top:-16px;left:26px;padding:5px 18px;border-radius:10px;font-family:var(--serif);font-size:14px;letter-spacing:.08em;color:#16121f;background:var(--lane,var(--violet));box-shadow:0 6px 20px rgba(0,0,0,.4);max-width:calc(100% - 52px);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.galbox-text{margin:6px 0 12px;min-height:5.8em;font-family:var(--serif);font-size:16px;line-height:1.95;color:#f6f3ff;white-space:pre-wrap;cursor:pointer}
.galbox-meta{display:flex;flex-wrap:wrap;gap:8px;padding-right:130px}
.galbox-nav{position:absolute;right:26px;bottom:18px;display:flex;align-items:center;gap:8px;font-family:var(--mono);font-size:11px;color:var(--ink3)}
.galbox-nav button{width:32px;height:32px;border-radius:50%;border:1px solid rgba(198,180,255,.35);background:transparent;color:var(--violet);cursor:pointer}
.galbox-nav button:hover{background:rgba(198,180,255,.12)}
.galbox-caret{position:absolute;right:30px;top:20px;color:var(--violet);font-size:11px;animation:caret 1.2s ease-in-out infinite}
@keyframes caret{50%{transform:translateY(4px);opacity:.4}}
.route-legend{display:flex;flex-wrap:wrap;gap:16px;margin-top:16px;align-items:center}
.route-legend .lg.realized{color:var(--jade)}.route-legend .lg.candidate{color:var(--violet)}.route-legend .lg.ended{color:var(--ink4)}.route-legend .lg.cross{color:var(--rose)}.route-legend .lg.fork{color:var(--amber)}
.lg-note{font-size:11.5px;color:var(--ink3)}

/* 04 week --------------------------------------------------------------- */
.week-nav{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.week-range{font-family:var(--mono);font-size:13px;color:var(--ink);padding:0 6px}
.pager{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink3);margin-left:6px}
.week-phases{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.phase{display:inline-flex;gap:8px;align-items:center;padding:6px 14px;border-radius:12px;border:1px solid rgba(198,180,255,.22);background:rgba(198,180,255,.06);cursor:pointer;font-size:12px;color:var(--ink2)}
.phase-k{color:var(--violet);font-size:11px;flex:none}
.phase.more{color:var(--violet);border-style:dashed}
.legend-chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.legend-chip{display:inline-flex;align-items:center;gap:7px;padding:5px 12px;border-radius:999px;border:1px solid var(--line2);background:transparent;cursor:pointer;font-size:12px;color:var(--ink2);transition:all .2s}
.legend-chip::before{content:"";width:9px;height:9px;border-radius:3px;background:var(--c)}
.legend-chip[aria-pressed="false"]{opacity:.38;text-decoration:line-through}
.legend-chip.k-planning{--c:var(--k-planning)}.legend-chip.k-plan{--c:var(--k-plan)}.legend-chip.k-experience{--c:var(--k-experience)}.legend-chip.k-world{--c:var(--k-world)}.legend-chip.k-expectation{--c:var(--k-expectation)}.legend-chip.k-revisit{--c:var(--k-revisit)}.legend-chip.k-commitment{--c:var(--k-commitment)}
.week-card{padding:14px 18px 18px}
.week-grid{min-width:760px}
.week-card{overflow-x:auto}
.week-axis{display:grid;grid-template-columns:86px 1fr;height:24px}
.week-ticks{position:relative}
.week-ticks span{position:absolute;transform:translateX(-50%);font-family:var(--mono);font-size:10.5px;color:var(--ink4)}
.week-row{display:grid;grid-template-columns:86px 1fr;border-top:1px solid var(--line)}
.week-row.today{background:linear-gradient(90deg,rgba(244,234,213,.06),transparent 70%)}
.week-day{display:flex;flex-direction:column;justify-content:center;padding:8px 0;gap:0}
.week-day strong{font-size:13px}.week-day span{font-family:var(--mono);font-size:11px;color:var(--ink3)}
.week-day em{font-style:normal;font-size:10px;color:#14121a;background:var(--cream);border-radius:6px;padding:0 6px;width:max-content;margin-top:3px}
.week-track{position:relative;background-image:linear-gradient(90deg,rgba(255,255,255,.045) 1px,transparent 1px);background-size:calc(100%/8) 100%}
.week-course,.week-act,.week-window,.week-dot,.week-unloaded{position:absolute;border:0;padding:0;cursor:pointer}
.week-course{height:20px;border-radius:6px;border:1px solid rgba(134,204,255,.45);background:rgba(134,204,255,.1);color:var(--k-planning);font-size:10.5px;overflow:hidden;text-align:left}
.week-course span,.week-act span{display:block;padding:0 7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:20px}
.week-act{height:20px;border-radius:999px;color:#0f1416;font-size:11px;text-align:left;overflow:hidden;background:var(--c);box-shadow:0 0 0 1px rgba(0,0,0,.25),0 4px 14px color-mix(in srgb,var(--c) 30%,transparent);transition:transform .2s,filter .2s;min-width:8px}
.week-act:hover{transform:translateY(-1px) scale(1.02);filter:brightness(1.1);z-index:3}
.week-act.k-plan{--c:var(--k-plan)}.week-act.k-experience{--c:var(--k-experience)}
.week-act.abandoned{background:transparent;color:var(--ink3);box-shadow:inset 0 0 0 1px rgba(126,232,198,.4);text-decoration:line-through}
.week-window{height:4px;border-radius:4px;background:var(--c);opacity:.75}
.week-window.k-expectation{--c:var(--k-expectation)}.week-window.k-revisit{--c:var(--k-revisit)}.week-window.k-commitment{--c:var(--k-commitment)}
.week-window:hover{opacity:1;height:6px}
.week-dot{width:7px;height:7px;margin-left:-3.5px;border-radius:50%;background:var(--k-world);box-shadow:0 0 8px var(--k-world)}
.week-dot:hover{transform:scale(1.6)}
.week-unloaded{top:2px;bottom:2px;border-radius:6px;background:repeating-linear-gradient(135deg,rgba(255,255,255,.05) 0 4px,transparent 4px 8px);border:1px dashed rgba(255,255,255,.12)}
.week-now{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--cream);box-shadow:0 0 10px var(--cream);z-index:4;pointer-events:none}
.week-now b{position:absolute;top:-1px;left:5px;font-family:var(--mono);font-size:9.5px;color:var(--cream)}
.week-foot{display:flex;gap:18px;flex-wrap:wrap;margin-top:12px;font-size:11.5px;color:var(--ink3)}
.week-foot .warn{color:var(--amber)}

/* 05 bonds / 06 mind ---------------------------------------------------- */
.duo{display:block}
.bonds-grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:22px}
.constellation{padding:12px;background:radial-gradient(70% 70% at 50% 50%,rgba(246,166,193,.08),transparent 70%),linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,.015))}
.constellation-svg{width:100%;height:auto}
.orbit{fill:none;stroke:rgba(255,255,255,.08);stroke-dasharray:2 6;transform-origin:center;transform-box:view-box;animation:spin var(--dur) linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.bond-edge{stroke:rgba(246,166,193,.35)}.bond-edge.you{stroke:var(--rose);opacity:.85}.bond-edge.far{stroke-dasharray:3 4;opacity:.45}
.float{animation:float 6s ease-in-out infinite;animation-delay:var(--d)}
@keyframes float{50%{transform:translateY(-4px)}}
.bond-halo{fill:rgba(246,166,193,.12);stroke:rgba(246,166,193,.35)}
.bond-core{fill:#f6c2d4}
.bond-node.you .bond-halo{fill:rgba(246,166,193,.22);stroke:var(--rose);animation:breathe 3s infinite}
.bond-node.you .bond-core{fill:#fff}
.bond-name{fill:var(--ink);font-size:13px;text-anchor:middle;font-weight:600}
.bond-name.me{font-family:var(--serif);font-size:14px;letter-spacing:.1em}
.bond-stage{fill:var(--ink3);font-size:10.5px;text-anchor:middle}
.bond-cluster circle{fill:rgba(255,255,255,.35)}.bond-cluster text{fill:var(--ink3);font-size:10.5px;text-anchor:middle}
.me-petals path{fill:var(--cream);opacity:.92}
.me-petals{animation:bloom 30s linear infinite;transform-box:fill-box;transform-origin:center}
.me-heart{fill:#e9c46a}
.map-note{margin:8px 4px 0;font-size:11px;color:var(--ink3)}
.bond-you{display:flex;flex-direction:column;gap:12px;background:radial-gradient(90% 60% at 100% 0%,rgba(246,166,193,.12),transparent 60%),linear-gradient(180deg,rgba(255,255,255,.045),rgba(255,255,255,.015))}
.bond-you .eyebrow{margin:0;color:var(--rose)}
.stage-name{margin:0;font-family:var(--serif);font-size:38px;font-weight:600;color:#fff;letter-spacing:.08em}
.radar{width:100%;max-width:280px;align-self:center;overflow:visible}
.radar-grid{fill:none;stroke:rgba(255,255,255,.08)}.radar-axis{stroke:rgba(255,255,255,.08)}
.radar-label{fill:var(--ink2);font-size:10.5px;text-anchor:middle}.radar-val{fill:var(--rose);font-size:10px;text-anchor:middle;font-family:var(--mono)}
.radar-shape{fill:rgba(246,166,193,.22);stroke:var(--rose);stroke-width:2;stroke-dasharray:900;stroke-dashoffset:900;animation:draw 2.2s var(--ease) .3s forwards}
@keyframes draw{to{stroke-dashoffset:0}}
.radar-dot{fill:#fff}
.radar-scale{fill:var(--ink4);font-size:9px;text-anchor:end}
.bond-quote{margin:0;padding:12px 14px;border-radius:14px;background:rgba(246,166,193,.06);border:1px solid rgba(246,166,193,.15)}
.bond-quote blockquote{margin:0;font-family:var(--serif);font-size:13.5px;line-height:1.75;color:#fff}
.bond-quote figcaption{margin-top:6px;font-size:11.5px;color:var(--ink3)}
.bond-threads{display:grid;gap:6px}
.thread-item,.adjust-line,.related{display:block;width:100%;text-align:left;padding:8px 12px;border-radius:10px;border:1px solid var(--line);background:rgba(255,255,255,.025);cursor:pointer;font-size:12.5px;color:var(--ink)}
.thread-item:hover,.adjust-line:hover,.related:hover{border-color:var(--line2);background:rgba(255,255,255,.05)}
.adjust-line{color:var(--ink3);font-size:11.5px}
.mind-grid{display:grid;grid-template-columns:minmax(260px,.8fr) minmax(0,1.25fr) minmax(0,1fr);gap:22px}
.mood{display:flex;flex-direction:column;align-items:center}
.mood .card-title{align-self:flex-start}
.orb-svg{width:200px;height:200px;overflow:visible}
.orb-aura{animation:breathe 4.5s ease-in-out infinite}
.orb-core{animation:orb 6s ease-in-out infinite;transform-origin:120px 120px}
@keyframes orb{50%{transform:scale(1.06)}}
.orb-ring{fill:none;stroke:rgba(255,255,255,.07)}
.orb-meter{fill:none;stroke-width:3;stroke-linecap:round}
.orb-label{margin:4px 0 0;font-family:var(--serif);font-size:24px;color:#fff;letter-spacing:.1em}
.orb-pct{margin:0;font-family:var(--mono);color:var(--amber);font-size:13px}
.mini-kpis{display:flex;gap:18px;justify-content:center;margin-top:10px;flex-wrap:wrap}
.mini-kpis div{display:grid;text-align:center}.mini-kpis strong{font-family:var(--mono);font-size:17px;color:#fff;font-weight:500}.mini-kpis span{font-size:11px;color:var(--ink3)}
.memory-svg{width:100%;height:auto}
.mem-bubble{fill:rgba(134,204,255,var(--a));stroke:rgba(134,204,255,.55);transition:all .3s}
.mem:hover .mem-bubble{fill:rgba(134,204,255,.45);stroke:#fff}
.mem-title{fill:#fff;font-size:11.5px;text-anchor:middle}.mem-val{fill:rgba(255,255,255,.65);font-size:10px;text-anchor:middle;font-family:var(--mono)}
.timeline{list-style:none;margin:0;padding:0 0 0 14px;border-left:1px solid var(--line2);display:grid;gap:14px}
.tl-item{position:relative;padding:2px 0 2px 10px;border-radius:10px}
.tl-item::before{content:"";position:absolute;left:-19px;top:8px;width:9px;height:9px;border-radius:50%;background:var(--amber);box-shadow:0 0 10px var(--amber)}
.tl-item time{font-family:var(--mono);font-size:10.5px;color:var(--ink3)}
.tl-item strong{display:inline-block;margin-left:8px;font-size:11px;color:var(--amber);font-weight:500}
.tl-item p{margin:3px 0 0;font-size:13px;line-height:1.7}
.tl-item:hover p{color:#fff}

/* 07 seen --------------------------------------------------------------- */
.ticker{display:flex;align-items:center;gap:14px;margin-bottom:18px;padding:10px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line);overflow:hidden;mask-image:linear-gradient(90deg,transparent,#000 8%,#000 92%,transparent)}
.ticker-label{position:relative;z-index:2;background:var(--bg);padding-right:12px;flex:none;font-size:11px;letter-spacing:.2em;color:var(--sky);padding-left:30px}
.ticker-lane{display:flex;gap:40px;white-space:nowrap;animation:tick var(--dur) linear infinite}
.ticker:hover .ticker-lane{animation-play-state:paused}
@keyframes tick{to{transform:translateX(-50%)}}
.tick-item{display:inline-flex;gap:8px;font-size:13px;color:var(--ink2)}
.tick-item b{font-size:9.5px;color:#0d1520;background:var(--sky);border-radius:4px;padding:1px 5px;align-self:center}
.seen-grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(320px,1fr);gap:22px;align-items:start}
.feed{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}
.video{display:flex;flex-direction:column;gap:8px;padding:18px;transition:transform .35s var(--ease),border-color .35s}
.video:hover{transform:translateY(-4px);border-color:rgba(134,204,255,.35)}
.video-head{display:flex;gap:8px;align-items:center;font-size:11px;color:var(--ink3)}
.video-head time{margin-left:auto;font-family:var(--mono)}
.video-badge{padding:1px 8px;border-radius:6px;background:#fb7299;color:#fff;font-weight:600;font-size:10.5px}
.video-cat{color:var(--sky)}
.video-title{margin:0;font-size:15px;line-height:1.45}
.video-up{margin:0;font-size:11.5px;color:var(--ink3)}
.video-take{margin:4px 0 0;padding:10px 12px;border-radius:12px;background:rgba(134,204,255,.06);border-left:2px solid var(--sky);font-family:var(--serif);font-size:13px;line-height:1.8;color:var(--ink)}
.video-foot{display:flex;align-items:center;gap:10px;margin-top:auto;flex-wrap:wrap}
.video-note summary{cursor:pointer;font-size:11.5px;color:var(--ink3)}.video-note p{margin:6px 0 0;font-size:12px;color:var(--ink2)}
.video-link{margin-left:auto;font-size:12px;color:var(--sky);text-decoration:none}
.seen-side{display:grid;gap:18px}
.photo-wall{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;padding:6px}
.polaroid{position:relative;min-width:0;display:flex;flex-direction:column;padding:8px 8px 10px;border:0;border-radius:4px;background:#f3eee3;color:#2a2620;cursor:zoom-in;transform:rotate(var(--r));box-shadow:0 12px 30px rgba(0,0,0,.45);transition:transform .4s var(--ease),box-shadow .4s}
.polaroid:hover{transform:rotate(0) scale(1.06);z-index:2;box-shadow:0 20px 40px rgba(0,0,0,.55)}
.polaroid img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:2px;background:#ddd}
.pol-cap{margin-top:6px;font-family:var(--serif);font-size:11px;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pol-meta{font-size:9.5px;color:#8a8070;text-align:left}
.voice-list{display:grid;gap:10px}
.voice{display:grid;grid-template-columns:40px 1fr auto;gap:12px;align-items:center;padding:10px 12px;border-radius:16px;background:rgba(255,255,255,.03);border:1px solid var(--line)}
.voice.playing{border-color:rgba(246,166,193,.4);background:rgba(246,166,193,.07)}
.voice-play{width:40px;height:40px;border-radius:50%;border:0;cursor:pointer;background:linear-gradient(135deg,var(--rose),#e98fb0);color:#2a1220;font-size:13px}
.voice-text{margin:0 0 6px;font-size:13px;line-height:1.5}
.wave{display:flex;align-items:center;gap:2px;height:22px}
.wave i{display:block;flex:1;max-width:4px;border-radius:2px;background:rgba(255,255,255,.22);transform-origin:center}
.voice.playing .wave i{background:var(--rose);animation:eq 1s ease-in-out infinite alternate}
.voice.playing .wave i:nth-child(3n){animation-delay:.2s}.voice.playing .wave i:nth-child(4n){animation-delay:.45s}
@keyframes eq{to{transform:scaleY(.4)}}
.voice-dur{font-family:var(--mono);font-size:11px;color:var(--ink3)}

/* 08 station ------------------------------------------------------------ */
.ix-badges{display:flex;gap:8px;flex-wrap:wrap}
.station-grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(320px,1fr);gap:22px;align-items:start}
.ix-list{display:grid;gap:14px;max-height:640px;overflow-y:auto;scrollbar-width:thin}
.ix-item{display:flex;flex-direction:column;gap:8px}
.ix-item.s-in_transit{border-color:rgba(134,204,255,.35)}.ix-item.s-ready_for_pickup{border-color:rgba(255,207,134,.4)}
.ix-top{display:flex;justify-content:space-between;align-items:center}
.ix-dir{font-family:var(--mono);font-size:11px;color:var(--ink3)}
.ix-title{margin:0;font-size:16px}.ix-detail{margin:0;font-size:13px;color:var(--ink2)}
.ix-note{margin:0;padding:8px 12px;border-radius:10px;background:rgba(255,207,134,.07);border-left:2px solid var(--amber);font-size:12.5px;font-family:var(--serif)}
.ix-note.r{border-color:var(--jade);background:rgba(126,232,198,.07)}
.ix-steps{display:flex;list-style:none;margin:6px 0 0;padding:0;counter-reset:s}
.ix-steps li{flex:1;position:relative;padding-top:16px;font-size:11px;color:var(--ink4);text-align:center}
.ix-steps li::before{content:"";position:absolute;top:4px;left:50%;width:9px;height:9px;margin-left:-4.5px;border-radius:50%;background:var(--ink4);z-index:1}
.ix-steps li::after{content:"";position:absolute;top:8px;left:-50%;width:100%;height:1px;background:var(--ink4)}
.ix-steps li:first-child::after{display:none}
.ix-steps li.done{color:var(--jade)}.ix-steps li.done::before{background:var(--jade);box-shadow:0 0 8px var(--jade)}.ix-steps li.done::after{background:var(--jade)}
.ix-meta{display:flex;gap:14px;flex-wrap:wrap;font-size:11.5px;color:var(--ink3)}
.ix-progress{height:4px;border-radius:4px;background:rgba(255,255,255,.08);overflow:hidden}.ix-progress i{display:block;height:100%;background:linear-gradient(90deg,var(--sky),var(--jade))}
.ix-outcome{margin:0;padding:10px 12px;border-radius:12px;background:rgba(126,232,198,.06);border:1px solid rgba(126,232,198,.18);font-size:12.5px;line-height:1.7}
.ix-input{width:100%;padding:9px 12px;border-radius:10px;border:1px solid var(--line2);background:rgba(0,0,0,.25)}
.ix-form{display:grid;gap:12px}
.ix-form label{display:grid;gap:5px;font-size:12px;color:var(--ink3)}
.ix-form input,.ix-form select{width:100%;padding:10px 12px;border-radius:12px;border:1px solid var(--line2);background:rgba(0,0,0,.28);color:var(--ink)}
.ix-form input:focus,.ix-form select:focus{border-color:var(--cream);outline:none}
.ix-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.ix-presets{display:flex;gap:6px;flex-wrap:wrap}
.ix-preset{padding:5px 11px;border-radius:999px;border:1px solid var(--line2);background:transparent;cursor:pointer;font-size:12px}
.ix-preset:hover{border-color:var(--cream)}
.ix-feedback{margin:0;font-size:12.5px;color:var(--jade)}

/* 09 engine ------------------------------------------------------------- */
.engine-top{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(280px,.8fr);gap:18px}
.mech{padding:14px}
.mech-svg{width:100%;height:auto}
.mech-path{fill:none;stroke:rgba(255,255,255,.12);stroke-width:1.4}
.mech-flow{fill:none;stroke:var(--jade);stroke-width:2;stroke-dasharray:3 22;stroke-linecap:round;animation:flow 2.4s linear infinite;animation-delay:var(--d);opacity:.85}
.mech-flow.back{stroke:var(--amber)}
@keyframes flow{to{stroke-dashoffset:-50}}
.mech-caption{fill:var(--ink3);font-size:11px;text-anchor:middle}
.mech-node rect{fill:rgba(16,22,32,.92);stroke:rgba(255,255,255,.12);transition:all .3s}
.mech-node:hover rect,.mech-node.sel rect{stroke:var(--cream)}
.mech-node.role rect{fill:url(#roleFill);stroke:rgba(246,166,193,.6);filter:drop-shadow(0 0 18px rgba(246,166,193,.25))}
.mech-node.shield rect{stroke:rgba(255,207,134,.45);stroke-dasharray:4 3}
.mech-k{fill:var(--ink4);font-size:9.5px;letter-spacing:.12em;font-family:var(--mono)}
.mech-node.role .mech-k{fill:var(--rose)}
.mech-t{fill:#fff;font-size:15px;font-weight:600}.mech-node.role .mech-t{font-size:18px}
.mech-s{fill:var(--ink3);font-size:11px}.mech-c{fill:var(--jade);font-size:11px;font-family:var(--mono)}
.mech-detail{display:flex;flex-direction:column;gap:10px}
.mech-detail h3{margin:0;font-family:var(--serif);font-size:22px}
.mech-detail p{margin:0;font-size:13px;line-height:1.75;color:var(--ink2)}
.mech-detail .limit{font-size:12px;color:var(--ink3);padding:10px 12px;border-radius:12px;background:rgba(255,207,134,.05);border:1px solid rgba(255,207,134,.15)}
.mech-auth{align-self:flex-start;font-size:11px;padding:3px 10px;border-radius:999px;background:rgba(126,232,198,.1);color:var(--jade)}
.mech-auth.role{background:rgba(246,166,193,.12);color:var(--rose)}
.mech-ev{list-style:none;margin:0;padding:0;display:grid;gap:6px}
.mech-ev li{display:flex;justify-content:space-between;gap:10px;padding:7px 10px;border-radius:10px;background:rgba(255,255,255,.03);font-size:12px}
.mech-ev time{font-family:var(--mono);color:var(--ink3);font-size:11px;white-space:nowrap}
.engine-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-top:18px}
.signals{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.signal{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:10px;background:rgba(255,255,255,.025);font-size:12px}
.signal em{margin-left:auto;font-style:normal;font-size:11px;color:var(--ink3)}
.light{width:8px;height:8px;border-radius:50%;background:var(--ink4);flex:none}
.signal[data-state="ready"] .light{background:var(--jade);box-shadow:0 0 8px var(--jade)}
.signal[data-state="warming"] .light{background:var(--sky);animation:breathe 1.6s infinite}
.signal[data-state="degraded"] .light,.signal[data-state="stale"] .light{background:var(--amber);box-shadow:0 0 8px var(--amber)}
.signal[data-state="unavailable"] .light{background:var(--coral)}
.notice{grid-column:1/-1;margin:4px 0 0;font-size:11.5px;color:var(--amber)}
.process-bars{display:grid;gap:7px}
.proc{display:grid;grid-template-columns:110px 1fr 46px;gap:10px;align-items:center;font-size:12px}
.proc-l{color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.proc-track{height:8px;border-radius:8px;background:rgba(255,255,255,.04)}
.proc-bar{display:flex;height:100%;border-radius:8px;overflow:hidden;transform-origin:left;animation:grow 1.4s var(--ease) both}
@keyframes grow{from{transform:scaleX(0)}}
.proc-bar i{display:block;height:100%}.proc-bar .term{background:var(--jade)}.proc-bar .claim{background:var(--amber)}.proc-bar .open{background:var(--rose)}
.proc-n{font-family:var(--mono);font-size:11px;color:var(--ink3);text-align:right}
.ledger-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.ledger-cell{padding:12px;border-radius:12px;background:rgba(255,255,255,.025);border:1px solid var(--line)}
.ledger-cell strong{display:block;font-family:var(--mono);font-size:20px;font-weight:500;color:#fff}
.ledger-cell span{font-size:11.5px;color:var(--ink3)}
.qual{margin:10px 0 0;font-size:11.5px;color:var(--amber)}
.cost{margin-top:18px}
.cost-head{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:10px}
.cost-head .card-title{margin:0}
.cost-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0 18px}
.cost-kpi{padding:14px;border-radius:14px;background:rgba(255,255,255,.025);border:1px solid var(--line);display:grid;gap:2px}
.cost-kpi span{font-size:11.5px;color:var(--ink3)}.cost-kpi strong{font-family:var(--mono);font-size:22px;font-weight:500;color:#fff}.cost-kpi small{font-size:11px;color:var(--ink3)}
.cost-body{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(260px,1fr);gap:22px}
.cost-svg{width:100%;height:auto}
.cost-grid{stroke:rgba(255,255,255,.06);stroke-dasharray:3 5}
.cost-axis{fill:var(--ink4);font-size:10px;text-anchor:middle;font-family:var(--mono)}.cost-axis.end{text-anchor:end}
.cost-hit{fill:transparent}
.cost-bg{fill:var(--jade);opacity:.75;transform-origin:bottom;transform-box:fill-box;animation:rise 1.2s var(--ease) both}
.cost-fg{fill:var(--amber)}
@keyframes rise{from{transform:scaleY(0)}}
.cost-bar:hover .cost-bg,.cost-bar.sel .cost-bg{opacity:1}
.cost-bar.sel .cost-hit{fill:rgba(255,255,255,.04)}
.cost-day{margin:6px 0 0;font-size:12px;color:var(--ink2);font-family:var(--mono)}
.cost-recent{margin-top:18px;display:grid;gap:4px}
.cost-rec{display:grid;grid-template-columns:118px minmax(0,1fr) 120px 60px 72px;gap:10px;font-size:12px;padding:6px 0;border-bottom:1px dashed var(--line);align-items:center}
.cost-rec time{font-family:var(--mono);font-size:11px;color:var(--ink3);white-space:nowrap}
.cost-rec span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cost-rec strong{text-align:right;font-weight:500}
.cost-models{display:grid;gap:8px;align-content:start}
.cost-model{display:grid;grid-template-columns:1fr auto;grid-template-areas:"n m" "b v";gap:2px 10px;font-size:12px}
.cost-model strong{grid-area:n;font-weight:500}.cost-model .muted{grid-area:m;font-size:11px}
.cost-model-bar{grid-area:b;height:4px;border-radius:4px;background:rgba(255,255,255,.06);align-self:center;overflow:hidden}.cost-model-bar i{display:block;height:100%}
.cost-model .cost-num{grid-area:v}
.cost-num{font-family:var(--mono);font-size:11.5px;color:var(--ink2)}
.cost-purpose{display:flex;justify-content:space-between;font-size:12px;color:var(--ink2);padding:3px 0;border-bottom:1px dashed var(--line)}
.evidence{margin-top:18px}
.evidence-tools{display:grid;grid-template-columns:minmax(0,1fr) 180px 180px;gap:10px;margin-bottom:8px}
.evidence-tools input,.evidence-tools select{padding:9px 12px;border-radius:12px;border:1px solid var(--line2);background:rgba(0,0,0,.25);color:var(--ink)}
.evidence-list{display:grid;gap:6px}
.ev{border-radius:12px;border:1px solid var(--line);background:rgba(255,255,255,.02)}
.ev summary{display:grid;grid-template-columns:110px minmax(0,1fr) auto;gap:12px;padding:10px 14px;cursor:pointer;list-style:none;font-size:12.5px}
.ev summary::-webkit-details-marker{display:none}
.ev-k{color:var(--jade);font-size:11.5px}.ev-t{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ev-m{font-size:11px;color:var(--ink3);font-family:var(--mono)}
.ev-body{padding:0 14px 12px;font-size:12.5px;color:var(--ink2)}
.evidence-pager{display:flex;justify-content:center;align-items:center;gap:12px;margin-top:12px;font-family:var(--mono);font-size:12px;color:var(--ink3)}
.tech{margin-top:14px}.tech summary{cursor:pointer;font-size:12px;color:var(--ink3)}
.authority{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.coverage{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:4px 16px;margin-top:10px;font-size:11.5px;color:var(--ink3);font-family:var(--mono)}

/* detail dialog / lightbox ------------------------------------------------ */
.detail{width:min(640px,calc(100% - 32px));max-height:min(80vh,820px);padding:0;border:1px solid var(--line2);border-radius:24px;background:linear-gradient(180deg,#121722,#0c1018);color:var(--ink);box-shadow:0 40px 120px rgba(0,0,0,.6)}
.detail[open]{animation:dlg .45s var(--ease)}
@keyframes dlg{from{opacity:0;transform:translateY(16px) scale(.98)}}
.detail::backdrop{background:rgba(4,6,10,.6);backdrop-filter:blur(6px)}
.detail-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;padding:22px 24px 14px;border-bottom:1px solid var(--line)}
.detail-head h2{margin:0;font-family:var(--serif);font-size:20px;line-height:1.45}
.detail-head .eyebrow{margin-bottom:6px}
.detail-body{padding:18px 24px 24px;overflow-y:auto;max-height:calc(80vh - 90px)}
.detail-tags{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}
.reading{margin:0;font-family:var(--serif);font-size:15px;line-height:1.95;white-space:pre-wrap}
.detail-title{margin:0 0 10px;font-size:16px;font-weight:600;line-height:1.6}
.reading-label{display:inline-block;font-size:11px;color:var(--amber);margin-bottom:6px}
.detail-values{display:grid;grid-template-columns:auto 1fr;gap:6px 14px;margin:16px 0 0;font-size:12.5px}
.detail-values dt{color:var(--ink3)}.detail-values dd{margin:0;color:var(--ink)}
.detail-sub{margin:18px 0 8px;font-size:12px;color:var(--ink3);font-weight:500}
.related{margin-top:6px}
.provenance{margin:18px 0 0;font-size:11px;color:var(--ink4);font-family:var(--mono)}
.structured summary{cursor:pointer;color:var(--ink3);font-size:12px}.structured pre{white-space:pre-wrap;font-size:11px;color:var(--ink3)}
.lightbox{position:fixed;inset:0;z-index:80;display:grid;place-items:center;background:rgba(4,6,10,.82);backdrop-filter:blur(8px);animation:dlg .3s}
.lightbox-fig{margin:0;max-width:min(900px,92vw);display:grid;gap:12px;justify-items:center}
.lightbox-fig img{max-width:100%;max-height:74vh;border-radius:8px;box-shadow:0 30px 80px rgba(0,0,0,.6)}
.lightbox-fig figcaption{display:grid;text-align:center;gap:2px;font-size:13px}.lightbox-fig figcaption span{color:var(--ink3);font-size:12px}

/* footer, presentation ------------------------------------------------- */
.footer{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-top:70px;padding-top:22px;border-top:1px solid var(--line);font-size:11.5px;color:var(--ink4)}
#footerMeta{font-family:var(--mono)}
.present-hud{position:fixed;right:24px;bottom:20px;z-index:60;display:flex;gap:14px;padding:8px 16px;border-radius:999px;background:rgba(8,11,17,.7);border:1px solid var(--line2);backdrop-filter:blur(12px);font-size:12px;color:var(--ink3)}
.present-hud #presentIndex{font-family:var(--mono);color:var(--cream)}
body.presenting .operator-only{display:none!important}
body.presenting .topbar{background:rgba(8,11,17,.35)}
body.presenting .scene{min-height:calc(100vh - 70px);display:flex;flex-direction:column;justify-content:center}
body.presenting .duo{display:block}
body.presenting .scene-head h2{font-size:clamp(36px,4vw,58px)}
body.presenting{font-size:15px}
body.paused .sync-dot{animation:none;background:var(--ink3)}

/* responsive ------------------------------------------------------------- */
@media(max-width:1180px){
  .topbar{grid-template-columns:auto 1fr;}
  .top-tools{grid-column:1/-1;justify-content:flex-end;flex-wrap:wrap}
  .duo,.inner-grid,.seen-grid,.station-grid,.engine-top{grid-template-columns:1fr}
  .engine-grid{grid-template-columns:1fr 1fr}
  .odometer{grid-template-columns:repeat(3,1fr);row-gap:18px}
  .odo:nth-child(4){border-left:0;padding-left:0}
}
@media(max-width:820px){
  .hero-grid,.bonds-grid,.mind-grid,.inner-lower,.cost-body,.thought-pair{grid-template-columns:1fr}
  .engine-grid,.cost-kpis{grid-template-columns:1fr 1fr}
  .lane-title{-webkit-line-clamp:3;font-size:10.5px}
  .evidence-tools{grid-template-columns:1fr}
  .top-clock,.nav{display:none}
  .hero{padding:36px 22px 24px;min-height:0}
  .wrap>*,.scene,.card,.hero-grid>*,.inner-grid>*,.seen-grid>*,.station-grid>*{min-width:0}
  .hero-context{grid-template-columns:1fr}
  .hero-name em{font-size:13px}
  .odometer{grid-template-columns:repeat(2,1fr)}
  .odo{padding:0 12px}.odo:nth-child(odd){border-left:0;padding-left:0}
  .phase{max-width:100%;min-width:0}.phase-t{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .route-wrap{grid-template-columns:92px minmax(0,1fr)}
  .lane-card{left:6px;right:6px;grid-template-columns:24px 1fr;padding:6px}
  .lane-letter{width:24px;height:24px}.lane-status{display:none}
  .galbox{padding:24px 18px 18px}.galbox-meta{padding-right:0}.galbox-nav{position:static;justify-content:flex-end;margin-top:12px}
  .galbox-text{font-size:15px}
  .top-tools{gap:6px}.tool{padding:6px 10px}
  .photo-wall{grid-template-columns:repeat(2,1fr)}
  .cost-rec{grid-template-columns:72px minmax(0,1fr) 64px}.cost-rec span.muted,.cost-rec span.cost-num{display:none}
  .ix-row{grid-template-columns:1fr}
  .scene-head h2{font-size:30px}
}
@media(prefers-reduced-motion:reduce){
  *,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}
  .reveal{opacity:1;transform:none}
  .radar-shape{stroke-dashoffset:0}
}
"""
