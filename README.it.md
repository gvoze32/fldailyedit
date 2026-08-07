[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](README.md) [![Bahasa Indonesia](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Bahasa_Indonesia-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

FL Daily Edit aggiorna le rose di SP Football Life 2026 ed eFootball PES 2021
applicando i trasferimenti reali a un file di salvataggio `EDIT00000000`.

## Compatibilità

La base inclusa è destinata a **SP Football Life 2026**. Richiede:

- Football Life 26 Update 2.2
- SmokePatch's National Squads Update

Non è compatibile con UML, con le versioni precedenti di FL26 né con le
installazioni prive dell'aggiornamento delle nazionali. Dopo aver installato il
salvataggio, avvia una nuova carriera in Master League o Diventa un Mito.

La [base inclusa](base/EDIT00000000) è
[Gondowan's Mid-Summer EDIT](https://www.reddit.com/r/SPFootballLife/comments/1v7z782/release_gondowans_midsummer_edit_file_more_than/),
datata 27 luglio 2026. Include oltre 500 trasferimenti, valutazioni aggiornate,
ruoli, numeri di maglia, rientri dai prestiti, allenatori, formazioni e modifiche
a promozioni o retrocessioni. Non crea giocatori e non aggiunge club promossi
dalle terze divisioni.

## Programma di installazione per Windows

Il programma di installazione per Windows è l'opzione consigliata ai principianti. L'interfaccia del programma di installazione è attualmente disponibile solo in inglese. I download attualmente convalidati sono destinati **esclusivamente a Football Life 2026 Update 2.2 + SmokePatch's National Squads Update**. Il rilevamento di eFootball PES 2021 vanilla è disponibile, ma l'installazione rimane disabilitata finché non viene pubblicata una base convalidata corrispondente.

1. Scarica [FLDailyEditInstaller.exe](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.exe).
2. Chiudi il gioco.
3. Scegli **Fast** o **Deep**. Sono opzioni separate per la copertura dell'aggiornamento e ciascuna mostra l'ora di generazione.
4. Conferma la cartella Football Life 2026 rilevata oppure usa **Browse**, se necessario.
5. Seleziona **Download and install**. Il programma verifica il download, crea un backup del salvataggio corrente e lo sostituisce in modo atomico.

**Aggiornare un salvataggio esistente tramite la GUI:** il programma di
installazione può anche aggiornare un `EDIT00000000` con layout comune scelto
dall'utente, invece di installare una versione precompilata. Seleziona **Update
my local save**, scegli una posizione rilevata oppure usa **Browse**, scegli
**Fast** o **Deep** e, dopo la revisione, seleziona **Apply update**. La procedura
verifica il salvataggio prima di modificarlo, crea un backup sul posto e mostra
progresso, risultato o diagnostica. L'idoneità locale non dipende dall'etichetta
SPFL/PES/UML e questo percorso non scarica una versione precompilata remota.
Quando questi cataloghi SPFL esterni opzionali non sono disponibili, il
meccanismo di corrispondenza locale ricorre ai nomi di giocatori e squadre
incorporati nel salvataggio selezionato, consentendo l'esecuzione della procedura
di aggiornamento locale integrata anche senza di essi.


> [!WARNING]
> L'eseguibile iniziale non è firmato, quindi Windows SmartScreen potrebbe mostrare un avviso. Prima di continuare, confronta il file scaricato con il `FLDailyEditInstaller.exe.sha256` pubblicato nell'[ultima versione](https://github.com/gvoze32/fldailyedit/releases/tag/latest).
> Se Windows blocca il programma di installazione tramite Smart App Control, apri **Settings → Privacy & security → Windows Security → App & browser control → Smart App Control settings** e imposta **Off**. In alternativa, fai clic destro sul file scaricato, apri **Properties** e seleziona **Unblock**, se disponibile.
Per un'installazione manuale senza il programma di installazione, scarica lo [ZIP pubblico Fast](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip) o lo [ZIP pubblico Deep](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip). Estrai `EDIT00000000`, crea una copia di sicurezza del salvataggio corrente, quindi copia il file estratto in:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Per un'esecuzione su richiesta o un elenco personalizzato di club, crea un fork del repository e usa **Run workflow** dalla scheda Actions.

## Cosa aggiorna

- Trasferimenti, svincoli, prestiti e rientri dai prestiti
- Numeri di maglia disponibili ricavati dai dati sulle rose di FotMob
- Identità dei giocatori verificate rispetto alla rosa corrente di FL26
- Formazioni e piani di gioco interessati dalle modifiche alle rose
- Report sui trasferimenti e log di controllo in formato JSON Lines
- Salvataggi precompilati ogni giorno tramite GitHub Actions
- Creazioni di giocatori e correzioni degli attributi sottoposte a revisione tramite comandi Player Update espliciti

Il programma di aggiornamento non sovrascrive un numero di maglia già usato da
un altro componente della rosa. Verifica inoltre il club corrente del giocatore
prima di applicare un trasferimento.

## Roadmap / Completa per ora

Tutte le attività attuali della roadmap sono completate. Attendiamo la prossima idea utile.

## Sicurezza e limitazioni

- Le esecuzioni locali creano backup a rotazione, eseguono la cifratura in modo atomico e ne verificano il risultato.
- I salvataggi vengono convalidati prima e dopo le modifiche alle rose.
- Un blocco di processo impedisce a due esecuzioni di scrivere contemporaneamente lo stesso output.
- Le istantanee FotMob incomplete interrompono l'esecuzione anziché produrre un salvataggio parziale.
- Le corrispondenze ambigue dei giocatori, le discrepanze del club di provenienza
  e le rose di destinazione complete vengono ignorate.
- Wikipedia, Sortitoutsi e Transfermarkt sono fonti supplementari. Un'interruzione
  di una di queste fonti non invalida un'istantanea FotMob completa.
- `--allow-overflow-release` interrompe l'operazione in sicurezza perché il
  catalogo incluso non contiene dati completi sul ruolo e sull'OVR di ogni giocatore.

## Esecuzione locale

La configurazione locale è supportata su macOS, Linux e Windows tramite WSL. È
richiesto Python 3.10 o una versione successiva.

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
# Preview changes without writing a save
python run.py run --dry-run --edit-file base/EDIT00000000

# Validate an existing save
python run.py validate --edit-file base/EDIT00000000

# Validate one-file-per-player updates against the pristine base revision
python run.py players validate

# Apply reviewed Player Updates explicitly to an existing output save
python run.py players apply \
  --base-revision fl26-u2.2-national-squads \
  --edit-file output/EDIT00000000 \
  --in-place

# Apply all effective transfers available through today
python run.py run --window auto

# Rebuild from the bundled base
python run.py run --from-base --window auto

# Update a specific save in place
python run.py run --edit-file /path/to/EDIT00000000 --in-place

# Show every run option
python run.py run --help
```

| Comando | Scopo |
|---|---|
| `run` | Applica solo i trasferimenti verificati |
| `players validate` | Convalida tutti i Player Update rispetto alla base originale |
| `players apply` | Applica esplicitamente i Player Update sottoposti a revisione a un salvataggio |
| `log` | Mostra i trasferimenti applicati di recente |
| `inspect` | Esamina le squadre, il numero di giocatori e gli offset del salvataggio |
| `validate` | Controlla le registrazioni nelle rose e le mappature dei piani di gioco |
| `repair` | Ripara una base precedente usando salvataggi di riferimento |


`run` gestisce soltanto i trasferimenti: non carica né applica mai i Player
Update. Per combinare i due workflow, esegui prima il comando per i trasferimenti
su un salvataggio di output, quindi esegui `players apply --in-place` sullo stesso
salvataggio.

## Aggiornamenti dei giocatori

Ogni Player Update sottoposto a revisione è un file JSON completo con versione
dello schema 2 per ciascun giocatore in `players/`. Registra un'`operation`
(`create` o `update`), un ciclo di vita (`active`, `upstreamed` o `retired`), le
revisioni esatte della base in `applies_to`, un'identità stabile del giocatore e
la provenienza dell'UUID e del profilo Pes Retro Stats, le prove citate e i dati
PES sottoposti a revisione. Gli aggiornamenti di creazione contengono una
proposta di scheda completa del giocatore e i dati della rosa di destinazione.
Gli aggiornamenti dei giocatori esistenti contengono soltanto i valori supportati
che differiscono dalla base verificata; ogni modifica registra i valori letterali
`from` e `to`.
I gruppi di aggiornamento supportati sono abilità, compatibilità con i ruoli,
stile di gioco, abilità giocatore, stili COM, nazionalità, impostazioni
fisiche/di base e ruolo registrato.

### Percorso semplice tramite issue

1. Apri il [modulo issue per l'aggiornamento di un giocatore](.github/ISSUE_TEMPLATE/player-update.yml).
   Inserisci il `Player name` esattamente come appare in un `Pes Retro Stats
   profile` canonico, fornisci gli URL delle prove e attendi che un maintainer
   applichi esattamente l'etichetta `generate-player-draft`.
2. Il workflow di generazione configurato recupera quel profilo e apre una PR in
   bozza contenente una proposta schema-version-2 in
   `players/<player-slug>.json`. Dal profilo ricava l'istantanea della fonte,
   l'identità, le impostazioni fisiche, i dati sui ruoli, le abilità, lo stile di
   gioco, le abilità giocatore e gli stili COM.
3. Per una creazione, in `draft.missing` restano elencati soltanto i valori
   specifici del gioco non disponibili dalla fonte: gli ID PES e i nomi
   stampati dell'identità e del giocatore, l'ID e il nome della squadra, l'ID
   della nazionalità, il colore della pelle e il colore dell'iride. Un
   contributore o un maintainer deve fornirli. Per un aggiornamento, il
   generatore individua il giocatore nella base verificata ed emette soltanto le
   differenze effettive `from`/`to`. Un ruolo della fonte non supportato da PES
   2021, come `RWB`, viene omesso anziché rimappato, anche dalla modifica del
   ruolo registrato.
4. Un contributore e un maintainer esaminano ogni valore generato come proposta
   non approvata. La CI accetta un Player Update soltanto quando la PR aggiunge o
   modifica esattamente un percorso JSON canonico di un giocatore e il
   validatore semantico condiviso ha esito positivo.
5. Il merge della PR resta lo stato di approvazione umana. Nel file JSON non
   esiste un flag `approved` separato.

È previsto che ogni proposta generata non superi la convalida dei file completi.
Per convertire le prove generate nello schema v2 completo, rimuovi i campi
riservati alla bozza `evidence.current_team`, `evidence.issue_number` ed
`evidence.issue_url`; conserva `evidence.profile_url` canonico,
`evidence.proof_urls` sottoposto a revisione ed `evidence.effective_date`; quindi
aggiungi un valore `evidence.reason` sottoposto a revisione e non vuoto. Mantieni
l'UUID canonico del profilo in `identity.pes_retro_stats_id` e soltanto i valori
di gioco sottoposti a revisione in `pes`. Per una creazione, completa inoltre
ogni campo specifico del gioco indicato da `draft.missing`. Infine, prima della
convalida dei file completi, rimuovi gli oggetti di primo livello `source` e
`draft`, che sono metadati riservati alla revisione della bozza generata.

### Percorso diretto per PR con un solo file

Un contributore esperto può ignorare la bozza generata dall'issue e aprire
direttamente una PR che aggiunga o modifichi esattamente un file completo
`players/<player-slug>.json`. Includi nei campi `identity` ed `evidence` la
provenienza dell'UUID e del profilo canonici, le prove citate, i valori PES
sottoposti a revisione, i valori di riferimento previsti per l'aggiornamento, il
ciclo di vita e la revisione esatta della base; quindi esegui
`python run.py players validate` prima di richiedere la revisione. Non includere
i metadati di primo livello `source` o `draft` della bozza generata. Non inserire
nella PR altre modifiche al codice o alla documentazione.

L'applicazione avviene sempre tramite un comando esplicito e richiede la revisione
esatta indicata in `data/base_manifest.json`; una mancata corrispondenza della
revisione causa un errore prima della decrittazione del salvataggio di destinazione.

### Ciclo di vita della revisione

Quando cambia la base ufficiale, aggiorna insieme `base/EDIT00000000` e
`data/base_manifest.json`. Conserva i Player Update storici in `players/`; non
eliminarli soltanto perché la revisione è cambiata. Un Player Update attivo il
cui elenco `applies_to` non contiene la nuova revisione è inattivo: la convalida
segnala `needs_review` e l'applicazione lo ignora. Dopo la revisione, aggiungi la
nuova revisione solo se il Player Update è ancora applicabile, contrassegnalo
come `upstreamed` quando la base ufficiale ne include la modifica oppure come
`retired` quando non è più applicabile.

Opzioni comuni di `run`:

| Opzione | Scopo |
|---|---|
| `--deep` | Recupera ogni club FotMob indicizzato localmente |
| `--club "Chelsea,Arsenal"` | Limita l'esecuzione ai club selezionati |
| `--window auto` | Riproduce tutti i trasferimenti datati disponibili fino a oggi |
| `--window summer` | Usa l'intervallo più recente dal 1° giugno al 30 settembre |
| `--window winter` | Usa l'intervallo gennaio-febbraio dell'anno selezionato |
| `--since YYYY-MM-DD` | Imposta manualmente il limite inferiore della data |
| `--dry-run` | Pianifica le modifiche senza scrivere un salvataggio |
| `--from-base` | Parte da `base/EDIT00000000` |
| `--fotmob-only` | Esegue senza fonti supplementari per i trasferimenti |

Senza `--from-base`, un'esecuzione normale prosegue dall'ultimo output verificato.
Questo impedisce che i trasferimenti scompaiano quando una successiva esecuzione
pianificata legge nuovamente la cronologia cumulativa.

## Fonti dei trasferimenti

FotMob fornisce la cronologia principale dei trasferimenti e i metadati delle
rose. Gli elenchi stagionali di Wikipedia, le segnalazioni di trasferimenti
abilitate su SortitoutSI e i record verificati e datati di Transfermarkt
integrano o confermano i percorsi dei trasferimenti. I profili Pes Retro Stats
forniscono proposte non approvate, ricavate dalla fonte, per le bozze dei Player
Update.

I record provenienti da fonti diverse vengono riconciliati senza eliminarne le
date, gli ID, le citazioni o i link alle prove. Gli eventi privi di data, con
efficacia futura, in conflitto o ambigui non possono aggiornare autonomamente il
salvataggio.

La corrispondenza dei giocatori inizia dalla rosa di provenienza e usa quella di
destinazione come soluzione di riserva idempotente. Ruolo, nazionalità ed età
vengono considerati soltanto quando queste informazioni sono disponibili.

## Sviluppo

Esegui la suite di test con:

```bash
pytest -v
```

La suite copre l'analisi e la convalida dei salvataggi, la riconciliazione dei
trasferimenti, la pianificazione delle rose, la cronologia dei prestiti, la
corrispondenza dei giocatori, i limiti delle rose, i report, i backup e i blocchi
di processo.

## Licenza

FL Daily Edit è disponibile secondo i termini della [licenza MIT](LICENSE).
