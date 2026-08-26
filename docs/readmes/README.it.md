[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](../../README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Versione Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Licenza: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../../LICENSE)

Aggiorna i file `EDIT00000000` di SP Football Life 2026 ed eFootball PES 2021
con trasferimenti reali verificati e aggiornamenti dei giocatori revisionati.

> **Beta:** release e compatibilità dei salvataggi sono ancora in fase di test.
>
> **La creazione di nuovi giocatori è disattivata per ora.** Sono supportati i
> trasferimenti e gli aggiornamenti revisionati dei giocatori già presenti. I
> giocatori mancanti o ambigui vengono ignorati. Se la rosa di destinazione è
> piena, viene svincolata una riserva sicura in base al ruolo; usa
> `--no-allow-overflow-release` per lasciare la rosa invariata.

## Compatibilità

La [base inclusa](../../base/EDIT00000000) richiede:

- **SP Football Life 2026 Update 2.2**
- **SmokePatch's National Squads Update**

Non è compatibile con UML, versioni precedenti di FL26 o installazioni senza
l’aggiornamento delle squadre nazionali. Inizia una nuova carriera di Master
League o Diventa una leggenda dopo l’installazione.

## Programma di installazione Windows

Il programma di installazione è l’opzione più semplice:

1. Scarica ed estrai [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip).
2. Chiudi il gioco e scegli **Fast** o **Deep**.
3. Conferma la cartella di Football Life e seleziona **Download and install**.

Il programma verifica la release, crea un backup e sostituisce il salvataggio in
modo atomico. Per aggiornare un file esistente, scegli **Update my local save**,
selezionalo e fai clic su **Apply update**.

Il programma non è firmato. Verifica `FLDailyEditInstaller.zip` con il file
`FLDailyEditInstaller.zip.sha256` pubblicato nell’[ultima release](https://github.com/gvoze32/fldailyedit/releases/tag/latest)
prima di eseguirlo; Windows SmartScreen potrebbe mostrare un avviso.

Per l’installazione manuale, scarica lo [ZIP Fast](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip)
o lo [ZIP Deep](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip).
Crea un backup, estrai `EDIT00000000` e copialo in:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Per un’esecuzione su richiesta o un elenco di club personalizzato, fai un fork
del repository e usa **Run workflow** nella scheda Actions.

## Cosa viene aggiornato

- Trasferimenti, svincoli, prestiti e rientri dai prestiti
- Numeri di maglia, formazioni e piani di gioco modificati dai cambi di rosa
- Report dei trasferimenti e log di audit
- Salvataggi precompilati ogni giorno tramite GitHub Actions

L’aggiornamento controlla il club attuale del giocatore e non sovrascrive un
numero di maglia già usato.

## Esecuzione locale

Compatibile con macOS, Linux e Windows tramite WSL. È richiesto Python 3.10 o
successivo.

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

## Comandi comuni

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

`run` applica solo i trasferimenti. `players apply` è un flusso separato. Per
usare entrambi, esegui prima i trasferimenti e poi applica i Player Updates allo
stesso file. Usa `python run.py <command> --help` per audit, confronto, log e
riparazione.

## Aggiornamenti giocatori

Gli aggiornamenti revisionati sono salvati in `players/`, con un file JSON per
giocatore. I record `update` dei giocatori esistenti possono essere applicati. I
record `create` per nuovi giocatori servono solo per la revisione e vengono
rifiutati da `players apply` con `create_temporarily_unavailable`.

Per proporre un aggiornamento:

1. Apri il [modulo issue per gli aggiornamenti dei giocatori](../../.github/ISSUE_TEMPLATE/player-update.yml).
2. Inserisci il nome esattamente come appare nel profilo Pes Retro Stats e aggiungi URL di prova.
3. Controlla la bozza, esegui `python run.py players validate` e invia un solo file JSON del giocatore.

## Sicurezza

- I salvataggi vengono convalidati prima e dopo le modifiche.
- Le esecuzioni locali creano backup rotativi e usano una crittografia atomica verificata.
- Un blocco impedisce scritture simultanee nella stessa destinazione.
- Dati incompleti interrompono l’esecuzione; le corrispondenze ambigue vengono ignorate.
- FotMob è la fonte principale; le altre fonti la integrano o la confermano.

## Sviluppo

```bash
pytest -v
```

## Licenza

FL Daily Edit è distribuito con la [Licenza MIT](../../LICENSE).
