[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](../../README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Python-Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Lizenz: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../../LICENSE)

Aktualisiert `EDIT00000000`-Speicherstände von SP Football Life 2026 und
eFootball PES 2021 mit geprüften echten Transfers und überprüften
Spieler-Updates.

> **Beta:** Releases und Kompatibilität werden noch getestet.
>
> **Die Erstellung neuer Spieler ist derzeit deaktiviert.** Transfers und
> überprüfte Updates für vorhandene Spieler funktionieren weiterhin. Fehlende oder
> mehrdeutige Spieler werden übersprungen. Bei vollen Zielkadern wird standardmäßig
> ein sicherer Reservespieler nach Rolle freigestellt; mit
> `--no-allow-overflow-release` bleibt der Kader unverändert.

## Kompatibilität

Die [mitgelieferte Basis](../../base/EDIT00000000) benötigt:

- **SP Football Life 2026 Update 2.2**
- **SmokePatch's National Squads Update**

Nicht kompatibel mit UML, älteren FL26-Versionen oder Installationen ohne das
Nationalmannschafts-Update. Starten Sie nach der Installation eine neue Meister-
Liga- oder Werd-zur-Legende-Karriere.

## Windows-Installationsprogramm

Der Installer ist die einfachste Option:

1. [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip) herunterladen und entpacken.
2. Das Spiel schließen und **Fast** oder **Deep** wählen.
3. Den Football-Life-Ordner bestätigen und **Download and install** auswählen.

Der Installer prüft das Release, erstellt ein Backup und ersetzt den Speicherstand
atomar. Für einen vorhandenen Speicherstand **Update my local save** wählen,
den Speicherstand auswählen und **Apply update** anklicken.

Der Installer ist nicht signiert. Prüfen Sie `FLDailyEditInstaller.zip` vor dem
Start mit der veröffentlichten Datei `FLDailyEditInstaller.zip.sha256` im
[neuesten Release](https://github.com/gvoze32/fldailyedit/releases/tag/latest);
Windows SmartScreen kann eine Warnung anzeigen.

Für die manuelle Installation das [Fast-ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip)
oder [Deep-ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip)
herunterladen. Backup erstellen, `EDIT00000000` entpacken und kopieren nach:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Für einen manuellen Lauf oder eine eigene Vereinsliste das Repository forken und
**Run workflow** im Reiter Actions verwenden.

## Was aktualisiert wird

- Transfers, Freistellungen, Leihen und Leihrückgaben
- Trikotnummern, Aufstellungen und Spielpläne nach Kaderänderungen
- Transferberichte und Prüfprotokolle
- Täglich vorkompilierte Speicherstände über GitHub Actions

Der Updater prüft den aktuellen Verein des Spielers und überschreibt keine bereits
verwendete Trikotnummer.

Saubere PES21-Spielstände können Trikotnummern in leeren Kaderplätzen behalten.
Sie werden als nicht blockierende Warnungen gemeldet und verhindern keine lokale
Aktualisierung.

## Lokale Ausführung

Unter macOS, Linux und Windows über WSL. Python 3.10 oder neuer ist erforderlich.

```bash
git clone https://github.com/gvoze32/fldailyedit.git
cd fldailyedit

python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cd vendor/pesXdecrypter
make
cd ../..
```

## Häufige Befehle

```bash
# Preview transfers without writing a save
python run.py run --dry-run --edit-file base/EDIT00000000

# Apply all available transfers
python run.py run --window auto

# Rebuild from the bundled base
python run.py run --from-base --window auto

# Update a specific save in place
python run.py run --edit-file /path/to/EDIT00000000 --in-place

# Validate a save
python run.py validate --edit-file /path/to/EDIT00000000

# Validate Player Updates
python run.py players validate

# Apply reviewed Player Updates
python run.py players apply \
  --base-revision fl26-u2.2-national-squads \
  --edit-file /path/to/EDIT00000000 \
  --in-place

# Show command options
python run.py run --help
```

`run` verarbeitet nur Transfers. `players apply` ist ein separater Ablauf. Für
beides zuerst Transfers ausführen und danach Player Updates auf denselben
Speicherstand anwenden. Für Audit-, Vergleichs-, Protokoll- und Reparaturtools
`python run.py <command> --help` verwenden.

## Player Updates

Geprüfte Updates liegen als eine JSON-Datei pro Spieler unter `players/`.
`update`-Einträge für vorhandene Spieler können angewendet werden. `create`-
Einträge für neue Spieler dienen nur der Prüfung und werden von `players apply`
mit `create_temporarily_unavailable` abgelehnt.

So schlagen Sie ein Update vor:

1. Das [Issue-Formular für Spieler-Updates](../../.github/ISSUE_TEMPLATE/player-update.yml) öffnen.
2. Den Namen genau wie im Pes-Retro-Stats-Profil eingeben und Beleg-URLs hinzufügen.
3. Den erzeugten Entwurf prüfen, `python run.py players validate` ausführen und eine JSON-Datei einreichen.

## Sicherheit

- Speicherstände werden vor und nach Änderungen validiert.
- Lokale Läufe erstellen rollierende Backups und verwenden verifizierte atomare Verschlüsselung.
- Eine Prozesssperre verhindert gleichzeitige Schreibvorgänge in dieselbe Ausgabe.
- Unvollständige Quelldaten brechen den Lauf ab; mehrdeutige Treffer werden übersprungen.
- FotMob ist die Hauptquelle; andere Quellen ergänzen oder bestätigen sie nur.

## Entwicklung

```bash
pytest -v
```

## Lizenz

FL Daily Edit steht unter der [MIT-Lizenz](../../LICENSE).
