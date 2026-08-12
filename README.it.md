[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Versione Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Licenza: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

FL Daily Edit aggiorna le rose di SP Football Life 2026 ed eFootball PES 2021
applicando i trasferimenti del mondo reale a un file di salvataggio `EDIT00000000`.

> **La creazione di nuovi giocatori è opt-in. Le chiamate API dirette restano
> disabilitate per impostazione predefinita; `players apply --allow-create`
> richiede un donor verificato di `PlayerAppearance.bin`.**
>
> I trasferimenti per i giocatori già presenti nel salvataggio e gli aggiornamenti
> revisionati per i giocatori esistenti rimangono supportati. I giocatori mancanti
> vengono ignorati e una rosa di destinazione completa viene ignorata per
> impostazione predefinita invece di svincolare un giocatore esistente.

> [!WARNING]
> **Avviso beta:** FL Daily Edit, i dati del repository e le versioni generate sono ancora in fase di test. Potrebbero non funzionare con ogni configurazione di gioco/salvataggio; alcune condizioni non sono ancora supportate.

## Compatibilità

La base inclusa è destinata a **SP Football Life 2026**. Richiede:

- Football Life 26 Update 2.2
- SmokePatch's National Squads Update

Non è compatibile con UML, versioni precedenti di FL26 o installazioni prive
dell'aggiornamento per le nazionali. Avviare una nuova carriera Campionato Master o Diventa un Mito
dopo aver installato il file di salvataggio.

La [base inclusa](base/EDIT00000000) è il file
[Gondowan's Mid-Summer EDIT](https://www.reddit.com/r/SPFootballLife/comments/1v7z782/release_gondowans_midsummer_edit_file_more_than/)
del 27 luglio 2026. Include oltre 500 trasferimenti, valutazioni generali aggiornate,
ruoli, numeri di maglia, rientri dai prestiti, allenatori, formazioni e variazioni
di promozioni e retrocessioni. Non crea nuovi giocatori né aggiunge squadre promosse dalla terza serie.

## Programma di installazione per Windows

Il programma di installazione per Windows è l'opzione consigliata per i principianti. L'interfaccia del programma di installazione è attualmente disponibile solo in inglese. I download convalidati correnti sono destinati **esclusivamente a Football Life 2026 Update 2.2 + SmokePatch's National Squads Update**. Il rilevamento di eFootball PES 2021 vanilla è disponibile, ma l'installazione rimane disabilitata finché non viene pubblicata una base convalidata corrispondente.

1. Scaricare ed estrarre [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip).
2. Chiudere il gioco.
3. Selezionare **Fast** o **Deep**. Sono opzioni separate per la copertura dell'aggiornamento e ciascuna mostra la data e ora di generazione.
4. Confermare la cartella di Football Life 2026 rilevata, oppure usare **Browse** se necessario.
5. Selezionare **Download and install**. L'installer verifica il download, esegue il backup del salvataggio corrente e lo sostituisce in modo atomico.

**Aggiornare un salvataggio esistente tramite GUI:** L'installer può anche
aggiornare un file `EDIT00000000` con layout comune selezionato dall'utente,
anziché installare una versione precompilata. Selezionare **Update my local
save**, scegliere un percorso rilevato o utilizzare **Browse**, selezionare
**Fast** o **Deep** e, dopo la revisione, fare clic su **Apply update**. La
procedura guidata convalida il salvataggio prima di modificarlo, crea un backup
nella stessa posizione e mostra l'avanzamento, il risultato o la diagnostica.
L'idoneità locale non dipende dall'etichetta SPFL/PES/UML e questa procedura non
scarica una versione remota precompilata. Se i cataloghi esterni opzionali di
SPFL non sono disponibili, il matcher locale utilizza i nomi di giocatori e
squadre integrati nel salvataggio selezionato, consentendo l'aggiornamento locale
anche senza di essi.

> [!WARNING]
> L'eseguibile dell'installer non è firmato, quindi Windows SmartScreen potrebbe all'avvio mostrare un avviso. Prima di proseguire, confrontare il file `FLDailyEditInstaller.zip` scaricato con il file `FLDailyEditInstaller.zip.sha256` pubblicato nella [versione più recente](https://github.com/gvoze32/fldailyedit/releases/tag/latest).
> Se Windows blocca l'installer tramite Smart App Control, aprire **Settings → Privacy & security → Windows Security → App & browser control → Smart App Control settings** e impostarlo su **Off**. In alternativa, fare clic con il tasto destro sul file scaricato, aprire **Properties** e selezionare **Unblock**, se disponibile.

Per l'installazione manuale senza installer, scaricare il [file ZIP pubblico della versione Fast](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip) o il [file ZIP pubblico della versione Deep](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip). Estrarre `EDIT00000000`, eseguire il backup del file corrente e copiare il file estratto in:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Per un'esecuzione su richiesta o per utilizzare un elenco personalizzato di club, effettuare un fork del repository e utilizzare **Run workflow** dalla scheda Actions.

## Cosa viene aggiornato

- Trasferimenti, svincoli, prestiti e rientri dai prestiti
- Numeri di maglia disponibili dai dati delle rose di FotMob
- Identità dei giocatori verificate rispetto alla rosa attuale di FL26
- Formazioni e schemi di gioco modificati in base alle variazioni di rosa
- Report sui trasferimenti e registri di controllo JSON Lines
- Salvataggi precompilati giornalieri tramite GitHub Actions
- Creazioni di giocatori e correzioni degli attributi revisionate tramite comandi espliciti Player Update

L'aggiornatore non sovrascrive un numero di maglia già utilizzato da un altro membro
della rosa. Inoltre verifica la squadra attuale del giocatore prima di applicare un trasferimento.

## Roadmap / Completa per ora

Tutte le attività attuali della roadmap sono completate. Attendiamo la prossima idea utile.

## Sicurezza e limitazioni

- Le esecuzioni locali creano backup progressivi e utilizzano una crittografia atomica verificata.
- I salvataggi vengono convalidati prima e dopo le modifiche alle rose.
- Un blocco di processo impedisce a due esecuzioni di scrivere contemporaneamente sullo stesso output.
- Gli snapshot di FotMob incompleti interrompono l'esecuzione anziché generare un salvataggio parziale.
- Le corrispondenze ambigue dei giocatori e le discrepanze sulla squadra di origine vengono ignorate.
- Le rose di destinazione al completo vengono ignorate per impostazione predefinita; l'aggiornatore dei
  trasferimenti non svincola mai automaticamente un giocatore esistente.
- `--allow-overflow-release` è un'opzione separata ed esplicita per i soli trasferimenti. Richiede metadati
  completi su ruoli e OVR e può svincolare un candidato sicuro per liberare spazio. Se tali metadati sono
  incompleti, l'esecuzione si interrompe in sicurezza.
- Wikipedia, Sortitoutsi e Transfermarkt sono fonti supplementari. Un'interruzione in una di queste
  fonti non invalida uno snapshot completo di FotMob.

**Aggiornamenti dei trasferimenti vs Player Updates**

Si tratta di flussi di lavoro separati:

- `run` elabora i trasferimenti per i giocatori già presenti nel salvataggio. Se una squadra di
  destinazione è al completo, quel trasferimento viene ignorato; altri trasferimenti sicuri nella
  stessa esecuzione possono comunque essere applicati.
- `players apply` applica le modifiche agli attributi revisionate. Le specifiche `update` per i
  giocatori esistenti sono supportate.
- Le specifiche `create` per nuovi giocatori rimangono caricabili e revisionabili.
  L'applicazione richiede `players apply --allow-create`, un donor d'aspetto
  esplicito e una fonte valida di `PlayerAppearance.bin`. Un donor assente o non
  valido rifiuta la specifica senza modificare i byte del salvataggio.
- Una rosa di destinazione completa richiede anche `--allow-overflow-release`;
  si può svincolare solo una riserva con OVR positivo completo dal salvataggio.
  I metadati di `Player.bin` non sostituiscono l'OVR.

## Esecuzione locale

La configurazione locale è supportata su macOS, Linux e Windows tramite WSL. È richiesto Python 3.10
o versioni successive.

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
| `players apply` | Applica esplicitamente i Player Update revisionati a un salvataggio |
| `log` | Mostra i trasferimenti applicati di recente |
| `inspect` | Ispeziona squadre, conteggio giocatori e offset del salvataggio |
| `validate` | Controlla le registrazioni nelle rose e le mappature dei piani di gioco |
| `repair` | Ripara una base legacy utilizzando salvataggi di riferimento |
| `audit` | Verifica in sola lettura del salvataggio e dei metadati nativi |
| `compare` | Confronto in sola lettura di due varianti CPK native |

`run` gestisce esclusivamente i trasferimenti: non carica né applica mai i Player Update.
Per combinare entrambi i flussi di lavoro, eseguire prima il comando di trasferimento su un
salvataggio di output, quindi eseguire `players apply --in-place` sullo stesso file.

## Aggiornamenti giocatori (Player Updates)

Ogni Player Update revisionato è un file JSON completo conforme alla versione 2 dello schema per
singolo giocatore in `players/`. Registra un'operazione (`operation`: `create` o `update`), un ciclo di
vita (`active`, `upstreamed` o `retired`), le revisioni base esatte in `applies_to`, l'identità stabile
del giocatore e la provenienza del profilo/UUID da Pes Retro Stats, le prove citate e i dati PES
revisionati. Gli aggiornamenti di creazione contengono una proposta di scheda giocatore completa e i dati
della rosa di destinazione. Gli aggiornamenti dei giocatori esistenti contengono solo i valori supportati
che differiscono dalla base verificata; ogni modifica registra i valori letterali `from` e `to`.
I record `create` restano supportati dallo schema per la revisione. La mutazione
tramite CLI richiede `players apply --allow-create` e dati d'aspetto validi; le
chiamate API dirette restano disabilitate per impostazione predefinita. Se la rosa
è completa, aggiungere `--allow-overflow-release`; metadati di sicurezza assenti o
non validi lasciano il salvataggio invariato.
I gruppi di aggiornamento supportati sono abilità, competenza nei ruoli, stile di gioco, abilità giocatore,
stili COM, nazionalità, impostazioni fisiche/base e ruolo registrato.
- I valori di revisione dell'OVR generati sono calcoli deterministici basati sulla formula
  pubblicata di PES 2021. Sono un ausilio per la parità, non una garanzia indipendente della
  runtime del gioco; i valori di abilità proposti richiedono comunque una revisione.
- Le bozze dei giocatori generate con il precedente identificatore del modello OVR devono essere rigenerate
  prima della convalida; non vi è alcuna migrazione implicita da v1 a v2.

### Percorso semplificato tramite issue

1. Aprire il [modulo issue per l'aggiornamento dei giocatori](.github/ISSUE_TEMPLATE/player-update.yml).
   Inserire il `Player name` esattamente come appare in un singolo `Pes Retro Stats profile` canonico,
   fornire gli URL delle prove e attendere che un manutentore applichi l'etichetta precisa `generate-player-draft`.
2. Il workflow del generatore configurato recupera tale profilo e apre una bozza di PR contenente una proposta
   `players/<player-slug>.json` conforme alla versione 2 dello schema. Ricava dal profilo lo snapshot di origine,
   l'identità, le impostazioni fisiche, i dati del ruolo, le abilità, lo stile di gioco, le abilità speciali e gli stili COM.
3. Per una creazione, solo i valori di gioco locali non disponibili dalla fonte rimangono elencati in `draft.missing`:
   gli ID PES e i nomi stampati per l'identità e il giocatore, ID e nome della squadra, ID della nazionalità,
   colore della pelle e colore dell'iride. Un collaboratore o manutentore deve fornirli. Per un aggiornamento,
   il generatore individua il giocatore nella base verificata ed emette solo le differenze effettive `from`/`to`.
   Un ruolo di origine non supportato da PES 2021, come `RWB`, viene omesso anziché rimappato, anche per quanto
   riguarda il cambio di ruolo registrato.
4. Un collaboratore e un manutentore esaminano ogni valore generato come proposta non approvata. La CI accetta un
   Player Update solo quando la PR aggiunge o modifica esattamente un percorso JSON canonico del giocatore e il
   validatore semantico condiviso ha esito positivo.
5. L'unione (merge) della PR costituisce lo stato di approvazione umana. Non è presente alcun flag `approved` separato nel file JSON.

Ogni proposta generata è destinata a non superare la convalida del file completo. Per convertire le prove generate
nello schema v2 completo, rimuovere i campi esclusivi della bozza `evidence.current_team`, `evidence.issue_number` ed
`evidence.issue_url`; mantenere l'URL canonico `evidence.profile_url`, le `evidence.proof_urls` revisionate e l'esatta
`evidence.effective_date`; e aggiungere un campo `evidence.reason` revisionato e non vuoto. Salvare l'UUID canonico del
profilo come `identity.pes_retro_stats_id` e solo i valori di gioco revisionati in `pes`. Per una creazione, completare
inoltre ogni campo locale del gioco indicato in `draft.missing`. Gli ID PES dei giocatori creati devono essere univoci
e pari ad almeno `0x100000` (1.048.576); l'allocatore delle proposte rimane in tale intervallo riservato.
Quindi rimuovere gli oggetti di primo livello `source` e `draft`, che costituiscono metadati generati per la bozza a
solo scopo di revisione, prima della convalida del file completo.

### Percorso diretto tramite PR a file singolo

Un collaboratore esperto può saltare la bozza generata tramite issue e aprire direttamente una PR che aggiunga o
modifichi esattamente un file completo `players/<player-slug>.json`. Fornire la provenienza canonica dell'UUID/profilo
in `identity` ed `evidence`, le prove citate, i valori PES revisionati, le basi di riferimento previste per l'aggiornamento,
il ciclo di vita e la revisione di base esatta, quindi eseguire `python run.py players validate` prima di richiedere
la revisione. Non includere i metadati di primo livello `source` o `draft` della bozza generata. Non includere altre
modifiche al codice o alla documentazione in tale PR.

L'applicazione richiede sempre un comando esplicito e necessita della revisione esatta indicata in
`data/base_manifest.json`; una mancata corrispondenza della revisione genera un errore prima della decrittografia
del salvataggio di destinazione.

### Ciclo di vita delle revisioni

Quando la base ufficiale cambia, aggiornare contemporaneamente `base/EDIT00000000` e `data/base_manifest.json`.
Conservare lo storico dei Player Update in `players/`; non eliminarli semplicemente perché la revisione è cambiata.
Un Player Update attivo la cui lista `applies_to` non contiene la nuova revisione risulta inattivo: la convalida
segnala `needs_review` e l'applicazione lo ignora. Dopo la revisione, aggiungere la nuova revisione solo se il
Player Update è ancora applicabile, contrassegnarlo come `upstreamed` se la base ufficiale include già la modifica,
o come `retired` se non è più applicabile.

Opzioni comuni di `run`:

| Opzione | Scopo |
|---|---|
| `--deep` | Recupera tutti i club FotMob indicizzati localmente |
| `--club "Chelsea,Arsenal"` | Limita l'esecuzione ai club selezionati |
| `--window auto` | Riproduce tutti i trasferimenti datati disponibili fino a oggi |
| `--window summer` | Utilizza l'intervallo più recente dal 1° giugno al 30 settembre |
| `--window winter` | Utilizza l'intervallo di gennaio-febbraio dell'anno selezionato |
| `--since YYYY-MM-DD` | Imposta manualmente il limite di data inferiore |
| `--dry-run` | Pianifica le modifiche senza scrivere un salvataggio |
| `--from-base` | Inizia da `base/EDIT00000000` |
| `--fotmob-only` | Esegue senza fonti di trasferimento supplementari |

Senza `--from-base`, una normale esecuzione prosegue dall'ultimo output verificato. In questo modo si evita che i
trasferimenti scompariranno quando una successiva esecuzione programmata rilegge lo storico accumulato.

## Fonti dei trasferimenti

FotMob fornisce lo storico primario dei trasferimenti e i metadati delle rose. Gli elenchi stagionali di Wikipedia, i
contributi abilitati sui trasferimenti di SortitoutSI e i record datati verificati di Transfermarkt integrano o
confermano i percorsi dei trasferimenti. I profili di Pes Retro Stats forniscono proposte derivate dalla fonte e non
approvate per le bozze dei Player Update.

I record provenienti da fonti diverse vengono riconciliati senza tralasciare date, ID, citazioni o link di prova. Eventi
senza date, con decorrenza futura, contrastanti o ambigui non possono aggiornare il salvataggio in modo autonomo.

La corrispondenza dei giocatori inizia dalla rosa della squadra di origine e utilizza la rosa della squadra di
destinazione come fallback idempotente. Ruolo, nazionalità ed età vengono presi in considerazione solo se tali
informazioni sono disponibili.

## Sviluppo

Per eseguire la suite di test:

```bash
pytest -v
```

La suite comprende analisi e convalida dei salvataggi, riconciliazione dei trasferimenti, pianificazione delle rose,
storico dei prestiti, corrispondenza dei giocatori, limiti delle rose, reportistica, backup e blocco dei processi.

## Licenza

FL Daily Edit è distribuito sotto [Licenza MIT](LICENSE).
