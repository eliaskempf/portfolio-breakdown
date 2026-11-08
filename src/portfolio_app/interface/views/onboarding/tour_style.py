"""Tour presentation. Content keeps its native colors inside SVG cutouts."""
CSS = '''
.st-key-app_tour {
    position:fixed;right:24px;bottom:24px;width:370px!important;
    max-width:calc(100vw - 24px)!important;max-height:calc(100dvh - 90px);
    overflow:auto;z-index:1000001;padding:20px!important;
    background:var(--tour-surface,#fff)!important;color:var(--tour-text,#202632)!important;
    border:1px solid var(--tour-border,#a4b3de)!important;border-radius:16px!important;
    box-shadow:0 12px 45px #0006;gap:12px!important;
}
.st-key-app_tour h3 {font-size:1.2rem!important;line-height:1.3!important;color:var(--tour-text,#202632)!important;padding:0 0 4px!important;}
.st-key-app_tour p {font-size:.9rem;line-height:1.5;color:var(--tour-body,#384354);}
.st-key-app_tour [data-testid="stCaptionContainer"] p {font-size:.65rem;letter-spacing:.05em;color:var(--tour-muted,#4c5c88);}
.st-key-app_tour [data-testid="stVerticalBlock"] {gap:10px;}
.st-key-tour_actions {justify-content:flex-end;flex-wrap:nowrap!important;gap:8px!important;}
.st-key-tour_actions > :first-child {margin-right:auto;}
.st-key-app_tour button {min-height:38px;border-radius:8px!important;}
.st-key-app_tour button p {font-size:.82rem;font-weight:600;color:inherit;}
.st-key-app_tour button[kind="primary"] {background:#586bd7!important;border-color:#7f94ff!important;color:white!important;}
.st-key-app_tour button[kind="secondary"] {background:var(--tour-button,#f3f5fb)!important;border-color:var(--tour-button-border,#b9c4df)!important;color:var(--tour-text,#202632)!important;}
.st-key-app_tour button[kind="tertiary"] {color:var(--tour-muted,#4c5c88)!important;min-height:28px;}
.st-key-app_tour button:disabled {opacity:.4;}
.st-key-app_tour button:focus-visible {outline:3px solid #8eaaff;outline-offset:3px;}
.st-key-tour_skip button {border-color:transparent!important;background:transparent!important;}
.st-key-app_tour > [data-testid="stLayoutWrapper"]:has(> .st-key-tour_actions) {position:sticky;bottom:-20px;background:var(--tour-surface,#fff);z-index:1;padding-top:6px;}
.portfolio-tour-overlay {position:fixed;inset:0;width:100vw;height:100dvh;z-index:1000000;pointer-events:none;}
.portfolio-tour-accent p {color:#fff!important;}
.portfolio-tour-accent {box-shadow:inset 0 -3px #637beb!important;background:#5265ce!important;color:#fff!important;border-radius:5px;}
body:has(.st-key-app_tour) .stMainBlockContainer {padding-bottom:420px;}
@media(max-width:700px) {
    .st-key-app_tour {width:calc(100vw - 24px)!important;padding:14px!important;max-height:calc(100dvh - 140px);border-radius:12px!important;}
    .st-key-app_tour h3 {font-size:1.04rem!important;}
    .st-key-app_tour p {font-size:.82rem;line-height:1.4;}
    .st-key-app_tour [data-testid="stVerticalBlock"] {gap:7px;}
}
'''
