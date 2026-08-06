[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](README.md) [![Bahasa Indonesia](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Bahasa_Indonesia-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Python-Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Lizenz: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

FL Daily Edit aktualisiert die Kader von SP Football Life 2026 und eFootball PES 2021, indem reale Transfers auf eine `EDIT00000000`-Speicherdatei angewendet werden.

## Kompatibilität

Die enthaltene Basis richtet sich an **SP Football Life 2026**. Erforderlich sind:

- Football Life 26 Update 2.2
- SmokePatch's National Squads Update

Sie ist nicht kompatibel mit UML, älteren FL26-Versionen oder Installationen ohne das Nationalmannschafts-Update. Starten Sie nach der Installation der Speicherdatei eine neue Meister-Liga- oder Werde-zur-Legende-Karriere.

Die [enthaltene Basis](base/EDIT00000000) ist das [Gondowan's Mid-Summer EDIT](https://www.reddit.com/r/SPFootballLife/comments/1v7z782/release_gondowans_midsummer_edit_file_more_than/) vom 27. Juli 2026. Sie enthält mehr als 500 Transfers, aktualisierte Gesamtbewertungen, Positionen, Trikotnummern, Leihrückkehrer, Trainer, Aufstellungen sowie Auf- und Abstiegsänderungen. Es werden weder neue Spieler erstellt noch aufgestiegene Drittliga-Vereine hinzugefügt.

## Windows-Installationsprogramm

Das Windows-Installationsprogramm ist die empfohlene Wahl für Einsteiger. Die Benutzeroberfläche des Installationsprogramms ist derzeit nur auf Englisch verfügbar. Die derzeit validierten Downloads sind **ausschließlich für Football Life 2026 Update 2.2 + SmokePatch's National Squads Update** bestimmt. Vanilla eFootball PES 2021 wird erkannt, die Installation bleibt jedoch deaktiviert, bis eine passende validierte Basis veröffentlicht wird.

1. Laden Sie [FLDailyEditInstaller.exe](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.exe) herunter.
2. Schließen Sie das Spiel.
3. Wählen Sie **Fast** oder **Deep**. Dies sind getrennte Optionen für den Aktualisierungsumfang; jede zeigt ihren Erstellungszeitpunkt.
4. Bestätigen Sie den erkannten Football-Life-2026-Ordner oder nutzen Sie bei Bedarf **Browse**.
5. Wählen Sie **Download and install**. Das Programm prüft den Download, sichert den aktuellen Spielstand und ersetzt ihn atomar.

> [!WARNING]
> Die erste ausführbare Datei ist nicht signiert, daher kann Windows SmartScreen eine Warnung anzeigen. Vergleichen Sie die heruntergeladene Datei vor dem Fortfahren mit der im [neuesten Release](https://github.com/gvoze32/fldailyedit/releases/tag/latest) veröffentlichten `FLDailyEditInstaller.exe.sha256`.

Für eine manuelle Installation ohne Installationsprogramm laden Sie das öffentliche [Fast-Release-ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip) oder [Deep-Release-ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip) herunter. Entpacken Sie `EDIT00000000`, sichern Sie Ihren aktuellen Spielstand und kopieren Sie die extrahierte Datei nach:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Für einen manuellen Durchlauf oder eine benutzerdefinierte Vereinsliste forken Sie das Repository und nutzen Sie **Run workflow** auf dem Reiter Actions.

## Was aktualisiert wird

- Transfers, Freistellungen, Leihen und Leihrückgaben
- Verfügbare Trikotnummern aus den FotMob-Kaderdaten
- Spieleridentitäten abgeglichen mit dem aktuellen FL26-Kader
- Aufstellungen und Spielpläne angepasst an Kaderänderungen
- Transferberichte und JSON-Lines-Prüfprotokolle
- Täglich vorkompilierte Speicherdateien über GitHub Actions
- Überprüfte Spieler-Erstellungen und Attributkorrekturen durch explizite Player-Update-Befehle

Der Updater überschreibt keine Trikotnummer, die bereits von einem anderen Kadermitglied verwendet wird. Zudem wird der aktuelle Verein des Spielers geprüft, bevor ein Wechsel vorgenommen wird.

## Roadmap / In Arbeit

Das GUI-Update für lokale Speicherstände ist verfügbar:

1. **Lokales Update im GUI** — der vierstufige Assistent aktualisiert jetzt
   einen vom Benutzer ausgewählten `EDIT00000000`-Speicherstand mit validiertem
   Standardlayout und **Fast**- oder **Deep**-Abdeckung. Die lokale Eignung ist
   unabhängig von der SPFL/PES/UML-Kennzeichnung des Speicherstands; der
   Speicherstand wird vor der Änderung geprüft und an Ort und Stelle gesichert
   und anschließend nur durch ein atomar verifiziertes Ergebnis ersetzt. Dies
   veröffentlicht keine neuen Remote-Assets: herunterladbare Releases bleiben
   auf validierte FL26/SPFL-Ziele beschränkt.

## Sicherheit und Einschränkungen

- Lokale Ausführungen erstellen rollierende Backups und nutzen verifizierte atomare Verschlüsselung.
- Speicherdateien werden vor und nach Kaderänderungen validiert.
- Eine Prozesssperre verhindert gleichzeitiges Schreiben zweier Instanzen in dieselbe Ausgabe.
- Unvollständige FotMob-Snapshots brechen den Lauf ab, anstatt unvollständige Dateien zu erzeugen.
- Mehrdeutige Spielerübereinstimmungen, Vereinsabweichungen und volle Zielkader werden übersprungen.
- Wikipedia, Sortitoutsi und Transfermarkt dienen als Ergänzung. Ein Ausfall einer dieser Quellen beeinträchtigt keinen vollständigen FotMob-Snapshot.
- `--allow-overflow-release` schlägt sicher fehl, da der enthaltene Katalog nicht für alle Spieler vollständige Positions- und OVR-Daten enthält.

## Lokale Ausführung

Das lokale Setup wird unter macOS, Linux und Windows über WSL unterstützt. Python 3.10 oder neuer ist erforderlich.

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
# Änderungen vorschauen, ohne eine Datei zu schreiben
python run.py run --dry-run --edit-file base/EDIT00000000

# Eine bestehende Speicherdatei validieren
python run.py validate --edit-file base/EDIT00000000

# Player Updates (eine Datei pro Spieler) gegen die unberührte Basis validieren
python run.py players validate

# Überprüfte Player Updates explizit auf eine Ausgabedatei anwenden
python run.py players apply \
  --base-revision fl26-u2.2-national-squads \
  --edit-file output/EDIT00000000 \
  --in-place

# Alle bis heute verfügbaren wirksamen Transfers anwenden
python run.py run --window auto

# Aus der mitgelieferten Basis neu erstellen
python run.py run --from-base --window auto

# Eine bestimmte Speicherdatei direkt aktualisieren
python run.py run --edit-file /path/to/EDIT00000000 --in-place

# Alle Befehlsoptionen anzeigen
python run.py run --help
```

| Befehl | Zweck |
|---|---|
| `run` | Nur verifizierte Transfers anwenden |
| `players validate` | Alle Player Updates gegen die Originalbasis validieren |
| `players apply` | Überprüfte Player Updates explizit auf einen Spielstand anwenden |
| `log` | Kürzlich angewendete Transfers anzeigen |
| `inspect` | Teams, Spieleranzahlen und Speicher-Offsets inspizieren |
| `validate` | Kaderregistrierungen und Spielplan-Zuweisungen prüfen |
| `repair` | Eine Legacy-Basis mithilfe von Referenzdateien reparieren |


`run` verarbeitet ausschließlich Transfers: es lädt oder wendet niemals Player Updates an. Um beide Workflows zu kombinieren, führen Sie zuerst den Transferbefehl auf eine Ausgabedatei aus und danach `players apply --in-place` auf dieselbe Datei.

## Player Updates

Jedes geprüfte Player Update ist eine vollständige JSON-Datei der Schema-Version 2 pro Spieler unter `players/`. Es erfasst eine `operation` (`create` oder `update`), einen Lebenszyklus (`active`, `upstreamed` oder `retired`), die genauen Basis-Revisionen in `applies_to`, die stabile Spieler-Identität und Pes-Retro-Stats-UUID/Profilherkunft, Belege und überprüfte PES-Daten. Erstellungs-Updates enthalten einen vollständigen Spielerdatensatz und Zielkader-Daten. Updates bestehender Spieler enthalten nur abweichende Werte von der geprüften Basis mit wörtlichen `from`- und `to`-Werten.
Unterstützte Update-Gruppen sind Fähigkeiten, Positionsbeherrschung, Spielstil, Spielerfähigkeiten, COM-Stile, Nationalität, physische/grundlegende Einstellungen und die registrierte Position.

### Einfacher Issue-Ablauf

1. Öffnen Sie das [Issue-Formular für Spieler-Updates](.github/ISSUE_TEMPLATE/player-update.yml). Geben Sie den `Player name` exakt so ein, wie er auf einem kanonischen `Pes Retro Stats`-Profil erscheint, fügen Sie Beleg-URLs bei und warten Sie auf das Label `generate-player-draft`.
2. Der konfigurierte Generator-Workflow ruft das Profil ab und öffnet einen Entwurfs-PR mit einem `players/<player-slug>.json`-Vorschlag.
3. Bei einer Erstellung bleiben nur spielinterne, in der Quelle nicht vorhandene Werte in `draft.missing` aufgeführt (PES-IDs, Anzeigenamen, Team-ID/Name, Nationalitäts-ID, Haut- und Augenfarbe), die von einem Mitwirkenden ergänzt werden müssen. Bei einer Aktualisierung werden nur tatsächliche Unterschiede generiert.
4. Mitwirkende und Maintainer überprüfen jeden Wert. CI akzeptiert ein Player Update nur, wenn der PR genau eine kanonische Spieler-JSON ändert und der semantische Validator erfolgreich durchläuft.
5. Das Zusammenführen (Merge) des PRs gilt als finale Freigabe.

### Direkter PR-Ablauf

Erfahrene Mitwirkende können den Entwurf überspringen und direkt einen PR mit einer vollständigen `players/<player-slug>.json`-Datei eröffnen. Fügen Sie die Herkunft, Belege, PES-Werte und Basisrevisionen ein und führen Sie vor dem Einreichen `python run.py players validate` aus.

Die Anwendung erfolgt stets über einen expliziten Befehl und erfordert die exakte Revision aus `data/base_manifest.json`.

### Revisionslebenszyklus

Wenn sich die offizielle Basis ändert, aktualisieren Sie `base/EDIT00000000` und `data/base_manifest.json` gemeinsam. Behalten Sie historische Updates in `players/`. Nach Überprüfung fügen Sie die neue Revision hinzu, markieren sie als `upstreamed` (wenn integriert) oder `retired` (wenn nicht mehr zutreffend).

Häufige `run`-Optionen:

| Option | Zweck |
|---|---|
| `--deep` | Alle lokal indexierten FotMob-Vereine abrufen |
| `--club "Chelsea,Arsenal"` | Ausführung auf ausgewählte Vereine begrenzen |
| `--window auto` | Alle datierten Transfers bis heute nachspielen |
| `--window summer` | Den letzten Zeitraum vom 1. Juni bis 30. September verwenden |
| `--window winter` | Den Januar-Februar-Zeitraum des gewählten Jahres verwenden |
| `--since YYYY-MM-DD` | Manuelle untere Datumsgrenze festlegen |
| `--dry-run` | Änderungen planen, ohne eine Datei zu schreiben |
| `--from-base` | Von `base/EDIT00000000` starten |
| `--fotmob-only` | Ohne ergänzende Transferquellen ausführen |

Ohne `--from-base` setzt ein normaler Lauf auf der letzten verifizierten Ausgabe auf.

## Transferquellen

FotMob liefert den primären Transferverlauf und Kadermetadaten. Saisonlisten von Wikipedia, freigegebene SortitoutSI-Einsendungen und datierte Transfermarkt-Einträge ergänzen oder bestätigen Wechsel. Profile von Pes Retro Stats liefern quellbasierte Vorschläge für Entwürfe.

Daten aus verschiedenen Quellen werden abgeglichen, ohne Daten, IDs oder Nachweise zu verwerfen. Unklare oder zukünftige Ereignisse können die Datei nicht eigenständig aktualisieren.

## Entwicklung

Test-Suite ausführen mit:

```bash
pytest -v
```

Die Suite deckt Speichervalidierung, Transferabgleich, Kaderplanung, Leihhistorie, Spielerabgleich, Kadergrenzen, Berichte, Backups und Prozesssperren ab.

## Lizenz

FL Daily Edit steht unter der [MIT-Lizenz](LICENSE) zur Verfügung.
