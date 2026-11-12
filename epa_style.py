"""Small local stylesheet. No external fonts, scripts, analytics, or requests."""
from __future__ import annotations

STYLES = """
<style>
:root { --epa-ink: #183C50; --epa-teal: #176B72; --epa-soft: #F2F6F8; --epa-line: #DEE7EA; }
.stApp { background: #FAFCFD; }
[data-testid="stMainBlockContainer"], .main .block-container {
    max-width: 1450px; padding-top: 2rem; padding-bottom: 2.5rem;
}
h1, h2, h3 { letter-spacing: -.025em; color: var(--epa-ink); }
h1 { font-size: 2rem; font-weight: 720; }
h2 { font-size: 1.65rem; }
[data-testid="stCaptionContainer"] { color: #506777; }
[data-testid="stSidebar"] { background: #F2F6F8; border-right: 1px solid var(--epa-line); }
[data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding-top: 1rem; }
[data-testid="stSidebar"] [role="radiogroup"] { gap: .5rem; }
[data-testid="stSidebar"] [role="radiogroup"] > label {
    border: 1px solid transparent; border-radius: 10px; padding: .6rem .7rem;
    margin: 0; background: transparent; transition: background .12s ease;
}
[data-testid="stSidebar"] [role="radiogroup"] > label:hover { background: #E7F0F2; }
[data-testid="stSidebar"] [role="radiogroup"] > label:has(input:checked) {
    background: #E0EFF0; border-color: #B6D3D7;
}
[data-testid="stMetric"] {
    padding: 1rem 1.1rem; border: 1px solid var(--epa-line); border-radius: 12px;
    background: #FFFFFF; box-shadow: 0 2px 6px rgba(24,60,80,.025);
}
[data-testid="stMetricValue"] { color: var(--epa-ink); font-size: 1.75rem; }
[data-testid="stMetricLabel"] { color: #506777; }
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
    border: 1px solid var(--epa-line); border-radius: 10px; overflow: hidden;
}
[data-testid="stExpander"] { border-radius: 10px; background: #FFFFFF; }
[data-testid="stForm"] { border-radius: 12px; background: #FFFFFF; }
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
    border-radius: 9px; min-height: 2.6rem; font-weight: 600;
}
button[kind="primary"] { background: var(--epa-teal); border-color: var(--epa-teal); }
button:focus-visible, input:focus-visible { outline: 3px solid #93BBC2; outline-offset: 2px; }
.epa-brand { color: var(--epa-ink); font-size: 1.5rem; font-weight: 750; line-height: 1.2; margin-bottom: .3rem; }
.epa-kicker { color: var(--epa-teal); font-size: .72rem; font-weight: 700; letter-spacing: .11em; margin-bottom: .5rem; }
.epa-login { text-align: center; padding: 1.2rem 0 .6rem; }
.epa-login h1 { margin: .25rem 0; }
.epa-login p { color: #506777; margin: .35rem 0; }
.epa-period { padding: .7rem .9rem; background: #EDF4F6; border-radius: 8px; color: #36576A; margin-bottom: 1rem; font-size: .9rem; }
@media (max-width: 760px) {
    [data-testid="stMainBlockContainer"], .main .block-container { padding: 1rem .8rem 2rem; }
    h1 { font-size: 1.7rem; } h2 { font-size: 1.4rem; }
    [data-testid="stMetric"] { padding: .7rem; }
    [data-testid="stMetricValue"] { font-size: 1.4rem; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
"""


def apply_styles(st) -> None:
    st.markdown(STYLES, unsafe_allow_html=True)


def page_header(st, title: str, description: str) -> None:
    st.header(title)
    st.caption(description)


def sidebar_brand(st) -> None:
    st.markdown('<div class="epa-kicker">RESIDENCY EDUCATION</div>'
                '<div class="epa-brand">EPA Tracker</div>', unsafe_allow_html=True)
    st.caption("Resident assessment workspace")
