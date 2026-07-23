"""Faithful reproduction of reconciliation_mockup.html inside Streamlit.

Two parts:
  1. CSS — the mockup's own styles verbatim, plus overrides that hide Streamlit
     chrome and restyle its widgets (sidebar, buttons, uploaders, inputs, tabs)
     so the app matches the mockup pixel-for-pixel.
  2. HTML builders — sidebar stepper, top bar, KPI tiles, filter chips, and the
     signature built-vs-billed rows (native <details> for click-to-expand, since
     Streamlit strips <script>).

Navigation uses query-param links (<a target="_self" href="?step=..">) so the
stepper is the mockup's real markup rather than Streamlit buttons.
"""
from __future__ import annotations

import html

from recon.models import ReconRow, Severity

# --------------------------------------------------------------------------- #
#  CSS
# --------------------------------------------------------------------------- #
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');

:root{
  --ink:#101826; --ink-2:#18223a; --ink-line:#26324f;
  --canvas:#eef1f6; --surface:#ffffff; --line:#d7dee8; --line-soft:#e6eaf1;
  --blue:#1c5ac4; --blue-soft:#e7effb;
  --text:#16202e; --muted:#5c6b80; --muted-2:#8592a6;
  --critical:#c6362f; --critical-soft:#fbe9e7;
  --warn:#c9781a; --warn-soft:#fbf0df;
  --ok:#2e7d57; --ok-soft:#e6f2ec;
  --mono:'IBM Plex Mono',ui-monospace,monospace;
  --sans:'IBM Plex Sans',system-ui,sans-serif;
  --r:10px;
}

/* ---------- hide Streamlit chrome ---------- */
header[data-testid="stHeader"]{display:none !important;}
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"]{display:none !important;}
#MainMenu, [data-testid="stMainMenu"]{display:none !important;}
footer{display:none !important;}
[data-testid="stSidebarCollapseButton"], [data-testid="stSidebarCollapsedControl"]{display:none !important;}
[data-testid="stSidebarHeader"]{display:none !important;}
[data-testid="stAppDeployButton"]{display:none !important;}

/* ---------- app canvas + base type ---------- */
html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"], [data-testid="stMain"]{
  background:var(--canvas) !important;
  font-family:var(--sans);
  color:var(--text);
}
[data-testid="stApp"]{font-size:14px;line-height:1.45;-webkit-font-smoothing:antialiased;}
.num,.mono{font-family:var(--mono);font-variant-numeric:tabular-nums;}

/* main content: centered column, comfortable width. The top bar breaks out of
   this padding with negative margins to sit flush against the container edges. */
[data-testid="stMainBlockContainer"]{padding:0 28px 56px !important;max-width:1160px !important;margin:0 auto !important;}
[data-testid="stMain"] [data-testid="stVerticalBlock"]{gap:14px;}
.lede{color:var(--muted);font-size:13.5px;max-width:70ch;margin:2px 0 6px;}

/* ---------- sidebar ---------- */
[data-testid="stSidebar"]{
  background:var(--ink) !important;
  width:264px !important;min-width:264px !important;
  border-right:1px solid var(--ink-line);
}
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"]{padding:0 !important;}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:0 !important;}
[data-testid="stSidebar"] .stMarkdown{width:100%;}

.side__brand{padding:20px 20px 16px;border-bottom:1px solid var(--ink-line);}
.side__logo{display:flex;align-items:center;gap:10px;}
.side__mark{width:26px;height:26px;border-radius:6px;background:var(--blue);display:grid;place-items:center;flex:none;}
.side__mark span{width:11px;height:11px;border:2.5px solid #fff;border-radius:50%;display:block;}
.side__name{color:#fff;font-weight:600;font-size:14px;letter-spacing:.2px;}
.side__sub{font-size:11px;color:var(--muted-2);margin-top:2px;letter-spacing:.3px;text-transform:uppercase;}
.side__ribbon{display:flex;height:3px;margin-top:14px;border-radius:2px;overflow:hidden;}
.side__ribbon i{flex:1;}

.steps{list-style:none;margin:0;padding:14px 12px;display:flex;flex-direction:column;}
.step{display:flex;gap:12px;align-items:flex-start;width:100%;text-align:left;padding:11px 12px;border-radius:9px;margin-bottom:2px;transition:background .15s;text-decoration:none;}
.step:hover{background:var(--ink-2);}
.step.is-active{background:var(--ink-2);}
.step.is-active .step__t{color:#fff;}
.step__dot{width:24px;height:24px;border-radius:50%;flex:none;display:grid;place-items:center;font-family:var(--mono);font-size:12px;font-weight:600;border:1.5px solid var(--ink-line);color:var(--muted-2);}
.step.is-done .step__dot{background:var(--ok);border-color:var(--ok);color:#fff;}
.step.is-active .step__dot{background:var(--blue);border-color:var(--blue);color:#fff;}
.step__t{font-size:13.5px;font-weight:500;color:#c3ccdb;display:block;}
.step__d{font-size:11.5px;color:var(--muted-2);margin-top:1px;display:block;}

.side__foot{padding:14px 16px;border-top:1px solid var(--ink-line);margin-top:6px;}
.proj{font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:var(--muted-2);}
.proj__name{color:#fff;font-weight:500;margin-top:3px;font-size:13px;}
.proj__meta{color:var(--muted-2);font-size:11.5px;margin-top:2px;}

/* project-settings expander, kept dark to match the sidebar */
[data-testid="stSidebar"] [data-testid="stExpander"]{border:none;background:transparent;}
[data-testid="stSidebar"] [data-testid="stExpander"] details{background:var(--ink-2);border:1px solid var(--ink-line);border-radius:9px;margin:0 14px 16px;}
[data-testid="stSidebar"] [data-testid="stExpander"] summary{color:#c3ccdb;font-size:12px;}
[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover{color:#fff;}
[data-testid="stSidebar"] label, [data-testid="stSidebar"] .stTextInput label, [data-testid="stSidebar"] .stNumberInput label{color:var(--muted-2) !important;font-size:11px !important;}
[data-testid="stSidebar"] input{background:var(--ink) !important;color:#fff !important;border-color:var(--ink-line) !important;}

/* ---------- top bar (breaks out of block-container padding) ---------- */
.top{background:var(--surface);border-bottom:1px solid var(--line);padding:16px 28px;margin:0 -28px 22px;display:flex;justify-content:space-between;align-items:center;gap:16px;position:sticky;top:0;z-index:20;}
.top__crumb{font-size:12px;color:var(--muted);letter-spacing:.2px;}
.top__title{font-size:19px;font-weight:600;margin-top:2px;color:var(--text);}
.top__actions{display:flex;gap:10px;flex:none;}

/* ---------- buttons (mockup .btn) ---------- */
.btn{border:1px solid var(--line);background:var(--surface);color:var(--text);padding:8px 15px;border-radius:8px;font-size:13px;font-weight:500;text-decoration:none;display:inline-flex;align-items:center;gap:6px;transition:border-color .15s,background .15s;cursor:pointer;}
.btn:hover{border-color:var(--muted-2);}
.btn--pri{background:var(--blue);border-color:var(--blue);color:#fff;}
.btn--pri:hover{background:#164a9f;}
.btn--sm{padding:5px 11px;font-size:12px;}

/* restyle real Streamlit buttons to match .btn / .btn--pri */
.stButton>button, .stDownloadButton>button{
  border:1px solid var(--line) !important;background:var(--surface) !important;color:var(--text) !important;
  border-radius:8px !important;font-size:13px !important;font-weight:500 !important;padding:8px 15px !important;
  font-family:var(--sans) !important;box-shadow:none !important;transition:border-color .15s,background .15s;
}
.stButton>button:hover, .stDownloadButton>button:hover{border-color:var(--muted-2) !important;color:var(--text) !important;}
.stButton>button[kind="primary"], .stDownloadButton>button[kind="primary"],
.stButton>button[data-testid="stBaseButton-primary"], .stDownloadButton>button[data-testid="stBaseButton-primary"]{
  background:var(--blue) !important;border-color:var(--blue) !important;color:#fff !important;
}
.stButton>button[kind="primary"]:hover, .stDownloadButton>button[kind="primary"]:hover,
.stButton>button[data-testid="stBaseButton-primary"]:hover{background:#164a9f !important;border-color:#164a9f !important;}

/* Streamlit styles markdown links (.stMarkdown a) with its own blue link color,
   which would win over our component classes — override with equal-or-higher
   specificity so our buttons/chips/tabs/stepper keep their intended colors. */
[data-testid="stMarkdown"] a.btn,
[data-testid="stMarkdown"] a.step,
[data-testid="stMarkdown"] a.fchip,
[data-testid="stMarkdown"] a.tab{text-decoration:none !important;}
[data-testid="stMarkdown"] a.btn{color:var(--text) !important;}
[data-testid="stMarkdown"] a.btn:hover{color:var(--text) !important;border-color:var(--muted-2) !important;}
[data-testid="stMarkdown"] a.btn--pri,
[data-testid="stMarkdown"] a.btn--pri:hover{color:#fff !important;}
[data-testid="stMarkdown"] a.fchip{color:var(--muted) !important;}
[data-testid="stMarkdown"] a.fchip.is-on{color:#fff !important;}
[data-testid="stMarkdown"] a.tab{color:var(--muted) !important;}
[data-testid="stMarkdown"] a.tab.on{color:var(--text) !important;}
/* stepper: the anchor must not tint/underline the title & description spans */
[data-testid="stMarkdown"] a.step:hover{text-decoration:none !important;}

/* ---------- KPI row ---------- */
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:22px;}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:14px 15px;}
.kpi__l{font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:var(--muted);font-weight:500;}
.kpi__v{font-family:var(--mono);font-size:22px;font-weight:600;margin-top:8px;letter-spacing:-.5px;}
.kpi__s{font-size:11.5px;color:var(--muted);margin-top:3px;}
.kpi--flag{background:linear-gradient(180deg,#fdf3f1,#fff);border-color:#f2ccc7;}
.kpi--flag .kpi__v{color:var(--critical);}

/* ---------- filter chips ---------- */
.bar-row{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap;}
.filters{display:flex;gap:7px;flex-wrap:wrap;}
.fchip{border:1px solid var(--line);background:var(--surface);border-radius:20px;padding:5px 13px;font-size:12.5px;color:var(--muted);font-weight:500;display:flex;align-items:center;gap:7px;text-decoration:none;}
.fchip.is-on{background:var(--ink);color:#fff;border-color:var(--ink);}
.fchip b{font-family:var(--mono);font-weight:600;}
.legend{font-size:12px;color:var(--muted);display:flex;align-items:center;gap:6px;}
.legend i{width:22px;height:8px;border-radius:2px;display:inline-block;}

/* ---------- reconciliation rows (native <details>) ---------- */
.recon{display:flex;flex-direction:column;gap:10px;}
.rrow{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);overflow:hidden;}
.rrow>summary{display:grid;grid-template-columns:1.6fr 2fr 1fr;gap:20px;align-items:center;padding:15px 18px;cursor:pointer;list-style:none;}
.rrow>summary::-webkit-details-marker{display:none;}
.rrow>summary::marker{content:"";}
.rrow.flag-critical{border-left:4px solid var(--critical);}
.rrow.flag-warning{border-left:4px solid var(--warn);}
.rrow.flag-info{border-left:4px solid var(--muted-2);}
.rrow.flag-ok{border-left:4px solid var(--ok);}
.rid{display:flex;align-items:baseline;gap:9px;}
.rid__code{font-family:var(--mono);font-size:12px;color:var(--muted);background:var(--canvas);padding:2px 7px;border-radius:5px;}
.rid__desc{font-weight:600;font-size:14px;}
.rid__uom{font-size:11px;color:var(--muted-2);margin-top:3px;text-transform:uppercase;letter-spacing:.4px;}

.bars{display:flex;flex-direction:column;gap:7px;min-width:0;}
.bline{display:grid;grid-template-columns:42px 1fr auto;gap:10px;align-items:center;}
.bline__k{font-size:10.5px;text-transform:uppercase;letter-spacing:.4px;color:var(--muted-2);}
.btrack{height:9px;background:var(--line-soft);border-radius:5px;overflow:hidden;}
.bfill{height:100%;border-radius:5px;}
.bfill--built{background:#9fb2cc;}
.bfill--billed{background:var(--blue);}
.flag-critical .bfill--billed{background:var(--critical);}
.flag-warning .bfill--billed{background:var(--warn);}
.flag-ok .bfill--billed{background:var(--ok);}
.bline__v{font-family:var(--mono);font-size:12.5px;color:var(--text);white-space:nowrap;}
.delta{font-family:var(--mono);font-size:11px;padding:0 0 0 6px;}
.delta--up{color:var(--critical);} .delta--dn{color:var(--muted);}

.rvar{text-align:right;}
.rvar__l{font-size:10.5px;text-transform:uppercase;letter-spacing:.4px;color:var(--muted-2);}
.rvar__v{font-family:var(--mono);font-size:18px;font-weight:600;margin-top:2px;}
.rvar__v.up{color:var(--critical);} .rvar__v.ok{color:var(--ok);} .rvar__v.dn{color:var(--muted);}
.chip{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:600;padding:3px 9px;border-radius:20px;margin-top:6px;}
.chip::before{content:"";width:6px;height:6px;border-radius:50%;}
.chip--critical{background:var(--critical-soft);color:var(--critical);} .chip--critical::before{background:var(--critical);}
.chip--warning{background:var(--warn-soft);color:var(--warn);} .chip--warning::before{background:var(--warn);}
.chip--ok{background:var(--ok-soft);color:var(--ok);} .chip--ok::before{background:var(--ok);}
.chip--info{background:var(--blue-soft);color:var(--blue);} .chip--info::before{background:var(--blue);}
.co-tag{font-size:9.5px;font-weight:600;letter-spacing:.4px;text-transform:uppercase;background:var(--blue-soft);color:var(--blue);padding:2px 6px;border-radius:5px;margin-left:8px;vertical-align:middle;}

.rrow__detail{border-top:1px dashed var(--line);padding:14px 18px;background:#fafbfd;font-size:12.5px;color:var(--muted);}
.rrow__detail b{color:var(--text);font-weight:600;}
.feed{display:flex;gap:10px;align-items:flex-start;padding:5px 0;}
.feed span{font-family:var(--mono);font-size:11.5px;color:var(--muted-2);flex:none;width:120px;}
.hint{font-size:12px;color:var(--muted-2);margin-top:14px;padding-left:2px;}

/* ---------- cards / tables ---------- */
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:20px 22px;margin-bottom:16px;}
.card__h{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap;}
.card__t{font-size:15px;font-weight:600;}
.card__note{font-size:12px;color:var(--muted);}
table.tbl{width:100%;border-collapse:collapse;font-size:13px;}
.tbl th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:var(--muted);font-weight:600;padding:8px 10px;border-bottom:1px solid var(--line);}
.tbl td{padding:9px 10px;border-bottom:1px solid var(--line-soft);}
.tbl tr:last-child td{border-bottom:0;}
.tbl .r{text-align:right;}
.tbl .code{font-family:var(--mono);font-size:12px;color:var(--muted);}
.tbl .num{font-family:var(--mono);font-variant-numeric:tabular-nums;}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;}

.badge{font-size:10.5px;font-weight:600;padding:2px 8px;border-radius:6px;letter-spacing:.2px;}
.badge--ok{background:var(--ok-soft);color:var(--ok);}
.badge--low{background:var(--warn-soft);color:var(--warn);}

/* export tabs (HTML links) */
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);}
.tabs a.tab{border:0;background:none;padding:10px 15px;font-size:13px;font-weight:500;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-1px;text-decoration:none;}
.tabs a.tab.on{color:var(--text);border-bottom-color:var(--blue);}
.prev{border:1px solid var(--line);border-top:0;border-radius:0 0 var(--r) var(--r);padding:16px;}
.card--flush{padding:0;overflow:hidden;}

/* dropzone — restyle the file uploader */
[data-testid="stFileUploaderDropzone"]{
  border:1.5px dashed #b9c5d8 !important;border-radius:var(--r) !important;background:#f7f9fc !important;
  padding:26px !important;color:var(--muted) !important;
}
[data-testid="stFileUploaderDropzone"] button{background:var(--surface) !important;}
[data-testid="stFileUploader"] small{color:var(--muted-2) !important;}

/* file rows */
.file{display:flex;align-items:center;gap:12px;padding:11px 14px;border:1px solid var(--line);border-radius:9px;margin-bottom:8px;background:var(--surface);}
.file__i{width:30px;height:30px;border-radius:7px;background:var(--canvas);display:grid;place-items:center;font-size:12px;font-family:var(--mono);color:var(--muted);flex:none;}
.file__n{font-weight:500;font-size:13px;}
.file__m{font-size:11.5px;color:var(--muted);}

/* crosswalk cards */
.xw{border:1px solid var(--line);border-radius:var(--r);padding:15px 17px;margin-bottom:10px;display:grid;grid-template-columns:1fr auto;gap:16px;align-items:center;background:var(--surface);}
.xw.is-resolved{background:var(--ok-soft);border-color:#c3e2d3;}
.xw__from{font-size:11px;color:var(--muted-2);margin-bottom:5px;text-transform:uppercase;letter-spacing:.3px;}
.xw__raw{font-family:var(--mono);font-size:13px;font-weight:500;}
.xw__sug{display:flex;align-items:center;gap:8px;margin-top:7px;font-size:12.5px;color:var(--muted);flex-wrap:wrap;}
.xw__sug b{color:var(--text);font-weight:600;}
.score{font-family:var(--mono);font-size:11px;padding:2px 7px;border-radius:5px;background:var(--blue-soft);color:var(--blue);}
.xw__act{display:flex;gap:8px;flex:none;}
.xw__done{color:var(--ok);font-weight:600;font-size:13px;display:flex;align-items:center;gap:6px;}

/* inputs / selects / toggles */
.stTextInput input, .stNumberInput input{border-radius:8px !important;border-color:var(--line) !important;font-family:var(--mono) !important;font-size:13px !important;}
[data-baseweb="select"]>div{border-radius:8px !important;border-color:var(--line) !important;font-size:13px !important;}
[data-testid="stTextInput"] label, [data-testid="stNumberInput"] label, [data-testid="stSelectbox"] label, .stRadio label{font-size:12px !important;font-weight:600 !important;color:var(--muted) !important;}

/* segmented control -> mockup .toggle (dark selected, not Streamlit red) */
[data-testid="stButtonGroup"] button{border-radius:8px !important;font-size:12.5px !important;
  color:var(--muted) !important;background:var(--surface) !important;border-color:var(--line) !important;}
[data-testid="stButtonGroup"] button[aria-checked="true"],
[data-testid="stButtonGroup"] button[aria-selected="true"],
[data-testid="stButtonGroup"] button[kind="segmented_controlActive"]{
  background:var(--ink) !important;color:#fff !important;border-color:var(--ink) !important;}

/* export tabs -> mockup .tabs */
.stTabs [data-baseweb="tab-list"]{gap:4px;border-bottom:1px solid var(--line);}
.stTabs [data-baseweb="tab"]{font-size:13px;font-weight:500;color:var(--muted);padding:10px 15px;}
.stTabs [aria-selected="true"]{color:var(--text) !important;border-bottom-color:var(--blue) !important;}

/* generic expander (main area) */
[data-testid="stMain"] [data-testid="stExpander"] details{border:1px solid var(--line);border-radius:9px;background:var(--surface);}

/* bordered st.container -> mockup .card. Streamlit 1.59 doesn't emit a key class
   on containers, so target the bordered vertical block via the segmented control's
   key (this uniquely identifies the billing-settings card). */
[data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"]:has(.st-key-billing_mode_ctl){
  background:var(--surface) !important;border:1px solid var(--line) !important;
  border-radius:var(--r) !important;padding:18px 20px !important;
}

.stAlert{border-radius:9px;}
@media (prefers-reduced-motion:reduce){*{animation:none !important;transition:none !important;}}
</style>
"""

# --------------------------------------------------------------------------- #
#  small formatting helpers
# --------------------------------------------------------------------------- #
_SEV_CLASS = {
    Severity.CRITICAL: "critical", Severity.WARNING: "warning",
    Severity.INFO: "info", Severity.OK: "ok",
}
_FIBER = ["#1f6fd6", "#e8621e", "#2e9e5b", "#7a4a2b", "#8a94a3", "#f2f4f7",
          "#d23b34", "#1a1f28", "#f2c033", "#7c5cd0", "#e58aa8", "#35bcc4"]


def _esc(s) -> str:
    return html.escape(str(s))


def _qty(v: float) -> str:
    return f"{v:,.6g}"


def _money_signed(v: float) -> str:
    sign = "+" if v > 0 else ("-" if v < 0 else "")
    return f"{sign}${abs(v):,.0f}"


# --------------------------------------------------------------------------- #
#  sidebar
# --------------------------------------------------------------------------- #
def sidebar_html(steps_state: list[dict], project_name: str, meta: str,
                 sid: str = "") -> str:
    """steps_state: list of {key,title,desc,status} where status in
    {'done','active','pending'}."""
    q = f"&sid={sid}" if sid else ""
    ribbon = "".join(f'<i style="background:{c}"></i>' for c in _FIBER)
    step_items = []
    for s in steps_state:
        cls = "step"
        if s["status"] == "done":
            cls += " is-done"; dot = "✓"
        elif s["status"] == "active":
            cls += " is-active"; dot = s["num"]
        else:
            dot = s["num"]
        step_items.append(
            f'<a class="{cls}" href="?step={s["key"]}{q}" target="_self">'
            f'<span class="step__dot">{dot}</span>'
            f'<span><span class="step__t">{_esc(s["title"])}</span>'
            f'<span class="step__d">{_esc(s["desc"])}</span></span></a>'
        )
    return f"""
    <div class="side__brand">
      <div class="side__logo">
        <div class="side__mark"><span></span></div>
        <div>
          <div class="side__name">Project Recon</div>
          <div class="side__sub">Reconciliation Tool</div>
        </div>
      </div>
      <div class="side__ribbon" title="Fiber color code">{ribbon}</div>
    </div>
    <nav class="steps">{''.join(step_items)}</nav>
    <div class="side__foot">
      <div class="proj">Active project</div>
      <div class="proj__name">{_esc(project_name or 'New project')}</div>
      <div class="proj__meta">{_esc(meta)}</div>
    </div>
    """


# --------------------------------------------------------------------------- #
#  top bar
# --------------------------------------------------------------------------- #
def topbar_html(project_name: str, title: str, actions: list[dict],
                sid: str = "") -> str:
    """actions: list of {label, href, primary}."""
    q = f"&sid={sid}" if sid else ""
    btns = "".join(
        f'<a class="btn{" btn--pri" if a.get("primary") else ""}" '
        f'href="?step={a["href"]}{q}" target="_self">{_esc(a["label"])}</a>'
        for a in actions
    )
    return f"""
    <div class="top">
      <div>
        <div class="top__crumb">{_esc(project_name or 'New project')} &nbsp;›&nbsp; {_esc(title)}</div>
        <div class="top__title">{_esc(title)}</div>
      </div>
      <div class="top__actions">{btns}</div>
    </div>
    """


# --------------------------------------------------------------------------- #
#  KPI row + filter bar
# --------------------------------------------------------------------------- #
def kpi_row_html(tiles: list[dict]) -> str:
    """tiles: list of {label,value,sub,flag}."""
    cells = []
    for t in tiles:
        cls = "kpi kpi--flag" if t.get("flag") else "kpi"
        cells.append(
            f'<div class="{cls}"><div class="kpi__l">{_esc(t["label"])}</div>'
            f'<div class="kpi__v">{_esc(t["value"])}</div>'
            f'<div class="kpi__s">{_esc(t["sub"])}</div></div>')
    return f'<div class="kpis">{"".join(cells)}</div>'


def filter_bar_html(active: str, counts: dict[str, int], sid: str = "") -> str:
    q = f"&sid={sid}" if sid else ""
    labels = [("all", "All"), ("critical", "Critical"), ("warning", "Warning"), ("ok", "OK")]
    chips = []
    for key, label in labels:
        on = " is-on" if active == key else ""
        chips.append(
            f'<a class="fchip{on}" href="?step=reconcile&flt={key}{q}" target="_self">'
            f'{label} <b>{counts.get(key, 0)}</b></a>')
    legend = ('<div class="legend"><i style="background:#9fb2cc"></i> Built '
              '<i style="background:var(--blue);margin-left:8px"></i> Billed</div>')
    return (f'<div class="bar-row"><div class="filters">{"".join(chips)}</div>'
            f'{legend}</div>')


# --------------------------------------------------------------------------- #
#  reconciliation rows
# --------------------------------------------------------------------------- #
def _chip_label(row: ReconRow) -> str:
    if not row.flags:
        return "Reconciled"
    # short chip text mirroring the mockup
    rule = row.flags[0].rule
    return {
        "no_contract": "No contract item",
        "qty_over": "Billed qty exceeds built",
        "price_over": "Unit price over contract",
        "over_run": f"Over bid estimate",
        "under_billed": "Built, not yet billed",
        "price_under": "Price below contract",
        "unmatched": "Unmatched line",
    }.get(rule, row.flags[0].message[:28])


def _feed_rows(row: ReconRow) -> str:
    feeds = []
    if row.asbuilt_refs:
        feeds.append(f'<div class="feed"><span>As-built</span><div>'
                     f'{_esc("; ".join(row.asbuilt_refs))}</div></div>')
    if row.invoice_refs:
        feeds.append(f'<div class="feed"><span>Invoice</span><div>'
                     f'{_esc("; ".join(row.invoice_refs))}</div></div>')
    for f in row.flags:
        feeds.append(f'<div class="feed"><span>Finding</span><div>'
                     f'<b>{_esc(f.severity.value.upper())}</b> — {_esc(f.message)}</div></div>')
    if not feeds:
        feeds.append('<div class="feed"><span>Finding</span><div>Reconciled cleanly. '
                     'No action.</div></div>')
    return "".join(feeds)


def recon_row_html(row: ReconRow, open_: bool = False) -> str:
    sev = _SEV_CLASS[row.severity]
    scale = max(row.built_qty, row.billed_qty, 1)
    built_pct = 100 * max(row.built_qty, 0) / scale
    billed_pct = 100 * max(row.billed_qty, 0) / scale

    dq = row.qty_delta
    if abs(dq) < 1e-9:
        delta = '<span class="delta delta--dn">match</span>'
    elif dq > 0:
        delta = f'<span class="delta delta--up">+{_qty(dq)}</span>'
    else:
        delta = f'<span class="delta delta--dn">{_qty(dq)}</span>'

    var = row.amount_variance
    var_cls = "up" if var > 0.5 else ("dn" if var < -0.5 else "ok")
    code = _esc(row.code) if row.code else "—"
    built_disp = _qty(row.built_qty) if row.built_qty else "—"
    uom_line = f"{row.uom.value}"
    co_tag = '<span class="co-tag">Change order</span>' if row.is_change_order else ""

    return f"""<details class="rrow flag-{sev}"{' open' if open_ else ''}>
      <summary>
        <div class="rid">
          <span class="rid__code">{code}</span>
          <div><div class="rid__desc">{_esc(row.description)}{co_tag}</div>
          <div class="rid__uom">{_esc(uom_line)}</div></div>
        </div>
        <div class="bars">
          <div class="bline"><span class="bline__k">Built</span>
            <div class="btrack"><div class="bfill bfill--built" style="width:{built_pct:.1f}%"></div></div>
            <span class="bline__v">{built_disp}</span></div>
          <div class="bline"><span class="bline__k">Billed</span>
            <div class="btrack"><div class="bfill bfill--billed" style="width:{billed_pct:.1f}%"></div></div>
            <span class="bline__v">{_qty(row.billed_qty)}{delta}</span></div>
        </div>
        <div class="rvar">
          <div class="rvar__l">Variance</div>
          <div class="rvar__v {var_cls}">{_money_signed(var)}</div>
          <span class="chip chip--{sev}">{_esc(_chip_label(row))}</span>
        </div>
      </summary>
      <div class="rrow__detail">{_feed_rows(row)}</div>
    </details>"""


def recon_list_html(rows: list[ReconRow]) -> str:
    return f'<div class="recon">{"".join(recon_row_html(r) for r in rows)}</div>'


# --------------------------------------------------------------------------- #
#  cards / tables / files / crosswalk
# --------------------------------------------------------------------------- #
def lede(text: str) -> str:
    return f'<p class="lede">{_esc(text)}</p>'


def card_open(title: str, note_html: str = "") -> str:
    note = f'<div class="card__note">{note_html}</div>' if note_html else ""
    return (f'<div class="card"><div class="card__h">'
            f'<div class="card__t">{_esc(title)}</div>{note}</div>')


def table_html(headers: list[tuple[str, str]], rows: list[list[str]]) -> str:
    """headers: list of (label, align) where align in {'', 'r'}. rows: pre-rendered
    cell HTML strings aligned to headers."""
    thead = "".join(
        f'<th class="{a}">{_esc(h)}</th>' for h, a in headers)
    body = []
    for r in rows:
        tds = "".join(cell for cell in r)
        body.append(f"<tr>{tds}</tr>")
    return (f'<div class="scroll"><table class="tbl"><thead><tr>{thead}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def td(value, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return f"<td{c}>{_esc(value)}</td>"


def badge(text: str, kind: str) -> str:
    """kind in {'ok','low'}."""
    return f'<span class="badge badge--{kind}">{_esc(text)}</span>'


def file_row(kind: str, name: str, meta: str, status_badge: str = "") -> str:
    return (f'<div class="file"><div class="file__i">{_esc(kind)}</div>'
            f'<div style="flex:1"><div class="file__n">{_esc(name)}</div>'
            f'<div class="file__m">{_esc(meta)}</div></div>{status_badge}</div>')


def card_close() -> str:
    return "</div>"


def xw_card_html(raw: str, suggestion_html: str, confirm_href: str,
                 change_href: str, confirm_label: str = "Confirm",
                 low: bool = False) -> str:
    """One crosswalk review card. Actions are query-param links (state persists
    across the reload, so this stays purely declarative HTML).

    Emitted as a single blank-line-free string: a whitespace-only line (e.g. from
    an omitted "Change" link) would read as a Markdown blank line and terminate the
    HTML block, spilling every following card out as raw text.
    """
    actions = (f'<a class="btn btn--pri btn--sm" href="{confirm_href}" '
               f'target="_self">{_esc(confirm_label)}</a>')
    if not low:
        actions += (f'<a class="btn btn--sm" href="{change_href}" '
                    f'target="_self">Change</a>')
    return (
        '<div class="xw"><div>'
        '<div class="xw__from">Source text</div>'
        f'<div class="xw__raw">{_esc(raw)}</div>'
        f'<div class="xw__sug">{suggestion_html}</div>'
        f'</div><div class="xw__act">{actions}</div></div>'
    )


def xw_resolved_html(raw: str, target_html: str, change_href: str) -> str:
    return (
        '<div class="xw is-resolved"><div>'
        f'<div class="xw__raw">{_esc(raw)}</div>'
        f'<div class="xw__done">✓ {target_html}</div>'
        f'</div><div class="xw__act">'
        f'<a class="btn btn--sm" href="{change_href}" target="_self">Change</a>'
        '</div></div>'
    )

