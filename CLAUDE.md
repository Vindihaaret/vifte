# Vifte – notater for Claude Code

Vifte er en Linux-app for viftestyring (GTK 4 / libadwaita, Python) som leser og skriver FanControl-konfigurasjoner.

## Pågående arbeid: redesign (branch `redesign`)

Designspesifikasjonen ligger i `docs/redesign/README.md`. Les den før du endrer GUI-kode.
- `docs/redesign/prototype.html` er fasit for utseendet (åpnes i nettleser, simulerte data).
- `docs/redesign/screenshots/` viser alle sidene i lys og mørk modus.
- Arbeidet gjøres i fasene i seksjon 6 i spesifikasjonen, én fase om gangen. Vis planen for en fase før du endrer kode.
- **Ingen funksjoner skal endres** – bare oppsett, farger og visuell stil. Unntakene står som beslutninger i spesifikasjonen.

## Konvensjoner
- Teksten i koden og kommentarer er på norsk. Brukerstrenger går gjennom `_()`.
- Nye strenger: engelsk oversettelse i `EN` i `vifte/i18n.py`, og tysk/fransk/spansk i `vifte/locales/{de,fr,es}.py`
  (nøkkel er den engelske teksten). Alle fem språk må med.
- GUI-kode ligger i `vifte/gui/` (`app.py` hovedvindu og CSS, `dialogs.py`, `graph.py`, `common.py`). Logikk uten GTK
  ligger i `vifte/gui/look.py` og testes uten skjerm.
- Krav: GTK ≥ 4.12 og libadwaita ≥ 1.5. Bruk derfor `@define-color` og `alpha()`/`mix()` i CSS, ikke `var()` eller `color-mix()`.

## Tester
```bash
python3 -m unittest discover -s tests -v
```
CI kjører det samme (`.github/workflows/ci.yml`).
