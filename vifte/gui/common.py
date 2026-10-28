"""Felles hjelpere for GUI-et: modellen (tjenestetilstand + konfigurasjon) og små widgets."""
import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..config import FanControlConfig  # noqa: E402
from ..i18n import _, get_setting  # noqa: E402


class Model:
    """Holder siste tilstand fra tjenesten og en lokal kopi av konfigurasjonen.
    Alle endringer gjøres i self.cfg og sendes straks til tjenesten (push)."""

    def __init__(self, client):
        self.client = client
        self.state = {}
        self.cfg = FanControlConfig()
        self.rev = -1
        self.name = ""
        self.history = {"t": [], "series": {}}
        # Hastighetsmålere uten eget navn vises med navnet til viften de er paret med (kan slås av).
        self.names_follow_pairing = bool(get_setting("names_follow_pairing", True))

    # ---- konfigurasjon
    def fetch_config(self):
        r = self.client.call("get_config")
        self.cfg = FanControlConfig(r["raw"])
        self.rev = r["rev"]
        self.name = r["name"]

    def push(self, save=False):
        r = self.client.call("apply_config", raw=self.cfg.raw, save=save)
        self.rev = r["rev"]

    @property
    def fahrenheit(self):
        return bool(self.cfg.window.get("Fahrenheit"))

    @property
    def show_hidden(self):
        return bool(self.cfg.window.get("ShowHiddenCards"))

    # ---- verdier
    def local_id(self, config_id):
        return (self.state.get("resolved") or {}).get(config_id, config_id)

    def sensor(self, config_id):
        lid = self.local_id(config_id)
        for s in self.state.get("sensors", []):
            if s["id"] == lid:
                return s
        return None

    def temp_value(self, config_id):
        if config_id is None:
            return None
        custom = self.state.get("custom") or {}
        if config_id in custom:
            return custom[config_id]
        s = self.sensor(config_id)
        return s["value"] if s else None

    def history_values(self, config_id):
        """(tidspunkter, verdier) fra tjenestens historikk for en sensor i konfigurasjonen."""
        series = self.history.get("series") or {}
        vals = series.get(config_id) or series.get(self.local_id(config_id)) or []
        return self.history.get("t") or [], vals

    def own_name(self, entry):
        """Navnet brukeren har gitt, eller None. FanControl lagrer også maskinvarenavnet
        («Fan #7») som NickName, så et navn regnes som eget bare når det er noe annet."""
        nick = entry.get("NickName")
        if not nick:
            return None
        lid = self.local_id(entry.get("Identifier"))
        hw = {x.get("name") for x in self.state.get("sensors", []) + self.state.get("controls", [])
              if x.get("id") == lid}
        if nick in hw | {entry.get("Name")} or re.fullmatch(r"(fan|control|pump|sensor)\s*#?\s*\d+", nick.lower()):
            return None
        return nick

    def paired_control_name(self, ident):
        """Navnet til kontrollen en hastighetsmåler er paret med, hvis den har fått et navn."""
        local = self.local_id(ident)
        for c in self.cfg.controls:
            ref = ((c.get("PairedFanSensor") or c.get("MatchingFanSensor") or {}).get("Identifier"))
            if ref and (ref == ident or self.local_id(ref) == local) and self.own_name(c):
                return self.own_name(c)
        return None

    def display_name(self, ident, default=None):
        """Navnet som vises i appen. Bare til visning – skrives aldri inn i konfigurasjonen, så
        filene forblir som i FanControl på Windows."""
        e = self.cfg.sensor_entry(ident)
        if (self.names_follow_pairing and e is not None and not self.own_name(e)
                and any(f is e for f in self.cfg.fan_sensors)):
            name = self.paired_control_name(ident)
            if name:
                return name
        return self.cfg.display_name(ident, default)

    def fmt_temp(self, v):
        if v is None:
            return "–"
        if self.fahrenheit:
            return f"{v * 9 / 5 + 32:.1f} °F"
        return f"{v:.1f} °C"

    def temp_choices(self, include_custom=True, exclude=None):
        """[(config_id, visningsnavn)] for alle temperaturkilder."""
        out = []
        for e in self.cfg.temp_sensors:
            ident = e.get("Identifier")
            s = self.sensor(ident)
            hwname = s["hardware"] if s else _("Mangler")
            out.append((ident, f"{hwname} / {e.get('NickName') or e.get('Name') or ident}"))
        if include_custom:
            for c in self.cfg.custom_sensors:
                if c.get("Identifier") != exclude:
                    out.append((c.get("Identifier"), _("Egendefinert / {}").format(c.get("Name"))))
        return out

    def fan_choices(self):
        out = []
        for e in self.cfg.fan_sensors:
            ident = e.get("Identifier")
            s = self.sensor(ident)
            hwname = s["hardware"] if s else _("Mangler")
            out.append((ident, f"{hwname} / {self.display_name(ident)}"))
        return out


def combo_row(title, choices, selected_id, on_change, none_label=None, subtitle=None):
    """Adw.ComboRow over [(id, tekst)]. on_change(id) kalles ved endring."""
    ids = ([None] if none_label else []) + [c[0] for c in choices]
    labels = ([none_label] if none_label else []) + [c[1] for c in choices]
    if selected_id is not None and selected_id not in ids:
        ids.append(selected_id)
        labels.append(_("Mangler: {}").format(selected_id))
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
    if subtitle:
        row.set_subtitle(subtitle)
    row.set_enable_search(len(labels) > 8)
    row.set_selected(ids.index(selected_id) if selected_id in ids else 0)
    row.connect("notify::selected", lambda r, _p: on_change(ids[r.get_selected()]))
    return row


def spin_row(title, value, lo, hi, step, on_change, digits=0, subtitle=None):
    adj = Gtk.Adjustment(value=float(value or 0), lower=lo, upper=hi, step_increment=step,
                         page_increment=step * 10)
    row = Adw.SpinRow(title=title, adjustment=adj, digits=digits)
    if subtitle:
        row.set_subtitle(subtitle)

    def clamp_typing(r):
        # Spinnfeltet retter først verdien når feltet forlates; vis maks med én gang i stedet
        # for at f.eks. 150 % står der mens man skriver. (Bare øvre grense: et tall under
        # minimum kan være starten på et gyldig tall.)
        try:
            v = float(r.get_text().replace(",", ".").strip())
        except ValueError:
            return
        if v > hi:
            GLib.idle_add(lambda: (r.set_value(hi), r.set_text(r.get_text()))[-1] and False)
    row.connect("changed", clamp_typing)
    row.connect("notify::value", lambda r, _p: on_change(
        round(r.get_value(), digits) if digits else int(round(r.get_value()))))
    return row


def switch_row(title, active, on_change, subtitle=None):
    row = Adw.SwitchRow(title=title, active=bool(active))
    if subtitle:
        row.set_subtitle(subtitle)
    row.connect("notify::active", lambda r, _p: on_change(r.get_active()))
    return row


def entry_row(title, text, on_change):
    row = Adw.EntryRow(title=title, text=text or "")
    row.set_show_apply_button(True)
    row.connect("apply", lambda r: on_change(r.get_text().strip()))
    return row
