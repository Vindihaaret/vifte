"""Temperaturvarsler: avgjør når et skrivebordsvarsel skal sendes (uten GTK, så det kan testes).

En regel er {"sensor": id, "temp": grense}. Et varsel sendes når sensoren har vært over grensen
i HOLD sekunder (korte topper gir ikke varsel), og ikke igjen før den har vært REARM grader under.
I tillegg varsles det når nødmodus slår inn og når en sensor slutter å svare.
"""

HOLD = 10.0
REARM = 3.0


class AlertMonitor:
    def __init__(self):
        self.above_since = {}   # regel-nøkkel -> tidspunkt
        self.fired = set()      # regler som har varslet og ikke er nullstilt
        self.failsafe = None    # None = ikke sett ennå (ikke varsle om det som pågikk ved oppstart)
        self.lost = None

    @staticmethod
    def key(rule):
        return f"{rule.get('sensor')}@{rule.get('temp')}"

    def check(self, now, rules, value_of, failsafe_active=False, lost_controls=()):
        """Returnerer nye hendelser: ("temp", regel, verdi), ("failsafe",) eller ("lost", kontroll-ider)."""
        events = []
        keys = set()
        for rule in rules:
            k = self.key(rule)
            keys.add(k)
            v = value_of(rule.get("sensor"))
            limit = float(rule.get("temp", 0))
            if v is None:
                self.above_since.pop(k, None)
                continue
            if v >= limit:
                since = self.above_since.setdefault(k, now)
                if k not in self.fired and now - since >= HOLD:
                    self.fired.add(k)
                    events.append(("temp", rule, v))
            else:
                self.above_since.pop(k, None)
                if v <= limit - REARM:
                    self.fired.discard(k)
        # glem regler som er fjernet
        for k in list(self.above_since):
            if k not in keys:
                del self.above_since[k]
        self.fired &= keys

        if self.failsafe is not None and failsafe_active and not self.failsafe:
            events.append(("failsafe",))
        self.failsafe = bool(failsafe_active)

        lost = set(lost_controls)
        if self.lost is not None and lost - self.lost:
            events.append(("lost", sorted(lost - self.lost)))
        self.lost = lost
        return events
