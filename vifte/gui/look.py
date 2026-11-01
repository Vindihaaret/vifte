"""Utseende som avhenger av målinger: fargetone, status og oversikt (uten GTK, så det kan testes)."""

HEAT_STEPS = (50.0, 65.0, 80.0)   # grenser mellom cool, mild, warm og hot (°C)
HEAT_NAMES = ("cool", "mild", "warm", "hot")
HEAT_MARGIN = 2.0                 # så tonen ikke blafrer når temperaturen ligger på en grense
CALM_BELOW, HOT_FROM = 60.0, 80.0


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
