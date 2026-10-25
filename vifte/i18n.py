"""Oversettelser. Teksten i koden er norsk; _() gir engelsk når det er valgt.

Språket velges i Innstillinger (lagres i ~/.config/vifte/settings.json) eller
følger systemspråket: norsk for nb/nn/no, ellers engelsk.
"""
import json
import os

LANGUAGES = [("auto", "Automatisk (systemspråk)"), ("nb", "Norsk"), ("en", "English"),
             ("de", "Deutsch"), ("es", "Español"), ("fr", "Français")]
CODES = {code for code, _label in LANGUAGES if code != "auto"}
_OTHER = {}   # språkkode -> {engelsk tekst: oversettelse}, lastes ved behov

EN = {
    # --- hovedvindu og meny
    "Kontroller": "Controls",
    "Kurver": "Curves",
    "Hastigheter": "Speeds",
    "Sensorer": "Sensors",
    "Lagre": "Save",
    "Meny": "Menu",
    "Konfigurasjon (profil)": "Configuration (profile)",
    "Konfigurasjoner": "Configurations",
    "Lagre konfigurasjonen (Ctrl+S)": "Save the configuration (Ctrl+S)",
    "Importer FanControl-konfigurasjon …": "Import FanControl configuration …",
    "Eksporter konfigurasjon …": "Export configuration …",
    "Ny tom konfigurasjon …": "New empty configuration …",
    "Lagre som …": "Save as …",
    "Slett konfigurasjon …": "Delete configuration …",
    "Par vifter automatisk": "Pair fans automatically",
    "Koble manglende sensorer …": "Map missing sensors …",
    "Søk etter maskinvare på nytt": "Rescan hardware",
    "Vis skjulte kort": "Show hidden cards",
    "Fahrenheit": "Fahrenheit",
    "Pause styring (BIOS overtar)": "Pause control (BIOS takes over)",
    "Innstillinger …": "Settings …",
    "Innstillinger": "Settings",
    "Om Vifte": "About Vifte",
    "Avinstaller Vifte …": "Uninstall Vifte …",
    "Får ikke kontakt med tjenesten vifted ({}). Kjør: sudo systemctl enable --now vifted":
        "Cannot reach the vifted service ({}). Run: sudo systemctl enable --now vifted",
    "Feil: {}": "Error: {}",
    "Kunne ikke sende endring: {}": "Could not send change: {}",
    "Lagret {}": "Saved {}",
    "Feil ved lagring: {}": "Error while saving: {}",
    "Mangler på denne maskinen": "Missing on this machine",
    "Ingen styrbare vifter funnet": "No controllable fans found",
    "Last inn driveren for hovedkortets Super I/O-brikke (f.eks. «sudo modprobe nct6775») og søk på nytt.":
        "Load the driver for the motherboard's Super I/O chip (e.g. “sudo modprobe nct6775”) and rescan.",
    "Aktiver styring": "Enable control",
    "(ingen kurve)": "(no curve)",
    "BIOS styrer": "BIOS controls",
    "MANUELL": "MANUAL",
    "Ny kurve": "New curve",
    "Ingen kurver ennå": "No curves yet",
    "Lag en kurve og velg den på en kontroll.": "Create a curve and select it on a control.",
    "Sensor mangler": "Sensor missing",
    "Lukk": "Close",
    "Vis": "Show",
    "Skjul": "Hide",
    "Gi nytt navn": "Rename",
    "Nytt navn": "New name",
    "Egendefinerte sensorer": "Custom sensors",
    "Ny sensor": "New sensor",
    "Kalibrerer: ": "Calibrating: ",
    "Parer vifter: ": "Pairing fans: ",
    "Kalibrering: ": "Calibration: ",
    "Paring: ": "Pairing: ",
    "Avbryt": "Cancel",
    "{} sensor(er) i konfigurasjonen finnes ikke på denne maskinen":
        "{} sensor(s) in the configuration are not present on this machine",
    "Koble til …": "Map …",
    "Styring er satt på pause – BIOS/driver styrer viftene":
        "Control is paused – BIOS/driver is controlling the fans",
    "Fortsett": "Resume",
    "Testmodus: ingenting skrives til maskinvaren": "Test mode: nothing is written to the hardware",
    "Ulagrede endringer": "Unsaved changes",
    "Vil du lagre endringene i {} først?": "Do you want to save the changes to {} first?",
    "Forkast": "Discard",
    "FanControl-konfigurasjon (*.json)": "FanControl configuration (*.json)",
    "Importer FanControl-konfigurasjon": "Import FanControl configuration",
    "Importert som {}": "Imported as {}",
    "Eksporter konfigurasjon": "Export configuration",
    "Eksportert – filen kan brukes i FanControl på Windows":
        "Exported – the file can be used in FanControl on Windows",
    "Navn på ny konfigurasjon": "Name of new configuration",
    "Ny": "New",
    "Lagre som": "Save as",
    "Det finnes ingen andre konfigurasjoner å slette": "There are no other configurations to delete",
    "Slett konfigurasjon": "Delete configuration",
    "Den aktive konfigurasjonen kan ikke slettes.": "The active configuration cannot be deleted.",
    "Slett": "Delete",
    "Hver vifteutgang kjøres kort på 100 % for å finne hvilken hastighetssensor som hører til. "
    "Det tar omtrent 15 sekunder per kontroll.":
        "Each fan output is briefly run at 100 % to find the matching speed sensor. "
        "It takes about 15 seconds per control.",
    "Start": "Start",
    "Maskinvare søkt på nytt": "Hardware rescanned",
    "Tjeneste": "Service",
    "Oppdateringsintervall (s)": "Update interval (s)",
    "Visning": "Display",
    "Skjul hastighetskort": "Hide fan speed cards",
    "Tema": "Theme",
    "Vis Vifte": "Show Vifte",
    "Avslutt": "Quit",
    "Vis i statusfeltet": "Show in system tray",
    "Fjern fra statusfeltet": "Remove from system tray",
    "Statusfelt": "System tray",
    "Start i statusfeltet ved innlogging": "Start in the system tray at login",
    "Viser ikonet i statusfeltet uten å åpne vinduet": "Shows the tray icon without opening the window",
    "vis bare ikonet i statusfeltet": "only show the system tray icon",
    "Følg systemet": "Follow system",
    "Lyst": "Light",
    "Mørkt": "Dark",
    "Språk": "Language",
    "Automatisk (systemspråk)": "Automatic (system language)",
    "Viftestyring for Linux.\nLeser og skriver konfigurasjonsfiler fra FanControl (Rem0o) for Windows.":
        "Fan control for Linux.\nReads and writes configuration files from FanControl (Rem0o) for Windows.",
    "OK": "OK",
    "Hovedkort": "Motherboard",
    "NØDMODUS: {} er {} – alle styrte vifter går på 100 %":
        "EMERGENCY MODE: {} is {} – all controlled fans run at 100 %",
    "Claude (MCP)": "Claude (MCP)",
    "La Claude lese sensorer og justere kurver og vifter for deg. Sikkerhetsgrensene under håndheves av Vifte "
    "og kan ikke overstyres av Claude.":
        "Let Claude read sensors and tune curves and fans for you. The safety limits below are enforced by "
        "Vifte and cannot be overridden by Claude.",
    "Tillat styring fra Claude": "Allow control from Claude",
    "Nødmodus er alltid på mens dette er slått på": "Emergency mode is always on while this is enabled",
    "Laveste viftehastighet (%)": "Lowest fan speed (%)",
    "Claude kan aldri sette en vifte lavere enn dette, og aldri stoppe den":
        "Claude can never set a fan lower than this, and never stop it",
    "Nødtemperatur (°C)": "Emergency temperature (°C)",
    "Når CPU eller GPU når denne temperaturen, går alle styrte vifter på 100 %":
        "When the CPU or GPU reaches this temperature, all controlled fans run at 100 %",
    "Nødmodus også uten Claude": "Emergency mode without Claude too",
    "Bruk nødtemperaturen selv om Claude-tilgang er av": "Use the emergency temperature even when Claude access is off",
    "Koble til Claude Code": "Connect to Claude Code",
    "Legg til": "Add",
    "Claude-tilgang er slått av": "Claude access is turned off",
    "Claude-tilgang er slått på": "Claude access is turned on",
    "Claude kan nå lese sensorer og endre kurver og vifter innenfor sikkerhetsgrensene. Ingen omstart er "
    "nødvendig.\n\nEndringer tas i bruk med en gang, men lagres først når du ber om det. Du ser et varsel her "
    "for hver endring Claude gjør.\n\nKoble Vifte til Claude Code med:\nclaude mcp add --scope user vifte -- "
    "vifte mcp":
        "Claude can now read sensors and change curves and fans within the safety limits. No restart is "
        "needed.\n\nChanges take effect immediately but are only saved when you ask for it. You'll see a "
        "notification here for every change Claude makes.\n\nConnect Vifte to Claude Code with:\n"
        "claude mcp add --scope user vifte -- vifte mcp",
    "Legg til i Claude Code": "Add to Claude Code",
    "Vifte er lagt til i Claude Code. Start en ny Claude Code-økt for å bruke den.":
        "Vifte was added to Claude Code. Start a new Claude Code session to use it.",
    # --- logg over Claude sine endringer
    "Laget kurven «{}»": "Created curve “{}”",
    "Endret kurven «{}»": "Changed curve “{}”",
    "Slettet kurven «{}»": "Deleted curve “{}”",
    "Endret viften «{}»": "Changed fan “{}”",
    "Laget sensoren «{}»": "Created sensor “{}”",
    "Ga «{}» nytt navn": "Renamed “{}”",
    "Startet paring av vifter": "Started pairing fans",
    "Startet kalibrering av «{}»": "Started calibrating “{}”",
    "Byttet til konfigurasjonen «{}»": "Switched to configuration “{}”",
    "Lagret konfigurasjonen «{}»": "Saved configuration “{}”",
    "Angret siste endring": "Undid the last change",
    # --- oppdateringer
    "Se etter oppdateringer …": "Check for updates …",
    "Oppdateringer": "Updates",
    "Se etter oppdateringer automatisk": "Check for updates automatically",
    "Én gang i måneden, fra GitHub": "Once a month, from GitHub",
    "aldri": "never",
    "Se etter oppdateringer nå": "Check for updates now",
    "Versjon {} · sist sjekket: {}": "Version {} · last checked: {}",
    "Sjekk": "Check",
    "Kunne ikke se etter oppdateringer": "Could not check for updates",
    "Vifte er oppdatert": "Vifte is up to date",
    "Du har den nyeste versjonen ({}).": "You have the latest version ({}).",
    "Vifte {} er tilgjengelig": "Vifte {} is available",
    "Klikk for å se hva som er nytt og oppdatere.": "Click to see what's new and update.",
    "Du har versjon {}.": "You have version {}.",
    "Vifte kjører fra en kildekodemappe. Oppdater med «git pull» og kjør install.sh.":
        "Vifte is running from a source folder. Update with “git pull” and run install.sh.",
    "Hopp over denne versjonen": "Skip this version",
    "Senere": "Later",
    "Oppdater nå": "Update now",
    "Åpne GitHub": "Open GitHub",
    "Oppdaterer Vifte …": "Updating Vifte …",
    "Laster ned versjon {} …": "Downloading version {} …",
    "Vent …": "Please wait …",
    "Oppdateringen feilet": "The update failed",
    "Installerer … Godkjenn med passordet ditt.": "Installing … Confirm with your password.",
    "Oppdateringen ble avbrutt": "The update was cancelled",
    "Vifte er oppdatert til {}": "Vifte has been updated to {}",
    "Programmet startes på nytt nå.": "The app will now restart.",
    # --- avinstallering
    "Avinstaller Vifte": "Uninstall Vifte",
    "Tjenesten stoppes og viftene gis tilbake til BIOS. Programmet, menyoppføringen og driveroppsettet fjernes.":
        "The service is stopped and the fans are handed back to the BIOS. The program, menu entry and "
        "driver setup are removed.",
    "Slett også alle konfigurasjoner": "Also delete all configurations",
    "Avinstaller": "Uninstall",
    "Vifte er avinstallert.": "Vifte has been uninstalled.",
    "Avinstallering feilet: {}": "Uninstall failed: {}",
    "Vifte ser ikke ut til å være installert (fant ikke {}).":
        "Vifte does not appear to be installed ({} not found).",
    # --- kontrolldialog
    "Generelt": "General",
    "Navn": "Name",
    "Aktivert": "Enabled",
    "Når av, styrer BIOS/driveren viften selv": "When off, the BIOS/driver controls the fan",
    "Kurve": "Curve",
    "Skjult": "Hidden",
    "Manuell styring": "Manual control",
    "Overstyrer kurven med en fast verdi": "Overrides the curve with a fixed value",
    "Manuell verdi": "Manual value",
    "Justering": "Adjustment",
    "Forskyvning (offset) %": "Offset %",
    "Legges til kurvens verdi": "Added to the curve's value",
    "Minimum %": "Minimum %",
    "Går aldri under dette": "Never goes below this",
    "Start %": "Start %",
    "Brukes for å starte viften fra stillstand": "Used to kick-start the fan from a stop",
    "Stopp %": "Stop %",
    "Verdier under dette blir 0 %": "Values below this become 0 %",
    "Trinn opp %": "Step up %",
    "Maks økning per oppdatering": "Max increase per update",
    "Trinn ned %": "Step down %",
    "Maks reduksjon per oppdatering": "Max decrease per update",
    "Tving bruk (Force apply)": "Force apply",
    "Skriv verdien hver gang, også når den ikke er endret": "Write the value every time, even when unchanged",
    "Paring og kalibrering": "Pairing and calibration",
    "Hastighetssensor": "Speed sensor",
    "(ikke paret)": "(not paired)",
    "Kalibrering": "Calibration",
    "Måler RPM fra 0–100 % og finner start/stopp (tar ca. 2 min)":
        "Measures RPM from 0–100 % and finds start/stop (takes about 2 min)",
    "Kalibrer": "Calibrate",
    "Kalibrering: {}": "Calibration: {}",
    # --- kurvedialog
    "Det finnes allerede en kurve med det navnet": "A curve with that name already exists",
    "{}-kurve": "{} curve",
    "Temperaturkilde": "Temperature source",
    "(velg sensor)": "(select sensor)",
    "Utdata": "Output",
    "Prosent (%)": "Percent (%)",
    "RPM (krever kalibrering)": "RPM (requires calibration)",
    "Nå": "Now",
    "Slett kurve": "Delete curve",
    "Venstreklikk: legg til / dra punkt · Høyreklikk: fjern punkt":
        "Left click: add / drag point · Right click: remove point",
    "Temperatur °C": "Temperature °C",
    "Verdi": "Value",
    "Akse": "Axis",
    "Minimum temperatur": "Minimum temperature",
    "Maksimum temperatur": "Maximum temperature",
    "Minimum temperatur °C": "Minimum temperature °C",
    "Maksimum temperatur °C": "Maximum temperature °C",
    "Minimum hastighet %": "Minimum speed %",
    "Maksimum hastighet %": "Maximum speed %",
    "Fast verdi": "Fixed value",
    "Prosent": "Percent",
    "Funksjon": "Function",
    "Kurver som inngår": "Included curves",
    "Over lasttemperatur: lasthastighet til temperaturen faller under tomgangstemperatur, og omvendt.":
        "Above the load temperature: load speed until the temperature drops below the idle temperature, "
        "and vice versa.",
    "Tomgangstemperatur °C": "Idle temperature °C",
    "Lasttemperatur °C": "Load temperature °C",
    "Tomgangshastighet %": "Idle speed %",
    "Lasthastighet %": "Load speed %",
    "Responstid (s)": "Response time (s)",
    "Følger en annen kontroll sin utgang": "Follows another control's output",
    "Kontroll": "Control",
    "(velg kontroll)": "(select control)",
    "Forskyvning": "Offset",
    "Proporsjonal": "Proportional",
    "Forskyvning i prosent av verdien i stedet for absolutt": "Offset as a percentage of the value instead of absolute",
    "Finner laveste hastighet som holder lasttemperaturen. Under lastsonen oppfører den seg som en lineær kurve.":
        "Finds the lowest speed that holds the load temperature. Below the load zone it behaves like a "
        "linear curve.",
    "Lasttemperatur (mål) °C": "Load temperature (target) °C",
    "Trinn %": "Step %",
    "Dødbånd °C": "Deadband °C",
    "Ukjent kurvetype": "Unknown curve type",
    "Denne kurvetypen kjennes ikke igjen. Den bevares uendret i filen.":
        "This curve type is not recognised. It is kept unchanged in the file.",
    "Hysterese og responstid": "Hysteresis and response time",
    "Hysterese opp °C": "Hysteresis up °C",
    "Hysterese ned °C": "Hysteresis down °C",
    "Responstid opp (s)": "Response time up (s)",
    "Responstid ned (s)": "Response time down (s)",
    "Ignorer hysterese ved min/maks": "Ignore hysteresis at min/max",
    "Hysterese °C": "Hysteresis °C",
    "Kun én vei (bare ved synkende temperatur)": "One way (only when temperature falls)",
    "Slette «{}»?": "Delete “{}”?",
    "Kontroller som bruker kurven mister den.": "Controls using the curve will lose it.",
    # --- egendefinerte sensorer
    "Verdi nå": "Current value",
    "Sensorer som inngår": "Included sensors",
    "Kilde": "Source",
    "Periode (s)": "Period (s)",
    "Fil (.sensor)": "File (.sensor)",
    "Format": "Format",
    "Første linje i filen: temperatur i °C som tekst. Relative navn leses fra /var/lib/vifte/sensors/":
        "First line of the file: temperature in °C as text. Relative names are read from /var/lib/vifte/sensors/",
    "Proporsjonal (%)": "Proportional (%)",
    "Slett sensor": "Delete sensor",
    "Egendefinert / {}": "Custom / {}",
    "Mangler": "Missing",
    "Mangler: {}": "Missing: {}",
    # --- omkobling
    "Manglende sensorer": "Missing sensors",
    "Koble til sensorer på denne maskinen": "Map to sensors on this machine",
    "Konfigurasjonen refererer til maskinvare som ikke ble funnet. Velg hva hver oppføring skal bruke her. "
    "Filen endres ikke, så den fungerer fortsatt i FanControl på Windows.":
        "The configuration refers to hardware that was not found. Choose what each entry should use here. "
        "The file is not changed, so it still works in FanControl on Windows.",
    "Ingen manglende sensorer 🎉": "No missing sensors 🎉",
    "(ikke koblet)": "(not mapped)",
    # --- typer
    "Graf": "Graph", "Lineær": "Linear", "Flat": "Flat", "Mix": "Mix", "Trigger": "Trigger",
    "Synk": "Sync", "Auto": "Auto", "Ukjent": "Unknown",
    "Maks": "Max", "Min": "Min", "Gjennomsnitt": "Average", "Sum": "Sum", "Differanse": "Subtract",
    "Tidsgjennomsnitt": "Time average", "Fil": "File",
    # --- meldinger fra tjenesten
    "Starter …": "Starting …",
    "Ferdig": "Done",
    "Avbrutt": "Cancelled",
    "Kjører viften på 100 % …": "Running the fan at 100 % …",
    "Måler {} % …": "Measuring {} % …",
    "Måler stopp …": "Measuring stop …",
    "Finner startpunkt ({} %) …": "Finding start point ({} %) …",
    "Setter alle vifter til {} % …": "Setting all fans to {} % …",
    "Tester {} …": "Testing {} …",
    "en annen jobb kjører allerede": "another job is already running",
    "kontrollen må være paret med en hastighetssensor først": "the control must be paired with a speed sensor first",
    "kan ikke slette aktiv konfigurasjon": "cannot delete the active configuration",
    "ingen tilgang: brukeren er ikke med i gruppen vifte (kjør install.sh på nytt)":
        "access denied: the user is not in the vifte group (run install.sh again)",
    # --- kommandolinje
    "Vifte – viftestyring (FanControl for Linux)": "Vifte – fan control (FanControl for Linux)",
    "bytt til (eller importer og bytt til) denne konfigurasjonen":
        "switch to (or import and switch to) this configuration",
    "ikke åpne vinduet": "do not open the window",
    "åpne vinduet": "open the window",
    "søk etter maskinvare på nytt": "rescan hardware",
    "slett også konfigurasjonene ved avinstallering": "also delete configurations when uninstalling",
    "Importert og aktivert: {}": "Imported and activated: {}",
    "Aktivert: {}": "Activated: {}",
    "Konfigurasjon: {}": "Configuration: {}",
    " (ulagret)": " (unsaved)",
    "Manglende sensorer:": "Missing sensors:",
    "Får ikke kontakt med vifted ({}): {}\nStart tjenesten: sudo systemctl start vifted":
        "Cannot reach vifted ({}): {}\nStart the service: sudo systemctl start vifted",
}

_lang = None


def settings_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "vifte", "settings.json")


def _load_settings():
    try:
        with open(settings_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def get_setting(key, default=None):
    """Brukerinnstillinger i ~/.config/vifte/settings.json (språk, oppdateringssjekk …)."""
    return _load_settings().get(key, default)


def set_setting(key, value):
    s = _load_settings()
    s[key] = value
    os.makedirs(os.path.dirname(settings_path()), exist_ok=True)
    tmp = settings_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, settings_path())


def language_setting():
    return get_setting("language", "auto")


def set_language_setting(value):
    global _lang
    set_setting("language", value)
    _lang = None


def system_language():
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var)
        if v and v not in ("C", "POSIX"):
            code = v.split(":")[0][:2].lower()
            if code in ("nb", "nn", "no"):
                return "nb"
            return code if code in CODES else "en"
    return "en"


def current():
    global _lang
    if _lang is None:
        s = os.environ.get("VIFTE_LANG") or language_setting()
        _lang = s if s in CODES else system_language()
    return _lang


def _table(code):
    if code not in _OTHER:
        try:
            mod = __import__(f"vifte.locales.{code}", fromlist=["T"])
            _OTHER[code] = mod.T
        except ImportError:
            _OTHER[code] = {}
    return _OTHER[code]


def _(text):
    lang = current()
    if lang == "nb":
        return text
    en = EN.get(text, text)
    if lang == "en":
        return en
    return _table(lang).get(en, en)
