"""Single-page dashboard presentation. No character or World decisions."""

DASHBOARD_CSS = """
:root {
  font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif;
  font-synthesis: none;
  color: #213f3a;
  background: #eff5f3;
  --ink:#213f3a;
  --ink-soft:#46635b;
  --muted:#71867e;
  --line:#dbe7e1;
  --line-strong:#b6cdc2;
  --paper:#fbfdfc;
  --paper-tint:#f3f8f6;
  --green:#1d8072;
  --green-deep:#134a40;
  --soft:#e5f1ed;
  --amber:#ac813f;
  --shadow:0 4px 22px #193f3010;
  --gutter:32px;
}

* {
  box-sizing:border-box
}

body {
  margin:0;
  min-height:100vh;
  background:radial-gradient(ellipse at 100% 0,#d5ebe3 0,transparent 45%),#eff5f3
}

button,input,select {
  font:inherit;
  color:inherit
}

button,summary,select {
  cursor:pointer
}

button,a {
  touch-action:manipulation;
  -webkit-tap-highlight-color:transparent
}

button:disabled {
  opacity:.45;
  cursor:default
}

button:focus-visible,a:focus-visible,input:focus-visible,summary:focus-visible,select:focus-visible,[tabindex]:focus-visible {
  outline:2px solid #259d88;
  outline-offset:4px
}

[hidden] {
  display:none!important
}

h1,h2,h3,h4,p {
  overflow-wrap:anywhere
}

p {
  margin:0
}

svg {
  max-width:100%
}

::selection {
  background:#b7e4d6;
  color:#173f33
}

html {
  scroll-padding-top:100px
}

::-webkit-scrollbar {
  width:5px;
  height:5px
}

::-webkit-scrollbar-thumb {
  background:#b3cdc0;
  border-radius:8px
}

.bar {
  height:76px;
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:16px;
  padding:0 28px;
  border-bottom:1px solid #d9e6e1;
  background:#f8fcfbed;
  backdrop-filter:blur(18px);
  position:sticky;
  top:0;
  z-index:40
}

.identity {
  display:flex;
  align-items:center;
  gap:12px;
  min-width:0
}

.monogram {
  display:grid;
  place-items:center;
  flex:none;
  width:40px;
  height:40px;
  border-radius:13px;
  background:#19594b;
  color:#e9fff5;
  font:22px "Songti SC",serif;
  box-shadow:0 4px 12px #19594b25
}

.bar h1 {
  font-size:15px;
  font-weight:600;
  margin:0;
  letter-spacing:.07em
}

.eyebrow {
  font-size:8px;
  letter-spacing:.24em;
  margin-top:5px;
  color:var(--muted)
}

.bar-actions {
  display:flex;
  align-items:center;
  gap:16px;
  flex:none
}

.button {
  padding:7px 12px;
  border-radius:9px;
  border:1px solid var(--line-strong);
  background:var(--paper);
  font-size:11px;
  line-height:1.5;
  white-space:nowrap;
  transition:background .18s,border-color .18s
}

.button:hover:not(:disabled) {
  border-color:#55a58e;
  background:#e8f4f0
}

.button.primary {
  background:#1b5f51;
  border-color:#1b5f51;
  color:#f6fffc
}

.logout {
  margin:0
}

.logout button {
  background:none;
  border:0;
  font-size:11px;
  color:var(--muted);
  padding:8px 0
}

.status-bar,.capture-notice,.recording-tools,.wrap {
  width:calc(100% - 272px);
  max-width:1440px;
  margin-left:240px;
  margin-right:32px
}

.status-bar {
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:12px;
  padding:22px 0 0;
  color:var(--muted);
  font-size:11px;
  flex-wrap:wrap
}

.status-meta,.view-tools {
  display:flex;
  align-items:center;
  gap:16px;
  flex-wrap:wrap
}

.capture-state {
  display:inline-flex;
  align-items:center;
  gap:7px;
  color:#387d69;
  font-size:11px
}

.capture-state:before {
  content:"";
  width:6px;
  height:6px;
  border-radius:50%;
  background:#269e80;
  box-shadow:0 0 0 3px #269e8012
}

.capture-state.error {
  color:#9c642e
}

.capture-state.error:before {
  background:#b78243;
  box-shadow:none
}

.header-clock {
  font-size:11px;
  font-variant-numeric:tabular-nums;
  margin:0;
  color:#6c837b
}

.readonly {
  font-size:10px
}

.view-tools .button {
  font-size:10px;
  padding:4px 9px;
  background:transparent;
  border-color:#d3e0db
}

.capture-notice,.agenda-warning {
  background:#fbf3e5;
  color:#94662c;
  border:1px solid #e7d4ad;
  border-radius:10px;
  padding:10px 14px;
  font-size:12px;
  line-height:1.7
}

.capture-notice {
  margin-top:14px
}

.recording-tools {
  padding:12px;
  margin-top:12px;
  background:var(--soft);
  display:flex;
  flex-wrap:wrap;
  gap:12px;
  border-radius:10px;
  font-size:12px
}

.recording-tools select {
  max-width:100%
}

.wrap {
  display:flex;
  flex-direction:column;
  gap:22px;
  padding:28px 0 28px;
  min-width:0
}

.page-intro {
  display:flex;
  align-items:flex-end;
  justify-content:space-between;
  gap:20px
}

.page-intro h2 {
  font:600 29px/1.3 "Songti SC","STSong",serif;
  letter-spacing:.06em;
  margin:0;
  color:var(--green-deep)
}

.page-intro p {
  font-size:12px;
  line-height:1.7;
  color:var(--muted);
  margin:8px 0 0
}

.page-index {
  font-size:9px;
  letter-spacing:.16em;
  color:#90a59d;
  white-space:nowrap
}

.panel,.section-card {
  min-width:0;
  border:1px solid var(--line);
  border-radius:16px;
  background:var(--paper);
  box-shadow:var(--shadow)
}

.section-card {
  padding:22px;
  grid-column:span 6;
  align-self:start
}

.section-head {
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  margin-bottom:18px
}

.section-head h2 {
  font-size:14px;
  font-weight:600;
  letter-spacing:.04em;
  color:#244e41;
  margin:0
}

.section-state {
  font-size:10px;
  background:var(--soft);
  border-radius:6px;
  padding:3px 7px;
  color:#4f7e69;
  white-space:nowrap
}

.section-state[data-state="ready"] {
  display:none
}

.section-state[data-state="unavailable"],.section-state[data-state="stale"],.section-state[data-state="degraded"] {
  color:#976a34;
  background:#f7efdf
}

.section-body {
  display:flex;
  flex-direction:column;
  gap:14px;
  min-width:0
}

.section-note,.truncated {
  font-size:10px;
  line-height:1.7;
  color:var(--muted);
  margin:0
}

.section-subtitle {
  font-size:12px;
  font-weight:600;
  margin:0 0 10px;
  color:#3e6b58;
  letter-spacing:.03em
}

.empty-state {
  grid-column:1/-1;
  padding:14px 0;
  font-size:12px;
  line-height:1.8;
  color:#80968e;
  margin:0
}

.section-grid {
  display:grid;
  grid-template-columns:repeat(12,minmax(0,1fr));
  gap:20px;
  align-items:start
}

.visual-studio {
  display:contents
}

.section-card[data-focus-area="media"],.section-card[data-focus-area="perception"] {
  grid-column:1/-1
}

.domain-details {
  min-width:0
}

#domainDetails>summary {
  display:none
}

.footer {
  display:flex;
  justify-content:space-between;
  flex-wrap:wrap;
  gap:12px;
  border-top:1px solid var(--line);
  padding-top:20px;
  font-size:10px;
  color:#869b93;
  line-height:1.7
}

/* The present is a reading space, with a truthful world-time instrument. */
.now-panel {
  padding:24px 30px;
  color:#e8faf3;
  background:radial-gradient(ellipse at 80% 10%,#2b80745c,transparent 65%),linear-gradient(115deg,#143f37,#1c5c4e);
  border-color:#225e50;
  position:relative;
  isolation:isolate;
  overflow:hidden
}

.now-panel:after {
  content:"";
  position:absolute;
  inset:0;
  z-index:-1;
  pointer-events:none;
  background-image:radial-gradient(#bdebda1c .65px,transparent .65px);
  background-size:18px 18px;
  mask-image:linear-gradient(90deg,transparent,black)
}

.now-panel>div:first-child {
  display:flex;
  align-items:center;
  flex-wrap:wrap;
  gap:12px
}

.hero-label {
  font-size:9px;
  letter-spacing:.2em;
  font-weight:500;
  margin:0;
  color:#6e9b86
}

.now-panel .hero-label {
  color:#83cdb2
}

.now-panel h2 {
  font:500 16px "Songti SC",serif;
  color:#deefe9;
  margin:0
}

.hero-note {
  font-size:10px;
  color:#9dbeb0;
  padding-left:12px;
  border-left:1px solid #96c7b338
}

.world-clock {
  font-size:10px;
  color:#96b9a9;
  margin-left:auto;
  font-variant-numeric:tabular-nums
}

.now-story {
  display:grid;
  grid-template-columns:minmax(0,1fr) 206px;
  gap:16px 40px;
  align-items:center;
  margin-top:8px
}

.present-main {
  min-width:0;
  padding:18px 0 8px
}

.present-label {
  display:block;
  font-size:9px;
  letter-spacing:.12em;
  color:#83cdb2;
  margin-bottom:10px
}

.present-title {
  font-size:clamp(17px,1.6vw,24px);
  font-weight:500;
  line-height:1.75;
  letter-spacing:.015em;
  color:#f4fcf9
}

.present-main>.prose-disclosure {
  font-size:19px;
  color:#f4fcf9;
  line-height:1.8
}

.day-orbit {
  width:206px;
  color:#b8e7d4;
  display:block
}

.orbit-caption {
  font:8px system-ui;
  letter-spacing:2px;
  fill:#9ac8b5
}

.orbit-time {
  font:300 37px system-ui;
  fill:#edfff7;
  letter-spacing:1px
}

.orbit-zone {
  font-size:8px;
  fill:#9ac8b5
}

.orbit-dot {
  filter:drop-shadow(0 0 5px #88f5ce);
  animation:orbit-breathe 4s ease-in-out infinite
}

.now-context-detail {
  margin-top:14px;
  font-size:11px;
  color:#a9cabb
}

.now-context-detail summary {
  width:fit-content
}

.now-context-detail p {
  margin-top:10px;
  color:#d1e6de;
  line-height:1.8
}

.now-context {
  display:flex;
  gap:18px;
  flex-wrap:wrap;
  margin-top:18px;
  padding-top:14px;
  border-top:1px solid #b2d7c522;
  font-size:11px;
  color:#b9d8cd
}

.now-context>details {
  flex:1;
  min-width:150px
}

.now-context summary {
  font-size:11px
}

.now-context p {
  font-size:12px;
  line-height:1.8;
  margin-top:10px;
  color:#deeee8
}

.present-expectation {
  grid-column:1/-1;
  font-size:11px;
  border-top:1px solid #b2d7c522;
  padding-top:12px;
  color:#bad9ce;
  line-height:1.8
}

/* Reading details stay available without taking over the overview. */
.highlight-list {
  display:grid;
  gap:16px;
  min-width:0
}

.highlight {
  min-width:0;
  position:relative;
  border:0;
  border-bottom:1px solid #e7eeeb;
  padding:0 0 18px
}

.highlight:last-child {
  border-bottom:0
}

.highlight-top {
  display:flex;
  gap:8px;
  align-items:center;
  justify-content:space-between;
  margin-bottom:8px
}

.kind-tag {
  font-size:10px;
  color:#3b8b73
}

.when {
  font-size:10px;
  color:#8a9c95;
  font-variant-numeric:tabular-nums;
  flex:none
}

.highlight h3 {
  font-size:14px;
  font-weight:550;
  line-height:1.7;
  margin:5px 0 8px;
  color:#264b3b
}

.highlight .detail {
  font-size:13px;
  line-height:1.95;
  margin:8px 0 0;
  white-space:pre-wrap
}

.status-pill {
  display:inline-block;
  font-size:9px;
  line-height:1.5;
  padding:2px 6px;
  border-radius:5px;
  color:#5e8170;
  background:#edf4f1;
  margin-bottom:3px
}

.reading-label {
  display:block;
  font-size:10px;
  line-height:1.7;
  color:#8d7548;
  margin-top:8px
}

.structured-detail {
  font-size:11px;
  color:#7a8e87;
  margin-top:8px
}

.structured-detail pre {
  white-space:pre-wrap;
  overflow-wrap:anywhere;
  font:11px/1.8 ui-monospace,monospace;
  max-height:220px;
  overflow:auto;
  padding:12px;
  background:#edf3f1;
  border-radius:8px
}

.prose-disclosure {
  font-size:12px;
  line-height:1.95;
  margin-top:6px
}

.prose-disclosure>summary {
  display:block;
  list-style:none
}

.prose-disclosure>summary::-webkit-details-marker {
  display:none
}

.prose-toggle {
  display:block;
  font-size:10px;
  color:#59937c;
  margin-top:5px
}

.prose-disclosure[open] .prose-preview {
  display:none
}

.prose-disclosure[open] .prose-toggle {
  font-size:0
}

.prose-disclosure[open] .prose-toggle:after {
  content:"收起全文";
  font-size:10px
}

.prose-disclosure[open]>p {
  margin-top:8px
}

.more-records>summary,.relationship-dimensions>summary,.pending-disclosure>summary,.agenda-fold>summary {
  font-size:10px;
  color:#5d8b75;
  padding:7px 0;
  line-height:1.7
}

.more-records .highlight-list {
  margin-top:14px;
  max-height:540px;
  overflow:auto;
  padding-right:8px
}

.chips {
  display:flex;
  flex-wrap:wrap;
  gap:6px;
  margin-top:9px
}

.chip {
  font-size:10px;
  line-height:1.6;
  color:#68857a;
  padding:3px 7px;
  background:#edf4f1;
  border-radius:5px;
  overflow-wrap:anywhere
}

.chip-link {
  text-decoration:none;
  color:#21866b
}

.recent-timeline>.highlight-list {
  position:relative;
  padding-left:24px
}

.recent-timeline>.highlight-list:before {
  content:"";
  position:absolute;
  left:4px;
  top:5px;
  bottom:20px;
  border-left:1px solid #c8dfd7
}

.recent-timeline>.highlight-list>.highlight:before {
  content:"";
  position:absolute;
  left:-24px;
  top:5px;
  width:9px;
  height:9px;
  border-radius:50%;
  background:#c1dbd1;
  border:2px solid #fafdfc
}

.recent-timeline>.highlight-list>.highlight:first-child:before {
  background:#29957b;
  box-shadow:0 0 0 3px #32967a17
}

.section-card[data-focus-area="recent"] .highlight h3 {
  font-size:14px
}

.section-card[data-focus-area="recent"] .detail {
  color:#526b62
}

/* Relationship and emotion are distinct, compact records. */
.radar-wrap {
  display:grid;
  grid-template-columns:minmax(140px,.95fr) minmax(110px,1fr);
  align-items:center;
  gap:14px
}

.radar-svg {
  width:100%;
  max-height:190px
}

.stage-hero {
  display:flex;
  flex-direction:column;
  gap:8px
}

.stage-hero span {
  font-size:10px;
  color:var(--muted)
}

.stage-hero strong {
  font:600 28px "Songti SC",serif;
  color:#266650
}

.radar-summary>.section-note {
  margin-top:12px;
  font-size:9px;
  max-width:180px
}

.relationship-dimensions {
  margin-top:8px
}

.rel-bars {
  display:grid;
  gap:9px;
  margin:10px 0
}

.rel-bar-head {
  display:flex;
  justify-content:space-between;
  font-size:10px;
  color:#608074
}

.rel-bar-track,.mood-bar {
  height:3px;
  background:#e0ece8;
  border-radius:3px;
  margin-top:6px;
  overflow:hidden
}

.rel-bar-fill,.mood-bar-fill {
  height:100%;
  background:#499d83;
  border-radius:3px
}

.mood-gauges {
  display:grid;
  gap:12px
}

.mood-row {
  border-bottom:1px solid #e5eeeb;
  padding:0 0 12px
}

.mood-row-top {
  display:flex;
  align-items:baseline;
  justify-content:space-between;
  gap:10px;
  font-size:11px;
  flex-wrap:wrap
}

.mood-row-top strong {
  font-weight:500
}

.mood-row .when {
  display:block;
  margin-top:6px;
  font-size:9px
}

.appraisal-quote {
  padding:0 0 0 12px;
  border-left:2px solid #93c0b0;
  font-size:11px;
  line-height:1.9;
  color:#4c695e
}

.appraisal-quote .mood-row-top {
  font-size:9px;
  color:#7d978d;
  margin-bottom:5px
}

.appraisal-quote .detail {
  font-size:11px;
  line-height:1.9
}

.appraisal-quote .when {
  font-size:9px
}

.pending-overview {
  display:flex;
  gap:12px;
  align-items:baseline;
  flex-wrap:wrap
}

.pending-overview strong {
  font-size:13px;
  font-weight:500
}

.section-card[data-empty="true"] {
  padding:16px 22px
}

.section-card[data-empty="true"] .section-head {
  margin-bottom:8px
}

.section-card[data-empty="true"] .section-body {
  gap:5px
}

.section-card[data-empty="true"] .section-body>.section-note {
  display:none
}

.section-card[data-empty="true"] .pending-overview {
  display:flex
}

.life-columns {
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:20px;
  margin-top:12px
}

.life-column {
  min-width:0
}

/* Media keeps the image as the focus, with separate provenance. */
.gallery-grid {
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(min(100%,220px),1fr));
  gap:22px
}

.polaroid-card {
  display:block;
  text-align:left;
  min-width:0;
  border:1px solid #e4e5d2;
  border-radius:10px;
  padding:8px;
  background:#fffff6;
  box-shadow:0 3px 12px #69643a0a;
  transition:transform .2s,box-shadow .2s
}

.polaroid-card:hover {
  transform:translateY(-3px);
  box-shadow:0 8px 22px #69643a18
}

.polaroid-tape {
  display:none
}

.polaroid-frame {
  position:relative;
  min-width:0;
  overflow:hidden;
  border-radius:6px;
  background:#e4f0ec;
  aspect-ratio:4/5
}

.polaroid-frame img {
  display:block;
  width:100%;
  height:100%;
  object-fit:cover
}

.polaroid-badge {
  position:absolute;
  left:8px;
  bottom:8px;
  background:#233d33d9;
  color:#e9fbf4;
  border-radius:5px;
  padding:4px 7px;
  font-size:9px
}

.polaroid-caption {
  font-size:12px;
  line-height:1.8;
  padding:12px 4px 8px;
  display:-webkit-box;
  -webkit-box-orient:vertical;
  -webkit-line-clamp:3;
  overflow:hidden
}

.polaroid-meta {
  font-size:8px;
  color:#989b77;
  display:flex;
  justify-content:space-between;
  gap:6px;
  padding:5px 4px
}

.film-strip {
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));
  gap:14px;
  margin-top:12px
}

.film-frame {
  padding:14px;
  border:1px solid #dedfc3;
  border-radius:9px;
  font-size:12px;
  line-height:1.8
}

.film-frame-head {
  display:flex;
  justify-content:space-between;
  gap:10px;
  font-size:9px;
  color:#729c8d;
  margin-bottom:10px
}

.bili-card {
  padding:16px;
  background:#e8f7f2;
  border:1px solid #cfe7de;
  border-radius:10px
}

.chat-bubble-card {
  background:#e8f4f0;
  border-radius:4px 14px 14px;
  padding:14px 18px;
  border:1px solid #d6e8e1
}

.lightbox-backdrop {
  position:fixed;
  inset:0;
  z-index:100;
  background:#0a2119dc;
  backdrop-filter:blur(12px);
  display:flex;
  align-items:center;
  justify-content:center;
  padding:24px
}

.lightbox-modal {
  max-width:100%;
  max-height:95vh;
  display:flex;
  flex-direction:column;
  background:#f4fcf9;
  border-radius:14px;
  overflow:hidden
}

.lightbox-modal>img {
  max-width:100%;
  max-height:78vh;
  object-fit:contain;
  min-height:0
}

.lightbox-caption {
  display:flex;
  align-items:center;
  gap:20px;
  justify-content:space-between;
  padding:16px;
  font-size:12px;
  line-height:1.8
}

.lightbox-caption>div {
  min-width:0;
  overflow-wrap:anywhere
}

/* Operating cost: summary, SVG chart, category, function, exact record. */
#costMeterDetails>summary,#explorerDetails>summary {
  padding:16px 20px;
  background:var(--paper);
  border:1px solid var(--line);
  border-radius:12px;
  font-size:12px;
  color:#487464
}

.domain-details>summary {
  list-style:none
}

.domain-details>summary::-webkit-details-marker {
  display:none
}

#costMeterDetails>summary:after,#explorerDetails>summary:after {
  content:"+";
  float:right
}

#costMeterDetails[open]>summary:after,#explorerDetails[open]>summary:after {
  content:"−"
}

#costMeterPanel {
  margin-top:12px;
  padding:22px
}

.cost-meter-wrap {
  display:grid;
  gap:18px
}

.cost-kpi-grid {
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:24px
}

.cost-kpi-card {
  min-width:0;
  display:flex;
  flex-direction:column;
  gap:9px;
  padding:4px 18px 12px 0;
  border-right:1px solid var(--line)
}

.cost-kpi-card:last-child {
  border:0
}

.cost-kpi-label {
  font-size:11px;
  color:#749388
}

.cost-kpi-value {
  font:400 32px/1.2 system-ui;
  color:#366051;
  font-variant-numeric:tabular-nums;
  letter-spacing:-1px
}

.cost-kpi-sub {
  font-size:10px;
  color:#7b9b8f;
  line-height:1.7
}

.cost-chart-grid {
  display:grid;
  grid-template-columns:minmax(0,1.75fr) minmax(230px,1fr);
  gap:20px
}

.cost-panel-box {
  min-width:0;
  padding:20px;
  border:1px solid #d5e8e1;
  border-radius:12px;
  background:#f2faf7
}

.cost-trend-svg {
  display:block;
  width:100%;
  margin-top:20px;
  overflow:visible
}

.chart-axis {
  font:10px system-ui;
  fill:#799a8e
}

.cost-day-hit {
  fill:transparent
}

.cost-day[aria-pressed="true"] .cost-day-hit,.cost-day:hover .cost-day-hit {
  fill:#dfeee8
}

.cost-day {
  cursor:pointer
}

.cost-day:focus {
  outline:none
}

.cost-day:focus .cost-day-hit {
  stroke:#2a8e6d;
  stroke-width:1
}

.cost-day-detail {
  display:flex;
  flex-wrap:wrap;
  justify-content:space-between;
  gap:8px 14px;
  border-top:1px solid #d5e8e1;
  padding-top:14px;
  font-size:10px;
  color:#6e9889
}

.cost-day-detail strong {
  font-size:11px;
  color:#50796a;
  font-weight:500
}

.cost-distribution {
  display:flex;
  flex-direction:column
}

.cost-donut {
  display:block;
  width:170px;
  margin:0 auto;
  flex:none
}

.donut-label {
  font-size:10px;
  fill:#729a8b
}

.donut-total {
  font:500 23px system-ui;
  fill:#3c6858
}

.cost-legend {
  display:grid;
  gap:9px;
  margin-top:auto
}

.cost-legend-row {
  display:flex;
  align-items:center;
  gap:8px;
  font-size:10px;
  color:#689182
}

.cost-legend-row i {
  width:7px;
  height:7px;
  border-radius:50%;
  flex:none
}

.cost-legend-row strong {
  margin-left:auto;
  color:#487866;
  font-weight:500
}

.cost-split-grid {
  display:grid;
  grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);
  gap:20px
}

.cost-category {
  padding:4px 0;
  border-bottom:1px solid #d4e8e1
}

.cost-category:last-child {
  border:0
}

.cost-category>summary {
  display:grid;
  grid-template-columns:minmax(0,1fr) auto 12px;
  gap:12px;
  align-items:center;
  padding:12px 0;
  list-style:none;
  font-size:11px
}

.cost-category>summary::-webkit-details-marker {
  display:none
}

.cost-category>summary strong {
  font-weight:500;
  overflow-wrap:anywhere
}

.disclosure-plus {
  color:#70a592
}

.cost-category[open] .disclosure-plus {
  transform:rotate(45deg)
}

.cost-number {
  font:500 12px ui-monospace,monospace;
  color:#47866f
}

.cost-purpose-list {
  display:grid;
  gap:14px;
  border-left:1px solid #9ecebc;
  padding:4px 0 12px 15px;
  margin-left:3px
}

.cost-purpose-head {
  display:flex;
  gap:10px;
  justify-content:space-between;
  font-size:10px;
  line-height:1.7
}

.cost-purpose-head strong {
  font-weight:500
}

.cost-purpose-meter {
  display:block;
  width:100%;
  height:3px;
  margin:6px 0
}

.cost-purpose-meta {
  font-size:9px;
  line-height:1.8;
  color:#769d8f;
  margin:6px 0
}

.cost-model-mode {
  display:block;
  font-size:9px;
  color:#7ca495;
  margin-top:4px
}

.cost-audit {
  border-top:1px solid #d5e9e2;
  padding-top:14px
}

.cost-audit>summary {
  font-size:10px;
  color:#609380;
  line-height:1.8
}

.cost-recent-list {
  display:grid;
  gap:8px;
  margin-top:14px
}

.cost-recent-row {
  display:grid;
  grid-template-columns:100px minmax(0,1.3fr) minmax(0,1fr) 58px 65px;
  gap:10px;
  font-size:10px;
  padding:9px 0;
  border-bottom:1px solid #d8ece5;
  align-items:baseline
}

.cost-recent-row>* {
  min-width:0;
  overflow-wrap:anywhere
}

.cost-recent-time,.cost-recent-tok {
  color:#79a092
}

.cost-recent-cny {
  text-align:right;
  color:#44866e;
  font-weight:500
}

.operator-details {
  grid-column:1/-1;
  padding:16px 20px;
  border:1px solid var(--line);
  border-radius:12px;
  background:var(--paper);
  min-width:0
}

.operator-details>summary {
  font-size:12px;
  color:#4e8470
}

.operator-grid {
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:16px;
  margin-top:16px
}

.operator-grid>.section-card {
  grid-column:auto
}

.metric-grid {
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(min(100%,100px),1fr));
  gap:6px
}

.metric {
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:8px;
  font-size:10px;
  padding:9px;
  border-radius:6px;
  background:#e7f4ef
}

.metric strong {
  font-size:14px;
  font-weight:500
}

.zero-metrics>summary,.section-contract>summary {
  font-size:10px;
  color:#699786
}

.zero-metrics .metric-grid {
  margin-top:10px
}

.run-explorer,.evidence-explorer {
  padding:24px;
  margin-top:16px
}

.mechanism-map .flow {
  stroke-dasharray:4 7;
  animation:flow-drift 30s linear infinite
}

.map-label {
  white-space:normal
}

.evidence-records {
  max-height:500px
}

.record-values {
  grid-template-columns:repeat(auto-fit,minmax(min(100%,140px),1fr))
}

body.recording .operator-only {
  display:none!important
}

body.recording .dashboard-nav {
  display:none
}

body.recording .status-bar,body.recording .capture-notice,body.recording .recording-tools,body.recording .wrap {
  width:calc(100% - 48px);
  max-width:1440px;
  margin-left:auto;
  margin-right:auto
}

body.recording .page-intro {
  display:none
}

body.recording[data-focus]:not([data-focus="all"]) .section-card {
  grid-column:1/-1
}

@keyframes orbit-breathe {
  0%,100% {
    opacity:1
  }
  50% {
    opacity:.45
  }
}

@keyframes flow-drift {
  to {
    stroke-dashoffset:-110
  }
}

/* All sections live on one page. Details are opened deliberately. */
.status-bar,.capture-notice,.recording-tools,.wrap {width:calc(100% - 64px);max-width:1380px;margin-left:auto;margin-right:auto}
.wrap {padding-top:22px;gap:20px}
.page-intro h2 {font-size:28px}
.now-panel {padding:18px 26px}
.now-story {grid-template-columns:minmax(0,1fr) 116px;gap:24px;margin-top:0}
.present-main {padding:10px 0 0}
.present-title {font-size:18px;line-height:1.7}
.present-label {font-size:8px;margin-bottom:6px}
.day-orbit {width:116px}
.now-context {margin-top:10px;padding-top:9px;font-size:10px}
.now-context-detail {margin-top:8px}
.section-grid {grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.section-grid>.section-card,.visual-studio>.section-card {grid-column:auto}
.visual-studio {display:contents}
details.section-card {padding:0;align-self:start;display:block}
details.section-card>summary {list-style:none;display:flex;align-items:center;flex-wrap:wrap;gap:8px 12px;padding:16px 18px;margin:0;position:relative;padding-right:38px}
details.section-card>summary::-webkit-details-marker {display:none}
details.section-card>summary:after {content:'+';position:absolute;right:18px;top:18px;color:var(--green);font-size:16px}
details.section-card[open]>summary:after {content:'−'}
details.section-card>summary h2 {font-size:12px}
.card-preview {display:block;flex-basis:100%;font-size:11px;line-height:1.65;color:var(--muted);overflow-wrap:anywhere}
details.section-card[open] {grid-column:1/-1}
details.section-card>.section-body {padding:4px 20px 20px}
.section-grid .section-card[data-empty="true"] {padding:0}
.section-grid .section-card[data-empty="true"] .section-head {margin:0}
.radar-wrap {grid-template-columns:180px minmax(0,1fr);max-width:700px}
.operator-details {grid-column:1/-1}
.agenda-controls {display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.agenda-navigation {display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-right:auto}
.agenda-range {font:500 12px system-ui;letter-spacing:.02em;margin:0 10px;color:var(--ink-soft)}
.agenda-filter,.story-route-select {min-width:0;max-width:100%;padding:8px 12px;font-size:11px;border:1px solid var(--line-strong);border-radius:8px;background:var(--paper-tint)}
.week-help,.week-footer {display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center;color:var(--muted);font-size:10px;line-height:1.6}
.week-scroll {width:100%;max-height:438px;overflow:auto;border:1px solid var(--line);border-radius:12px;scrollbar-gutter:stable;overscroll-behavior:contain}
.week-table {min-width:840px;position:relative;background:#fafdfc}
.week-row {display:grid;grid-template-columns:62px repeat(7,minmax(0,1fr))}
.week-head {position:sticky;top:0;z-index:4;background:#eaf4f0;border-bottom:1px solid #c9ded6}
.week-day-heading {display:flex;gap:8px;align-items:center;justify-content:center;padding:14px 6px;color:#69867b;font-size:10px;border-left:1px solid #dbe9e4}
.week-day-heading strong {font:500 13px system-ui;color:#346b52}
.week-day-heading[data-today="true"] {background:#1c6653;color:#a5d8c5}
.week-day-heading[data-today="true"] strong {color:#effff9}
.week-corner,.week-hour {padding:12px 6px;text-align:center;font:10px ui-monospace,monospace;color:#8aa199;position:sticky;left:0;background:#edf5f2;z-index:2}
.week-corner {font-family:inherit;display:grid;place-items:center}
.week-cell {min-width:0;min-height:86px;padding:5px;border-left:1px solid #e0ebe7;border-bottom:1px solid #e0ebe7}
.week-hour {border-bottom:1px solid #dbe8e3}
.week-empty {display:block;text-align:center;font-size:11px;color:#ccd9d4;padding-top:22px}
.week-event {display:flex;flex-direction:column;width:100%;height:100%;min-height:74px;text-align:left;gap:4px;padding:7px 8px;background:#e8f3ef;border:1px solid #d2e4dd;border-left:3px solid #5da485;border-radius:7px;transition:border-color .15s,background .15s}
.week-event:hover {background:#dbefe8;border-color:#72ab96}
.week-event[data-kind="planning"] {background:#ecf1f8;border-color:#d5dfed;border-left-color:#81a0c1}
.week-event[data-kind="plan"] {background:#f2f2e5;border-color:#e1e1c5;border-left-color:#b5ac64}
.week-event[data-kind="unloaded"] {background:#f4f8f7;border:1px dashed #d7e4df;color:#8ba29a}
.week-kind {font-size:8px;color:#709286}
.week-title {font-size:10px;line-height:1.5;font-weight:500;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
.week-count {font-size:8px;color:#869d95;margin-top:auto}
.week-footer {justify-content:flex-start}.week-footer>.section-note {margin-left:auto}
.week-cell[data-state="unknown"] .week-event {border:0;background:none;color:#9aada1;min-height:74px;justify-content:center;align-items:center}
.week-cell[data-state="unknown"] .week-count {display:none}
/* Branch map: actual prerequisites have arrows; membership has none. */
.story-toolbar {display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.story-route-select {display:block;flex:1;max-width:460px}
.story-zoom {display:flex;gap:5px;margin-left:auto}
.story-map-viewport {height:378px;overflow:auto;border:1px solid #214e43;border-radius:12px;background:radial-gradient(#8ebdab22 .8px,transparent .8px),#102e29;background-size:18px 18px;overscroll-behavior:contain;scrollbar-color:#44725e #16362b}
.story-map-extent {position:relative}
.story-map-canvas {position:relative;transform-origin:0 0}
.story-connections {position:absolute;inset:0;max-width:none;pointer-events:none}
.story-edge {fill:none;stroke:#78af9b;stroke-width:1.5}
.story-edge.membership {stroke:#6a9786;stroke-dasharray:3 5;stroke-opacity:.58}
.story-edge.prerequisite {stroke:#83e1b4;stroke-width:2;filter:drop-shadow(0 0 3px #6cd9a744)}
.story-node {position:absolute;text-align:left;display:flex;flex-direction:column;gap:8px;padding:13px 16px;border:1px solid #578977;border-radius:5px;background:#173f2e;color:#d8f2e8;box-shadow:0 4px 14px #0002;transition:border-color .18s,background .18s,box-shadow .18s}
.story-node-label {font-size:8px;color:#89baa8;letter-spacing:.09em}
.story-node-title {font-size:12px;font-weight:500;line-height:1.65;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden}
.story-node[data-tone="root"] {border-color:#81c9af;background:#295b49}
.story-node[data-tone="question"] {border-color:#c8aa6d;background:#49432c;color:#f4e8bf;clip-path:polygon(8% 0,92% 0,100% 16%,100% 84%,92% 100%,8% 100%,0 84%,0 16%);padding-left:20px}
.story-node[data-tone="question"] .story-node-label {color:#d9c28c}
.story-node[data-tone="realized"] {background:#c5eadc;border-color:#c5eadc;color:#1e4e3c}
.story-node[data-tone="realized"] .story-node-label {color:#4d8570}
.story-node[data-tone="candidate"] {border-style:dashed;background:#16352a}
.story-node[data-tone="ended"] {background:#243c33;color:#8faea3;border-color:#426458}
.story-node:hover,.story-node[aria-pressed="true"] {border-color:#bbffe6;box-shadow:0 0 0 2px #96e3c722,0 0 22px #7df1c612}
.story-map-legend {display:flex;align-items:center;gap:16px;flex-wrap:wrap;font-size:10px;color:var(--muted)}
.story-map-legend .legend-realized:before,.story-map-legend .legend-candidate:before,.story-map-legend .legend-ended:before {content:'';display:inline-block;width:8px;height:8px;margin-right:6px;background:#73ab96;border-radius:2px}
.story-map-legend .legend-candidate:before {background:transparent;border:1px dashed #73ab96}.story-map-legend .legend-ended:before {background:#b8c9c3}
.agenda-reading>.status-pill {justify-self:start}
/* Native modal supplies focus confinement, Escape and background inertness. */
.detail-drawer {margin:0 0 0 auto;width:min(540px,100%);max-width:100%;height:100dvh;max-height:100dvh;border:0;border-left:1px solid #c9ddd6;padding:0;background:#f7fbfa;color:var(--ink);box-shadow:-12px 0 60px #092c2926}
.detail-drawer::backdrop {background:#102f294f;backdrop-filter:blur(4px)}
.drawer-head {position:sticky;top:0;z-index:2;display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:26px;background:#f6fbf9f5;border-bottom:1px solid var(--line)}
.drawer-head>div {min-width:0}.drawer-head h2 {font:500 20px/1.6 'Songti SC',serif;margin:10px 0 0;overflow-wrap:anywhere}.drawer-head .button {flex:none}
.drawer-body {padding:24px 26px;display:grid;gap:16px}.drawer-body>div {display:grid;gap:14px}
.drawer-record {width:100%;min-width:0;text-align:left;padding:15px;background:#eef6f3;border:1px solid #d7e6e1;border-radius:10px;display:grid;gap:8px}
.drawer-record strong {font-size:13px;line-height:1.8;font-weight:500}.drawer-record .status-pill {width:fit-content}
.drawer-page-controls {display:flex;gap:8px;flex-wrap:wrap}.drawer-provenance {border-top:1px solid var(--line);padding-top:18px;font-size:10px;color:var(--muted)}
.agenda-detail-time {font-size:11px;line-height:1.8;color:var(--muted)}.agenda-detail-body,.agenda-reading .detail,.story-node-reading .detail {font-size:13px;line-height:2;white-space:pre-wrap;margin:0}
.agenda-related {display:block;padding:12px;text-align:left;line-height:1.8;background:#e8f3ef;border:1px solid #cfe0da;border-radius:9px;color:#598172;font-size:12px}
.chapter-entry {padding:0 0 18px;border-bottom:1px solid var(--line)}.chapter-entry h3 {font-size:16px;line-height:1.7;margin:8px 0}.chapter-entry .detail {font-size:12px;line-height:1.9;margin-top:12px}
.map-layout {grid-template-columns:minmax(0,1fr)}.map-layout>div {min-width:0}
.cost-date-select {display:none}.cost-purpose-list {max-height:420px;overflow:auto;scrollbar-gutter:stable;padding-right:8px}.cost-inactive>summary {font-size:10px;color:var(--muted);padding:8px 0}.cost-inactive>.cost-purpose-row {margin-top:12px}
@media(max-width:1000px) {
  .section-grid {grid-template-columns:repeat(2,minmax(0,1fr))}
  .cost-chart-grid,.cost-split-grid {grid-template-columns:minmax(0,1fr)}
  .cost-distribution {display:grid;grid-template-columns:170px minmax(0,1fr);gap:10px 24px;align-items:center}.cost-distribution h3 {grid-column:1/-1}.cost-legend {margin:0}
  .mechanism-detail {grid-template-columns:1fr}.mechanism-detail>* {grid-column:1!important}
}
@media(max-width:600px) {
  .bar {height:60px;padding:0 16px}.bar h1 {font-size:12px}.monogram {width:32px;height:32px}.eyebrow {font-size:7px}.bar-actions {gap:10px}.identity {gap:8px}.button.primary {font-size:10px;padding:6px 9px}.logout button {font-size:10px}
  .status-bar,.capture-notice,.recording-tools,.wrap {width:calc(100% - 28px);margin-left:14px;margin-right:14px}.status-bar {padding-top:16px}.readonly,.page-index {display:none}.status-meta {gap:9px}.header-clock {font-size:10px}.view-tools {gap:8px}
  .wrap {padding-top:18px;gap:16px}.page-intro h2 {font-size:24px}.page-intro p {font-size:11px}.now-panel {padding:18px}.now-story {display:block}.day-orbit,.hero-note {display:none}.world-clock {width:100%;margin:0;font-size:9px}.present-title {font-size:16px}.present-main {padding-top:10px}.now-context {gap:10px}
  .section-card {padding:16px}.section-head {margin-bottom:12px}.section-head h2 {font-size:13px}.section-grid {gap:10px}.card-preview {font-size:10px}details.section-card>summary {padding:14px 30px 14px 14px}details.section-card>summary:after {right:12px;top:13px}
  .agenda-range {width:100%;margin:8px 0}.agenda-filter {font-size:10px;padding:7px 9px}.week-help {font-size:9px}.week-scroll {max-height:390px}.week-footer .button {font-size:10px}.week-footer>.section-note {margin:4px 0 0}.story-route-select {flex-basis:100%;max-width:100%}.story-toolbar>.button {max-width:100%;white-space:normal;text-align:left;font-size:10px}.story-zoom {margin-left:0}.story-zoom .button {font-size:10px;padding:6px 10px}.story-map-viewport {height:340px}.story-map-legend {font-size:9px;gap:10px}
  .radar-wrap {grid-template-columns:140px minmax(0,1fr);gap:12px}.stage-hero strong {font-size:25px}.life-columns,.operator-grid {grid-template-columns:minmax(0,1fr)}.gallery-grid {grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.polaroid-caption {font-size:10px}.polaroid-meta {display:block}
  .cost-kpi-grid {grid-template-columns:1fr;gap:12px}.cost-kpi-card {border:0;border-bottom:1px solid var(--line);padding:0 0 12px;display:grid;grid-template-columns:1fr auto;gap:7px;align-items:center}.cost-kpi-value {font-size:26px;text-align:right}.cost-kpi-sub {grid-column:1/-1;font-size:9px}.cost-panel-box {padding:14px}.cost-date-select {display:block;width:100%;padding:8px 10px;font-size:11px;background:var(--paper);border:1px solid var(--line);border-radius:7px;margin-top:12px}.cost-trend-svg .chart-axis {font-size:20px}.cost-distribution {grid-template-columns:110px minmax(0,1fr);gap:10px}.cost-donut {width:110px}.cost-legend-row {font-size:9px;gap:5px}.cost-recent-row {grid-template-columns:76px minmax(0,1fr) 58px;gap:6px;font-size:9px}.cost-recent-model,.cost-recent-tok {display:none}.cost-day-detail {font-size:9px}
  .run-explorer,.evidence-explorer {padding:16px}.explorer-heading {display:block}.explorer-heading h2 {font-size:17px}.map-label {display:inline-block;margin-top:8px;white-space:normal}.evidence-filters {grid-template-columns:1fr}.evidence-record summary {grid-template-columns:minmax(0,1fr) 22px;gap:6px}.evidence-record summary .when {display:none}
  .drawer-head {padding:20px}.drawer-head h2 {font-size:18px}.drawer-body {padding:20px}.lightbox-backdrop {padding:12px}.lightbox-caption {gap:12px;padding:12px;font-size:11px}
}
@media(prefers-reduced-motion:reduce) {*,*:before,*:after {animation:none!important;transition:none!important;scroll-behavior:auto!important}}

"""
