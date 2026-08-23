[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](../../README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Python-Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Lizenz: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../../LICENSE)

FL Daily Edit aktualisiert Kader in SP Football Life 2026 und eFootball PES 2021, indem reale Transfers auf eine `EDIT00000000`-Speicherdatei angewendet werden.

> **Die Erstellung geprüfter neuer Spieler ist in `players apply` standardmäßig
> aktiviert, wenn ein gültiger `PlayerAppearance.bin`-Donor vorhanden ist. Der
> positive Schalter ist `--allow-create`; mit `--no-allow-create` wird die CLI-
> Erstellung deaktiviert. Direkte API-Aufrufe bleiben standardmäßig deaktiviert.**
>
> Transfers für vorhandene Spieler und geprüfte Aktualisierungen bestehender Spieler
> werden weiterhin unterstützt. Fehlende Spieler werden übersprungen. Rollenbasiertes
> Overflow-Freistellen ist standardmäßig aktiv; `--no-allow-overflow-release` lässt
> einen vollen Zielkader unverändert.

> [!WARNING]
> **Beta-Hinweis:** FL Daily Edit, seine Repository-Daten und die erzeugten Releases werden noch getestet. Sie funktionieren möglicherweise noch nicht mit jeder Spiel-/Speicherstand-Konfiguration; einige Bedingungen werden noch nicht unterstützt.

## Kompatibilität

Die mitgelieferte Basis ist für **SP Football Life 2026** ausgelegt. Voraussetzungen:

- Football Life 26 Update 2.2
- SmokePatch's National Squads Update

Nicht kompatibel mit UML, älteren FL26-Versionen oder Installationen ohne das Nationalmannschafts-Update. Starten Sie nach der Installation des Speicherstands eine neue Meister-Liga- oder Werd-zur-Legende-Karriere.

Die [mitgelieferte Basis](../../base/EDIT00000000) ist [Gondowan's EDIT vom 22. August 2026](https://www.reddit.com/r/SPFootballLife/comments/1vvh129/release_gondowans_edit_file_22082026_latest/). Sie enthält Last-Minute-Transfers zum 22.08.2026 für alle Ligen, Bewertungsänderungen für mehr als 600 Spieler, Auf- und Absteiger zwischen erster und zweiter Liga, Korrekturen an Größe und Position, Namens- und Trikotnummern-Updates, Änderungen verfügbarer Trainer sowie automatische, nach den besten Spielern sortierte Aufstellungen. Sie erstellt keine neuen Spieler und fügt keine Drittliga-Aufsteiger hinzu.

## Windows-Installationsprogramm

Das Windows-Installationsprogramm ist die empfohlene Option für Einsteiger. Die Benutzeroberfläche des Installationsprogramms ist derzeit nur auf Englisch verfügbar. Die aktuellen validierten Downloads gelten **ausschließlich für Football Life 2026 Update 2.2 + SmokePatch's National Squads Update**. Vanilla eFootball PES 2021 wird erkannt, die Installation bleibt jedoch deaktiviert, bis eine passende validierte Basis veröffentlicht wird.

1. Laden Sie die [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip) herunter und entpacken Sie sie.
2. Schließen Sie das Spiel.
3. Wählen Sie **Fast** oder **Deep**. Dies sind getrennte Optionen für den Aktualisierungsumfang; jede zeigt den Generierungszeitpunkt an.
4. Bestätigen Sie den erkannten Ordner von Football Life 2026 oder nutzen Sie bei Bedarf **Browse**.
5. Wählen Sie **Download and install**. Der Installer verifiziert den Download, sichert den aktuellen Speicherstand und ersetzt ihn atomar.

**Vorhandenen Speicherstand über die GUI aktualisieren:** Der Installer kann
auch eine vom Benutzer ausgewählte `EDIT00000000` im Standardlayout aktualisieren,
anstatt einen vorgefertigten Build zu installieren. Wählen Sie **Update my local save**,
wählen Sie einen erkannten Speicherort oder nutzen Sie **Browse**, wählen Sie
**Fast** oder **Deep** und nach Prüfung **Apply update**. Der Assistent validiert
den Speicherstand vor Änderungen, erstellt ein Backup am selben Ort und zeigt
Fortschritt, Ergebnis oder Diagnosen an. Die lokale Eignung hängt nicht vom
SPFL/PES/UML-Label ab; dieser Weg lädt keinen entfernten vorgefertigten Build
herunter. Wenn diese optionalen externen SPFL-Kataloge nicht verfügbar sind,
greift der lokale Abgleich auf die im ausgewählten Speicherstand eingebetteten
Spieler- und Teamnamen zurück, sodass der gebündelte lokale Aktualisierungspfad
auch ohne sie funktioniert.

> [!WARNING]
> Die ausführbare Installationsdatei ist nicht signiert, daher kann Windows SmartScreen beim Starten eine Warnung anzeigen. Vergleichen Sie vor dem Fortfahren die heruntergeladene `FLDailyEditInstaller.zip` mit der im [neuesten Release](https://github.com/gvoze32/fldailyedit/releases/tag/latest) veröffentlichten `FLDailyEditInstaller.zip.sha256`.
> Falls Windows das Installationsprogramm über die Smart-App-Control blockiert, öffnen Sie **Settings → Privacy & security → Windows Security → App & browser control → Smart App Control settings** und schalten Sie auf **Off**. Alternativ klicken Sie mit der rechten Maustaste auf die heruntergeladene Datei, öffnen Sie die **Properties** und aktivieren Sie **Unblock**, falls verfügbar.

Für eine manuelle Installation ohne Installer laden Sie die öffentliche [Fast-Release-ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip) oder [Deep-Release-ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip) herunter. Entpacken Sie `EDIT00000000`, sichern Sie Ihren aktuellen Speicherstand und kopieren Sie die Datei nach:

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

## Roadmap / Vorerst abgeschlossen

Alle aktuellen Roadmap-Aufgaben sind abgeschlossen. Wir warten auf die nächste sinnvolle Idee.

## Sicherheit und Einschränkungen

- Lokale Ausführungen erstellen rollierende Backups und nutzen verifizierte atomare Verschlüsselung.
- Speicherdateien werden vor und nach Kaderänderungen validiert.
- Eine Prozesssperre verhindert gleichzeitiges Schreiben zweier Instanzen in dieselbe Ausgabe.
- Unvollständige FotMob-Snapshots brechen den Lauf ab, anstatt unvollständige Dateien zu erzeugen.
- Mehrdeutige Spielerübereinstimmungen und Vereinsabweichungen werden übersprungen.
- Volle Zielkader verwenden standardmäßig rollenbasiertes Overflow-Freistellen.
  Stammspieler und Spieltagsbank werden geschützt, die tiefste native Reserve wird
  bevorzugt, und erstellte Spieler werden geschützt, wenn ein nativer Kandidat
  existiert. Ability/OVR wird nie verwendet; deaktivieren mit
  `--no-allow-overflow-release`.
- Wikipedia, Sortitoutsi und Transfermarkt dienen als Ergänzung. Ein Ausfall einer dieser Quellen beeinträchtigt keinen vollständigen FotMob-Snapshot.

**Transfer-Updates vs. Player Updates**

Dies sind getrennte Arbeitsabläufe:

- `run` verarbeitet Transfers für Spieler, die sich bereits im Speicherstand befinden. Ist ein
  Zielverein voll, wird der rollenbasierte Overflow-Kandidat standardmäßig freigestellt;
  mit `--no-allow-overflow-release` wird der Transfer übersprungen.
- `players apply` wendet geprüfte Attributänderungen an. `update`-Spezifikationen für bestehende
  Spieler werden unterstützt.
- `create`-Spezifikationen für neue Spieler bleiben ladbar und überprüfbar. `players apply`
  versucht sie standardmäßig bei vorhandenem explizitem Donor und gültiger
  `PlayerAppearance.bin`-Quelle. Mit `--no-allow-create` werden sie deaktiviert; fehlende oder
  ungültige Donor-Daten lassen den Speicherstand unverändert.

- Die Fast- und Deep-Synchronisierungs-Workflows verwenden vorübergehend `--no-allow-create` und `--no-allow-overflow-release`; die automatische Synchronisierung wendet keine `create`-Spezifikationen an. Der lokale Befehl `players apply` behält das normale Verhalten bei.
- Für `players apply` nutzt ein voller Zielkader standardmäßig rollenbasiertes Overflow.
  Der positive Schalter ist `--allow-overflow-release`; `--no-allow-overflow-release` deaktiviert es.

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
| `base-audit` | Aktive Player Updates, Ziele und Loan-Parent gegen eine Basis prüfen |
| `base-refresh` | Eine lokale oder HTTPS-Basis prüfen und optional übernehmen |
| `usage-import` | Offline-Nutzungsdaten aus CSV in die Release Policy übernehmen |
| `players apply --preflight` | Geprüfte create-Ziele und Sicherheitsdaten ohne Schreibvorgang anzeigen |
| `log` | Kürzlich angewendete Transfers anzeigen |
| `inspect` | Teams, Spieleranzahlen und Speicher-Offsets inspizieren |
| `validate` | Kaderregistrierungen und Spielplan-Zuweisungen prüfen |
| `repair` | Eine Legacy-Basis mithilfe von Referenzdateien reparieren |
| `audit` | Speicherstand und native Metadaten schreibgeschützt prüfen |
| `compare` | Zwei native CPK-Metadatenvarianten schreibgeschützt vergleichen |

`run` verarbeitet ausschließlich Transfers: es lädt oder wendet niemals Player Updates an. Um beide Workflows zu kombinieren, führen Sie zuerst den Transferbefehl auf eine Ausgabedatei aus und danach `players apply --in-place` auf dieselbe Datei.

## Player Updates

Jedes geprüfte Player Update ist eine vollständige JSON-Datei der Schema-Version 2 pro Spieler unter `players/`. Es erfasst eine `operation` (`create` oder `update`), einen Lebenszyklus (`active`, `integrated` oder `superseded`), die genauen Basis-Revisionen in `applies_to`, die stabile Spieler-Identität und Pes-Retro-Stats-UUID/Profilherkunft, Belege und überprüfte PES-Daten. Erstellungs-Updates enthalten einen vollständigen Spielerdatensatz und Zielkader-Daten. Updates bestehender Spieler enthalten nur abweichende Werte von der geprüften Basis mit wörtlichen `from`- und `to`-Werten.

> **Hinweis zum Lebenszyklus:** `superseded` ist ein Status des Player Updates, nicht der Spielerkarriere. Er bedeutet, dass das Update nicht mehr für die ausgewählte Basisrevision gilt.
`create`-Einträge bleiben zur Prüfung schemaunterstützt. Eine Mutation über die CLI erfordert
`players apply --allow-create` und gültige Aussehensdaten; direkte API-Aufrufe bleiben
standardmäßig deaktiviert. Bei vollem Zielkader ist zusätzlich `--allow-overflow-release`
erforderlich; fehlende oder ungültige Sicherheitsmetadaten lassen den Speicherstand unverändert.
Unterstützte Update-Gruppen sind Fähigkeiten, Positionsbeherrschung, Spielstil, Spielerfähigkeiten, COM-Stile, Nationalität, physische/grundlegende Einstellungen und die registrierte Position.
- Die generierten OVR-Prüfwerte sind deterministische Berechnungen basierend auf der veröffentlichten PES 2021-Formel. Sie dienen als Paritätshilfe und stellen keine unabhängige Garantie für die Laufzeit des Spiels dar; vorgeschlagene Fähigkeitswerte müssen weiterhin überprüft werden.
- Spielerentwürfe, die mit der früheren OVR-Modellkennung generiert wurden, müssen vor der Validierung neu generiert werden; es gibt keine implizite Migration von v1 auf v2.

### Einfacher Issue-Ablauf

1. Öffnen Sie das [Issue-Formular für Spieler-Updates](../../.github/ISSUE_TEMPLATE/player-update.yml). Geben Sie den `Player name` exakt so ein, wie er auf einem kanonischen `Pes Retro Stats profile` erscheint, fügen Sie Beleg-URLs bei und warten Sie auf das Label `generate-player-draft`.
2. Der konfigurierte Generator-Workflow ruft das Profil ab und öffnet einen Entwurfs-PR mit einem `players/<player-slug>.json`-Vorschlag nach Schema-Version 2. Er leitet den Quell-Snapshot, Identität, physische Einstellungen, Positionsdaten, Fähigkeiten, Spielstil, Spielerfähigkeiten und COM-Stile aus dem Profil ab.
3. Bei einer Erstellung bleiben nur spielinterne, in der Quelle nicht vorhandene Werte in `draft.missing` aufgeführt: PES-IDs und Drucknamen für Identität und Spieler, Team-ID und Name, Nationalitäts-ID, Hautfarbe und Irisfarbe. Ein Mitwirkender oder Maintainer muss diese bereitstellen. Bei einer Aktualisierung lokalisiert der Generator den Spieler in der verifizierten Basis und gibt nur tatsächliche `from`/`to`-Unterschiede aus. Eine von PES 2021 nicht unterstützte Quellposition wie `RWB` wird ausgelassen statt neu zugeordnet, einschließlich bei Änderungen der registrierten Position.
4. Ein Mitwirkender und ein Maintainer prüfen jeden generierten Wert als unbestätigten Vorschlag. CI akzeptiert ein Player Update nur, wenn der PR genau einen kanonischen Spieler-JSON-Pfad hinzufügt oder ändert und der gemeinsame semantische Validator erfolgreich durchläuft.
5. Das Zusammenführen (Merge) des PRs gilt als finale Freigabe. Es gibt kein separates `approved`-Flag in der JSON-Datei.

Jeder generierte Vorschlag fällt erwartungsgemäß bei der vollständigen Dateivalidierung durch. Um die generierten Belege in das vollständige Schema v2 zu konvertieren, entfernen Sie die reinen Entwurfsfelder `evidence.current_team`, `evidence.issue_number` und `evidence.issue_url`; behalten Sie die kanonische `evidence.profile_url`, die geprüften `evidence.proof_urls` und das exakte `evidence.effective_date` bei; und fügen Sie ein geprüftes, nicht leeres `evidence.reason` hinzu. Speichern Sie die kanonische Profil-UUID als `identity.pes_retro_stats_id` und nur die geprüften Gameplay-Werte in `pes`. Bei einer Erstellung füllen Sie zusätzlich jedes in `draft.missing` genannte lokale Spielfeld aus. PES-IDs erstellter Spieler müssen eindeutig sein und mindestens `0x100000` (1.048.576) betragen; der Vorschlags-Allokator bleibt in diesem reservierten Bereich.
Entfernen Sie vor der vollständigen Validierung die Top-Level-Objekte `source` und `draft`, bei denen es sich um reine Entwurfsmetadaten zur Überprüfung handelt.

### Direkter PR-Ablauf

Erfahrene Mitwirkende können den Entwurf überspringen und direkt einen PR mit einer vollständigen `players/<player-slug>.json`-Datei eröffnen. Fügen Sie die kanonische UUID/Profilherkunft in `identity` und `evidence`, zitierte Belege, geprüfte PES-Werte, erwartete Update-Baselines, den Lebenszyklus und die exakte Basisrevision ein und führen Sie vor dem Einreichen `python run.py players validate` aus. Fügen Sie keine generierten Top-Level-Metadaten `source` oder `draft` hinzu. Halten Sie andere Code- oder Dokumentationsänderungen aus diesem PR heraus.

Die Anwendung erfolgt stets über einen expliziten Befehl und erfordert die exakte Revision aus `data/base_manifest.json`; eine Revisionsabweichung schlägt vor dem Entschlüsseln des Zielspeicherstands fehl.

### Revisionslebenszyklus

Wenn sich die offizielle Basis ändert, aktualisieren Sie `base/EDIT00000000` und `data/base_manifest.json` gemeinsam. Behalten Sie historische Player Updates in `players/`; löschen Sie sie nicht nur wegen einer Revisionsänderung. Ein aktives Player Update, dessen `applies_to`-Liste die neue Revision nicht enthält, wird inaktiv: Die Validierung meldet `needs_review` und das Anwenden überspringt es. Nach Überprüfung fügen Sie die neue Revision nur hinzu, wenn das Player Update weiterhin zutrifft, markieren es als `integrated`, wenn die offizielle Basis die Änderung enthält, oder als `superseded`, wenn es nicht mehr zutrifft.

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
| `--release-policy PATH` | Geschützte Spieler pro Verein und Offline-Nutzungszähler laden |
| `--numbers-only` | Aktuelle Rückennummern nur mit FotMob-Kaderdaten korrigieren |

Ohne `--from-base` setzt ein normaler Lauf auf der letzten verifizierten Ausgabe auf. Dies verhindert das Verschwinden angewendeter Transfers, wenn ein späterer geplanter Lauf den akkumulierten Verlauf erneut liest.

## Transferquellen

FotMob liefert den primären Transferverlauf und Kadermetadaten. Saisonlisten von Wikipedia, freigegebene SortitoutSI-Einsendungen und datierte Transfermarkt-Einträge ergänzen oder bestätigen Wechsel. Profile von Pes Retro Stats liefern quellbasierte Vorschläge für Entwürfe.

Daten aus verschiedenen Quellen werden abgeglichen, ohne Daten, IDs oder Nachweise zu verwerfen. Undatierte, zukünftig wirksame, widersprüchliche oder unklare Ereignisse können die Datei nicht eigenständig aktualisieren.

Der Spielerabgleich beginnt beim Kader des abgebenden Vereins und nutzt den Zielkader als idempotenten Fallback. Position, Nationalität und Alter werden nur berücksichtigt, wenn diese Informationen verfügbar sind.

## Entwicklung

Test-Suite ausführen mit:

```bash
pytest -v
```

Die Suite deckt Speichervalidierung, Transferabgleich, Kaderplanung, Leihhistorie, Spielerabgleich, Kadergrenzen, Berichte, Backups und Prozesssperren ab.

## Lizenz

FL Daily Edit steht unter der [MIT-Lizenz](../../LICENSE) zur Verfügung.
