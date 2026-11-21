"""Utseende uten GTK, så det kan testes: fargetokens, fargetone, status og oversikt."""

HEAT_STEPS = (50.0, 65.0, 80.0)   # grenser mellom cool, mild, warm og hot (°C)
HEAT_NAMES = ("cool", "mild", "warm", "hot")
HEAT_MARGIN = 2.0                 # så tonen ikke blafrer når temperaturen ligger på en grense
CALM_BELOW, HOT_FROM = 60.0, 80.0

# Fargetokens fra designspesifikasjonen (docs/redesign/README.md, seksjon 2). Lastes som @define-color,
# så de samme navnene gjelder i lys og mørk modus og kan slås opp fra Cairo-tegningen i graph.py.
ACCENT_FG = "#1D1D1F"
PALETTE = {
    "dark": {
        "vifte_cpu": "#7AA2D2", "vifte_cpu_surface": "#34506E",
        "vifte_gpu": "#D29A6C", "vifte_gpu_surface": "#9A6236",
        "vifte_neutral": "#DCDEE3", "vifte_neutral_surface": "#4A505C", "vifte_idle": "alpha(#DCDEE3, 0.38)",
        "vifte_accent": "#F0A400", "vifte_accent_text": "#F5B83D",
        "vifte_text": "#F5F5F7", "vifte_sub": "#B4B8C2",
        "vifte_sidebar": "alpha(white, 0.075)", "vifte_card": "alpha(white, 0.055)",
        "vifte_fill": "alpha(white, 0.08)", "vifte_track": "alpha(white, 0.10)", "vifte_line": "alpha(white, 0.09)",
        "vifte_selected": "alpha(#F0A400, 0.20)",
    },
    "light": {
        "vifte_cpu": "#2F5A8A", "vifte_cpu_surface": "#34506E",
        "vifte_gpu": "#9A5B26", "vifte_gpu_surface": "#9A6236",
        "vifte_neutral": "#2E3138", "vifte_neutral_surface": "#5B616C", "vifte_idle": "alpha(#2E3138, 0.32)",
        "vifte_accent": "#C47F00", "vifte_accent_text": "#8F5D00",
        "vifte_text": "#1D1D1F", "vifte_sub": "#55585E",
        "vifte_sidebar": "alpha(white, 0.62)", "vifte_card": "alpha(white, 0.66)",
        "vifte_fill": "alpha(black, 0.055)", "vifte_track": "alpha(black, 0.08)", "vifte_line": "alpha(black, 0.08)",
        "vifte_selected": "alpha(#C47F00, 0.16)",
    },
}
STATUS_COLORS = {"vifte_calm": "#2EC27E", "vifte_warm": "#E5A50A", "vifte_hot": "#E66100",
                 "vifte_emergency": "#E01B24"}


def palette_css(dark, css_vars=False):
    """CSS med fargetokens og Vifte-oransje som aksent. css_vars: GTK ≥ 4.16, der libadwaita (≥ 1.6)
    leser aksenten fra CSS-variabler i stedet for @define-color."""
    colors = {**PALETTE["dark" if dark else "light"], **STATUS_COLORS}
    lines = [f"@define-color {name} {value};" for name, value in colors.items()]
    accent, text = colors["vifte_accent"], colors["vifte_accent_text"]
    lines += [f"@define-color accent_bg_color {accent};", f"@define-color accent_color {text};",
              f"@define-color accent_fg_color {ACCENT_FG};"]
    if css_vars:
        lines.append(f":root {{ --accent-bg-color: {accent}; --accent-color: {text}; "
                     f"--accent-fg-color: {ACCENT_FG}; }}")
    return "\n".join(lines) + "\n"


def heat_step(temp, previous=None, emergency=False):
    """Fargetonen for vinduet. Går ned først når temperaturen er HEAT_MARGIN under grensen."""
    if emergency:
        return "emergency"
    if temp is None:
        return None
    step = sum(temp >= limit for limit in HEAT_STEPS)
    if previous in HEAT_NAMES:
        old = HEAT_NAMES.index(previous)
        if step == old - 1 and temp >= HEAT_STEPS[old - 1] - HEAT_MARGIN:
            return previous
    return HEAT_NAMES[step]


def temp_status(temp, emergency=False):
    """Status for CPU- eller GPU-kortet: ("calm"|"warm"|"hot"|"emergency") eller None."""
    if temp is None:
        return None
    if emergency:
        return "emergency"
    return "calm" if temp < CALM_BELOW else "warm" if temp < HOT_FROM else "hot"


def fan_summary(control_state):
    """(snitt av styrte vifter i prosent eller None, antall styrte, antall pumper)."""
    pumps = sum(1 for st in control_state.values() if st.get("pump"))
    outs = [st["out"] for st in control_state.values() if st.get("out") is not None and not st.get("pump")]
    return (sum(outs) / len(outs) if outs else None), len(outs), pumps


def safety_state(state):
    """Det viktigste å si om sikkerheten akkurat nå."""
    failsafe = state.get("failsafe") or {}
    if failsafe.get("active"):
        return "emergency"
    if (state.get("sensor_lost") or {}).get("controls"):
        return "sensor_lost"
    if state.get("paused"):
        return "paused"
    return "ok" if failsafe.get("enabled") else "no_emergency"
