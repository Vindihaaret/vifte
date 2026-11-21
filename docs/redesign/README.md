# Vifte – redesign (designspesifikasjon)

Denne mappen er overleveringen fra designarbeidet i Claude (20. november 2026). Den beskriver det nye
utseendet på Vifte og hvordan det skal bygges i GTK 4 / libadwaita. **Ingen funksjoner skal endres** – bare
oppsett, farger og visuell stil.

| Fil | Hva |
|---|---|
| `prototype.html` | Klikkbar HTML-prototype med simulerte data. Åpne i en nettleser. Fasit for utseende og oppførsel. |
| `screenshots/` | Skjermbilder av prototypen, lys og mørk: oversikt, kontroller, kurver, sensorer, innstillinger. |
| `README.md` | Denne spesifikasjonen. |

Prototypen er HTML/CSS, ikke GTK. Bruk den som fasit for **hva** det skal se ut som; denne filen sier
**hvordan** det oversettes til GTK. Der de to er uenige om noe teknisk, gjelder GTK-begrensningene under.

---

## 1. Beslutninger (låst)

1. **Følger GNOME/libadwaita.** Ingen macOS-elementer (trafikklys, SF-fonter). Bare lukkeknapp i hodet, som
   standard i GNOME. Font: systemfonten (Adwaita Sans i GNOME 48+, som bygger på Inter).
2. **Navigasjon i sidefelt til venstre** i stedet for `Adw.ViewSwitcher` øverst.
   Rekkefølge: Oversikt, Kontroller, Kurver, Hastigheter, Sensorer. Nederst: statuskort og Innstillinger.
3. **Ny side: Oversikt** (første side). Fire temperaturkort øverst (CPU, GPU, Hovedkort, Lagring), stor
   historikkgraf, og nederst kortene Vifter og Beskyttelse. Se beslutning 14.
4. **Glass, men statisk:** nesten-sort (mørk) / lys gråblå (lys) bakgrunn med et svakt blått skjær øverst til
   høyre. Kort og sidefelt er halvgjennomsiktige med en tynn lys kant øverst. **Ingen uskarphet** (GTK har
   ikke `backdrop-filter`).
5. **Fargetone etter temperatur slås av som standard.** Innstillingen `tint_enabled` beholdes i menyen, men
   standardverdien endres fra `True` til `False`. Bakgrunnen endrer seg ikke med temperatur.
6. **Maks tre farger + status:**
   - Marineblå = CPU og vifter (Vifter-ruten)
   - Kobber = GPU
   - Vifte-oransje = aksent (Lagre, brytere, valgt side)
   - Statusfarger (grønn/gul/oransje/rød) brukes bare for tilstand, alltid sammen med tekst.
7. **Oversiktskortene er nøytrale.** Ingen fargenyanse i bakgrunnen. Farge bare i det lille ikonet og i
   sparkline: CPU marineblå, GPU kobber, Hovedkort og Lagring nøytral grå (`#4A505C` mørk / `#5B616C` lys,
   sparkline i `vifte_neutral`). Sikkerhetsstatusen står øverst i Beskyttelse-kortet; der skifter bare
   ikonet farge (grønn = i orden, gul = pause/nødmodus av/sensor borte, rød = nødmodus).
8. **Viftelister er nøytrale:** hvitgrå ikoner og stolper i mørk modus, nesten sort i lys. Vifter som ikke
   styres (BIOS) får svakere tone + teksten «BIOS styrer». Gjelder både Oversikt og Kontroller.
9. **Dialoger forblir rolige og ugjennomsiktige** (som i dag). Glasset gjelder hovedvinduet.
10. **Oversiktsrutene vises bare på Oversikt-siden**, ikke øverst på de andre
    sidene. Innstillingen «Vis oversikt» (`overview_enabled`) fjernes fra Innstillinger › Generelt, siden den
    ikke lenger har noen funksjon.
11. **Kurver redigeres som liste + detalj på Kurver-siden** (ikke i dialog), som i prototypen.
12. **Punktene under grafen kan redigeres direkte:** hvert punkt-merke har to tallfelt (°C og %). Endring
    oppdaterer grafen mens du skriver; Enter/Tab bekrefter. Temperaturen holdes mellom nabopunktene
    (punktene bytter ikke rekkefølge), prosent mellom 0 og 100. Klikk på et punkt i grafen markerer
    merket, og omvendt. Dette erstatter dagens «velg merke → tallfelt under grafen» (`point_selected`,
    `point_edited` i `dialogs.py`) – samme funksjon, færre klikk. I RPM-modus (`CommandMode`) vises RPM i
    stedet for %, og i Fahrenheit-modus °F.
13. **Temperaturaksen** (min/maks, i dag gruppen «Akse» i `CurveDialog`) beholdes, som to stegvelgere under
    punktene.
14. **Temperaturkortene på Oversikt** (endret 21. november):
    - **CPU** og **GPU** som før (`auto_tray_sensor("cpu"/"gpu")`).
    - **Hovedkort:** viser én hovedkortsensor. ⋯-knappen i kortet åpner en meny med alle temperatursensorer
      fra hovedkortbrikker (id-er som begynner med `/lpc/`, `/hwmon/asusec/`, `/hwmon/asus_wmi_sensors/`).
      Standard: første sensor med navn «Motherboard», ellers «SYSTIN», ellers første hovedkortsensor.
      Valget lagres i `settings.json` som `overview_board_sensor` (sensor-id).
    - **Lagring:** disker = temperatursensorer med id som begynner med `/nvme/` eller `/hdd/` (vis modellnavnet
      fra gruppetittelen, f.eks. «Samsung 990 PRO 2TB»). ⋯-menyen har tre valg: *Den varmeste disken*
      (standard; undertekst «varmest av N»), *Alle disker* (én linje per disk med navn, statusprikk og verdi)
      og *én bestemt disk*. Undertekst og linjer bruker kort modellnavn uten produsent (f.eks. «980 PRO 1TB»),
      kuttes med «…» om det ikke får plass, og hele navnet vises som verktøytips. Lagres som `overview_disk_mode` (`hottest` / `all` / `pick`) og
      `overview_disk_pick` (sensor-id).
    - Finnes ingen slik sensor, viser kortet «Ingen sensor funnet» og menyen lar deg velge en hvilken som helst
      temperatursensor.
    - Statusmerket (Rolig/Varm/Het) bruker de samme tersklene som CPU/GPU.
    - **Snitt viftehastighet** flyttes til toppen av Vifter-kortet nederst: stort tall + «snitt av styrte vifter
      · N styrt · Pumper: N» (samme tall som dagens `fan_summary`).
    - **Sikkerhet** flyttes til toppen av Beskyttelse-kortet: ikon + tekst fra `safety_state()`.
    - De to valgmenyene er nye innstillinger – et bevisst, lite unntak fra «ingen nye funksjoner».
    Velgerlogikken (standardvalg, varmeste disk) legges i `look.py` så den kan testes uten skjerm.

### Åpne spørsmål (avklares før/under fase 2)
(Ingen – alle er avklart.)

---

## 2. Fargetokens

Definer med `@define-color` (fungerer med GTK ≥ 4.12 / libadwaita ≥ 1.5, som README krever). Ikke bruk
CSS-variabler (`var()`) – de krever GTK 4.16. Ikke bruk `color-mix()` – bruk GTKs `alpha()` og `mix()`.

### Identitetsfarger

| Token | Mørk – linje/tekst | Mørk – ikonflate | Lys – linje/tekst | Lys – ikonflate |
|---|---|---|---|---|
| `vifte_cpu` (CPU + vifter) | `#7AA2D2` | `#34506E` | `#2F5A8A` | `#34506E` |
| `vifte_gpu` (GPU) | `#D29A6C` | `#9A6236` | `#9A5B26` | `#9A6236` |
| `vifte_neutral` (aktiv vifte) | `#DCDEE3` | – | `#2E3138` | – |
| `vifte_idle` (BIOS-vifte) | `alpha(#DCDEE3, 0.38)` | – | `alpha(#2E3138, 0.32)` | – |
| Aksent | `#F0A400` (tekst `#F5B83D`) | | `#C47F00` (tekst `#8F5D00`) | |

Ikonflater har hvitt ikon. Linjefarger brukes i grafer, sparklines og stolper.
CPU og GPU skilles også på lyshet (GPU-linjen er lysere i mørk modus), ikke bare kulør.

### Status (alltid med tekst)

| Tilstand | Farge | Brukes på |
|---|---|---|
| Rolig / i orden | `#2EC27E` | prikk i «Rolig»-merke, Sikkerhet-ikon, statuskort |
| Varm / pause | `#E5A50A` | «Varm»-merke, Sikkerhet-ikon ved pause eller nødmodus av |
| Het | `#E66100` | «Het»-merke |
| Nødmodus | `#E01B24` | banner, Sikkerhet-ikon, stiplet nødlinje i grafen |

Terskler som i dag (`look.py`): Rolig < 60 °C, Varm < 80 °C, Het ≥ 80 °C.

### Flater

| Token | Mørk | Lys |
|---|---|---|
| Vindusbakgrunn | `radial-gradient(75% 65% at 88% -12%, alpha(#6080BE, 0.20), transparent 70%)`, `radial-gradient(55% 45% at 0% 100%, alpha(#465A82, 0.12), transparent 70%)`, `linear-gradient(165deg, #141821, #0D1016 55%, #090B10)` | `radial-gradient(75% 65% at 88% -12%, alpha(#7896CD, 0.22), transparent 70%)`, `linear-gradient(165deg, #F2F4F8, #E8EBF0)` |
| Sidefelt | `alpha(white, 0.075)` | `alpha(white, 0.62)` |
| Kort | `alpha(white, 0.055)` | `alpha(white, 0.66)` |
| Kortskygge | `inset 0 1px alpha(white,0.09), inset 0 0 0 1px alpha(white,0.06), 0 12px 32px alpha(black,0.35)` | `inset 0 1px alpha(white,0.9), inset 0 0 0 1px alpha(white,0.55), 0 10px 28px alpha(#1E283C,0.10)` |
| Fyll (knapper, popup, merker) | `alpha(white, 0.08)` | `alpha(black, 0.055)` |
| Spor (stolper, ringer) | `alpha(white, 0.10)` | `alpha(black, 0.08)` |
| Skillelinjer | `alpha(white, 0.09)` | `alpha(black, 0.08)` |
| Sekundærtekst | `#B4B8C2` | `#55585E` |
| Primærtekst | `#F5F5F7` | `#1D1D1F` |
| Valgt i sidefelt | `alpha(#F0A400, 0.20)` | `alpha(#C47F00, 0.16)` |

Sekundærteksten er bevisst mørkere/lysere enn Adwaitas `dim-label` for å holde kontrast på
gjennomsiktige kort. **Sjekk kontrast i ekte vindu** (mål ≥ 4.5:1 for brødtekst).

**Høykontrast:** når `Adw.StyleManager.get_high_contrast()` er sann, skal glasset av: ugjennomsiktige
Adwaita-kort, ingen bakgrunnsgradient.

---

## 3. Mål og typografi

| Element | Verdi |
|---|---|
| Sidefelt-bredde | 232 px |
| Sidehode | 72 px høyt; tittel 26 px / 800 / -0.6 px; undertittel 13 px sekundær |
| Innhold | padding 2 px 28 px 28 px; 16 px mellom seksjoner, 14 px mellom kort |
| Radius | kort 16 px, lister (inset) 14 px, ikonflate 7 px (22–24 px store), knapper 8–9 px, merker 99 px |
| Store tall | 30 px / 700 / -0.8 px, tabulære sifre (`font-feature-settings: "tnum"`) |
| Enhet etter tall (°C, %) | 16 px / 600 / sekundær |
| Gruppeoverskrift | 14 px / 700 med ikonflate 22 px foran og dra-håndtak |
| Listerad | min. 56 px høy; navn 14 px / 600, undertekst 12 px sekundær |
| Brytere | Adwaitas `Gtk.Switch` (aksentfarge) |
| Stolper | 6 px høye, helt runde (finnes allerede: `.vifte-card levelbar`) |

---

## 4. Sidene

Se `screenshots/` for hver side i lys og mørk.

**Sidefelt** – øverst: app-ikon + «Vifte / FanControl for Linux», så «Konfigurasjon» med dagens
konfigurasjonsvelger (flyttes fra hodelinjen, beholder «•» ved ulagrede endringer). Navigasjon med
symbolske ikoner. Nederst: statuskort (fra `safety_state()`: ikon + «Alt i orden / Styring på pause /
Nødmodus / Sensor borte / Nødmodus av» + «Tjenesten styrer N vifter»), klikk åpner Innstillinger › Sikkerhet.
Til slutt «Innstillinger».

**Sidehode (alle sider)** – tittel + undertittel til venstre; til høyre: Pause (ikon), meny (⋯, samme
innhold som dagens `Gio.Menu`), Lagre (aksentfarge når `dirty`), lukk.

**Oversikt**
- Fire temperaturkort: CPU, GPU, Hovedkort, Lagring (se beslutning 14). Hvert kort: ikonflate + navn,
  statusmerke (Rolig/Varm/Het), stort tall, sparkline (eksisterende `Sparkline`), undertekst med sensornavn.
  Hovedkort og Lagring har en ⋯-knapp for valg av sensor/visning.
- Stort graf-kort: «Temperatur · siste 30 minutter», CPU og GPU fra `history` (finnes i daemon, 360 punkter),
  stiplet nødlinje når nødmodus er på og grensen er innenfor aksen. Ny `Gtk.DrawingArea`-klasse i `graph.py`.
- Nederst to kort: «Vifter» (øverst snitt % for styrte vifter, så nøytral liste, lenke til Kontroller) og
  «Beskyttelse» (øverst sikkerhetsstatus med farget ikon, så brytere for nødmodus og sikring ved sensortap –
  samme innstillinger som i Innstillinger – og Pause/Fortsett, lenke til Innstillinger).
  Oppsummeringsraden øverst i begge kortene (snitt % / sikkerhetsstatus) har lik fast høyde (54 px) med
  skillelinje under, så linjene står på samme høyde. Sikkerhetsikonet er 24 px, som ikonflatene ellers.

**Kontroller** – dagens kort blir grupperte lister (`Adw.PreferencesGroup`-lignende inset-lister) per maskinvare.
Rad: nøytralt vifteikon, navn + RPM/«BIOS styrer», kurvevelger, prosent, stolpe, bryter, tannhjul
(åpner dagens `ControlDialog`). Gruppene kan fortsatt dras (dagens `make_group_movable`).

**Kurver** – liste til venstre (navn, type, minigraf, nåverdi, søk), detalj til høyre: navn, nåverdi og hvilke
vifter som bruker kurven, segmentert valg av type (Graf, Lineær, Flat, Miks, Trigger, Synk, Auto), skjema for
typen, og for Graf den store `CurveGraph` (klikk legger til, dra flytter, høyreklikk fjerner – som i dag).

**Hastigheter / Sensorer** – grupperte lister, rad: navn, sparkline i gruppens identitetsfarge, verdi,
pil til detaljer. Statusmerke vises bare når sensoren er Varm/Het.

**Innstillinger** – beholder `Adw.PreferencesDialog` med sidene Generelt, Sikkerhet, Claude og dagens innhold.
Endring: «Farge følger temperaturen» er av som standard.

---

## 5. Hvor i koden

| Område | Fil / funksjon i dag | Endring |
|---|---|---|
| CSS | `vifte/gui/app.py`, konstanten `CSS` | Erstatt med tokens og klasser fra seksjon 2–3. Vurder å flytte til `vifte/gui/style.css`. |
| Lys/mørk | `apply_style_mode()` (klassen `vifte-light`) | Behold mønsteret; legg til høykontrast-sjekk. |
| Fargetone | `update_tint()`, `set_tint()`, `get_setting("tint_enabled", True)` | Standard `False` (to steder: `update_tint` og innstillingsraden ca. linje 1450). |
| Navigasjon | `MainWindow.__init__`: `Adw.ViewStack` + `Adw.ViewSwitcher` + `Adw.ViewSwitcherBar` + breakpoint | `Adw.NavigationSplitView` med `Gtk.ListBox` (`navigation-sidebar`) i sidefeltet; behold `ViewStack` som innhold. Sidefeltet kollapser på smal skjerm (breakpoint). |
| Oversikt | `build_overview()`, `update_overview()`, `Tile`, `set_overview()`, `auto_tray_sensor()` | Bygg Oversikt-side; gjenbruk logikken i `update_overview()` og `look.py`. `tile_fans`/`tile_safety` erstattes av Hovedkort og Lagring; snitt og sikkerhet flyttes til kortene nederst. Nye velgere i `look.py` (testes). Fjern rutene fra de andre sidene og innstillingen `overview_enabled`. |
| Grafer | `graph.py`: `Sparkline`, `CurveGraph`, `_color()` | Sparkline får fargeparameter (cpu/gpu/neutral) i stedet for alltid `accent_color`. Ny graf for CPU+GPU-historikk. |
| Kontroller | `build_controls()` | Kort → listerader. |
| Kurver | `build_curves()`, `CurveDialog` (`dialogs.py`) | Liste + detalj; gjenbruk byggefunksjonene i `CurveDialog` (`build_graph`, `build_linear` …). Punkt-merkene blir redigerbare (`Gtk.SpinButton` eller `Gtk.Entry` med validering i hvert merke) – se beslutning 12. |
| Sensorer/Hastigheter | `build_sensors()`, `build_speeds()`, `value_card()`, `add_history()` | Kort → listerader med sparkline. |
| Tekster | `i18n.py`, `locales/*.py` | Nye strenger må inn i alle fem språk (nb, en, de, fr, es). |

---

## 6. Plan i faser

Hver fase skal kunne slås sammen alene, med grønne tester (`tests/test_vifte.py`, CI i `.github/workflows/ci.yml`).

1. **Tokens og glass:** ny CSS, statisk bakgrunn, nøytrale kort, `tint_enabled` av som standard, høykontrast.
   Ingen endring i oppsett. Gir mest synlig effekt med minst risiko.
2. **Sidefelt-navigasjon** med `Adw.NavigationSplitView`, statuskort, konfigurasjonsvelger i sidefeltet.
3. **Oversikt-siden** med de fire kortene, stor historikkgraf og de to kortene nederst.
4. **Listerader** for Kontroller, Sensorer og Hastigheter; identitetsfarger i ikoner og sparklines.
5. **Kurver som liste + detalj** med redigerbare punkt-merker og temperaturakse (størst endring – gjøres sist;
   dialogen fungerer i mellomtiden).

Akseptkriterier for hver fase:
- Alle funksjoner virker som før (lagre, konfigurasjonsbytte, pause, nødmodus, sensortap, varsler, Claude/MCP,
  dra grupper, skjulte kort, import/eksport).
- Ser riktig ut i lys, mørk og høykontrast, og i et smalt vindu.
- Nye tekster finnes på alle fem språk.
- Skjermbildene i `docs/screenshots/` oppdateres til slutt.

---

## 7. Startmelding til Claude Code

Lim inn dette når du starter `claude` i repoet:

> Les `docs/redesign/README.md` og se på skjermbildene i `docs/redesign/screenshots/`. Prototypen
> `docs/redesign/prototype.html` er fasit for utseendet. Vi skal bygge redesignet i GTK 4 / libadwaita uten å
> endre funksjoner. Start med fase 1 (tokens og glass) på en egen branch. Les først `vifte/gui/app.py`
> (konstanten `CSS`, `apply_style_mode`, `update_tint`) og `vifte/gui/graph.py`. Vis meg planen for fase 1
> før du endrer kode, og kjør testene etterpå.
