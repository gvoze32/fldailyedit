[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](README.md) [![Bahasa Indonesia](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Bahasa_Indonesia-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Version Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Licence : MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

FL Daily Edit met à jour les effectifs de SP Football Life 2026 et eFootball PES 2021 en appliquant les transferts du monde réel à un fichier de sauvegarde `EDIT00000000`.

## Compatibilité

La base fournie cible **SP Football Life 2026**. Elle requiert :

- Football Life 26 Update 2.2
- SmokePatch's National Squads Update

Elle n'est pas compatible avec UML, les versions antérieures de FL26 ou les installations sans la mise à jour des équipes nationales. Démarrez une nouvelle carrière en Ligue des Masters ou Deviens une Légende après avoir installé la sauvegarde.

La [base incluse](base/EDIT00000000) est le [Gondowan's Mid-Summer EDIT](https://www.reddit.com/r/SPFootballLife/comments/1v7z782/release_gondowans_midsummer_edit_file_more_than/), daté du 27 juillet 2026. Elle comprend plus de 500 transferts, des notes, postes et numéros de maillot mis à jour, des retours de prêt, des entraîneurs, des compositions et les changements de promotion ou relégation. Elle ne crée pas de joueurs et n'ajoute pas de clubs promus de troisième division.

## Programme d’installation Windows

Le programme d’installation Windows est l’option recommandée aux débutants. L’interface du programme d’installation est actuellement disponible uniquement en anglais. Les téléchargements validés actuels ciblent **uniquement Football Life 2026 Update 2.2 + SmokePatch's National Squads Update**. La détection d’eFootball PES 2021 vanilla est disponible, mais l’installation reste désactivée jusqu’à la publication d’une base validée correspondante.

1. Téléchargez [FLDailyEditInstaller.exe](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.exe).
2. Fermez le jeu.
3. Choisissez **Fast** ou **Deep**. Ce sont deux choix distincts de couverture des mises à jour, chacun affichant son heure de génération.
4. Confirmez le dossier Football Life 2026 détecté ou utilisez **Browse** si nécessaire.
5. Sélectionnez **Download and install**. Le programme vérifie le téléchargement, sauvegarde le fichier actuel et le remplace de manière atomique.

> [!WARNING]
> L’exécutable initial n’est pas signé, Windows SmartScreen peut donc afficher un avertissement. Avant de continuer, comparez le fichier téléchargé avec le `FLDailyEditInstaller.exe.sha256` publié dans la [dernière version](https://github.com/gvoze32/fldailyedit/releases/tag/latest).

Pour une installation manuelle sans le programme, téléchargez le [ZIP public Fast](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip) ou le [ZIP public Deep](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip). Extrayez `EDIT00000000`, sauvegardez votre fichier actuel, puis copiez le fichier extrait vers :

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Pour une exécution à la demande ou une liste de clubs personnalisée, forkez le dépôt et utilisez **Run workflow** depuis l'onglet Actions.

## Ce qui est mis à jour

- Transferts, départs libres, prêts et retours de prêt
- Numéros de maillot disponibles depuis les données d'effectif FotMob
- Identités des joueurs vérifiées par rapport à l'effectif actuel de FL26
- Compositions et plans de jeu affectés par les mouvements d'effectif
- Rapports de transferts et journaux d'audit JSON Lines
- Sauvegardes précompilées quotidiennes via GitHub Actions
- Créations de joueurs et corrections d'attributs revues via des commandes explicites Player Update

L'outil n'écrase pas un numéro de maillot déjà utilisé par un autre membre de l'équipe. Il vérifie également le club actuel du joueur avant d'appliquer un transfert.

## Feuille de route / En cours de développement

Cet élément est planifié et en cours de développement :

1. **Mise à jour locale dans l'interface graphique (remplace la distribution de bases multiples séparées)** — au lieu de distribuer des bases préconstruites distinctes pour chaque patch, un mode de mise à jour locale sera ajouté directement à l'interface de l'installateur afin que les utilisateurs puissent exécuter le pipeline sur leur propre sauvegarde (**SP Football Life 2026**, **vanilla eFootball PES 2021** et **UML**).

## Sécurité et limitations

- Les exécutions locales créent des sauvegardes rotatives et utilisent un chiffrement atomique vérifié.
- Les sauvegardes sont validées avant et après chaque modification d'effectif.
- Un verrouillage de processus empêche deux exécutions d'écrire sur la même sortie en même temps.
- Les instantanés FotMob incomplets interrompent l'exécution au lieu de produire une sauvegarde partielle.
- Les correspondances de joueurs ambiguës, les discordances de club d'origine et les effectifs cibles complets sont ignorés.
- Wikipedia, Sortitoutsi et Transfermarkt sont complémentaires. Une panne de l'une de ces sources n'invalide pas un instantané complet de FotMob.
- `--allow-overflow-release` échoue de manière sécurisée car le catalogue inclus ne contient pas les données complètes de poste et de note globale (OVR) pour chaque joueur.

## Exécution locale

La configuration locale est prise en charge sous macOS, Linux et Windows via WSL. Python 3.10 ou supérieur est requis.

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

## Commandes courantes

```bash
# Prévisualiser les modifications sans écrire de sauvegarde
python run.py run --dry-run --edit-file base/EDIT00000000

# Valider une sauvegarde existante
python run.py validate --edit-file base/EDIT00000000

# Valider les Player Updates (un fichier par joueur) par rapport à la base d'origine
python run.py players validate

# Appliquer explicitement les Player Updates revues à une sauvegarde de sortie
python run.py players apply \
  --base-revision fl26-u2.2-national-squads \
  --edit-file output/EDIT00000000 \
  --in-place

# Appliquer tous les transferts effectifs disponibles jusqu'à aujourd'hui
python run.py run --window auto

# Reconstruire à partir de la base d'origine fournie
python run.py run --from-base --window auto

# Mettre à jour une sauvegarde spécifique sur place
python run.py run --edit-file /path/to/EDIT00000000 --in-place

# Afficher toutes les options disponibles
python run.py run --help
```

| Commande | Objectif |
|---|---|
| `run` | Appliquer uniquement les transferts vérifiés |
| `players validate` | Valider toutes les Player Updates par rapport à la base d'origine |
| `players apply` | Appliquer explicitement les Player Updates revues à une sauvegarde |
| `log` | Afficher les transferts récemment appliqués |
| `inspect` | Inspecter les équipes, le nombre de joueurs et les décalages du fichier de sauvegarde |
| `validate` | Vérifier les inscriptions dans les effectifs et les plans de jeu |
| `repair` | Réparer une base héritée à l'aide de sauvegardes de référence |


`run` gère uniquement les transferts : il ne charge ni n'applique jamais les Player Updates. Pour combiner les deux flux de travail, exécutez d'abord la commande de transferts sur une sauvegarde de sortie, puis lancez `players apply --in-place` sur cette même sauvegarde.

## Player Updates

Chaque Player Update validée est un fichier JSON conforme au schéma v2 par joueur sous `players/`. Elle enregistre une `operation` (`create` ou `update`), un cycle de vie (`active`, `upstreamed` ou `retired`), les révisions de base exactes dans `applies_to`, l'identité stable du joueur et la provenance UUID/profil Pes Retro Stats, les preuves citées et les données PES révisées. Les créations contiennent une proposition de profil complet du joueur et les données d'effectif cible. Les mises à jour de joueurs existants contiennent uniquement les valeurs compatibles différentes de la base vérifiée ; chaque modification consigne les valeurs littérales `from` et `to`.
Les groupes pris en charge sont les compétences, la maîtrise des postes, le style de jeu, les aptitudes de joueur, les styles COM, la nationalité, les paramètres physiques/de base et le poste enregistré.

### Méthode simple via une Issue

1. Ouvrez le [formulaire d'issue de mise à jour de joueur](.github/ISSUE_TEMPLATE/player-update.yml). Entrez le `Player name` exactement tel qu'il apparaît sur un profil canonique `Pes Retro Stats`, fournissez les URL de preuve et attendez qu'un mainteneur applique le label exact `generate-player-draft`.
2. Le workflow configuré récupère ce profil et ouvre une PR en brouillon contenant une proposition `players/<player-slug>.json` au format schéma v2. Il extrait du profil l'instantané source, l'identité, les paramètres physiques, les données de poste, les compétences, le style de jeu, les aptitudes et les styles COM.
3. Pour une création, seules les valeurs propres au jeu non disponibles à la source restent listées dans `draft.missing` : les ID PES et noms d'affichage, l'ID et le nom de l'équipe, l'ID de nationalité, la couleur de peau et la couleur des yeux. Un contributeur ou un mainteneur doit les renseigner. Pour une mise à jour, le générateur retrouve le joueur dans la base vérifiée et ne produit que les différences réelles `from`/`to`. Un poste source non pris en charge par PES 2021, tel que `RWB`, est omis plutôt que réassigné, y compris pour le changement de poste enregistré.
4. Un contributeur et un mainteneur vérifient chaque valeur générée. L'intégration continue n'accepte une Player Update que si la PR ajoute ou modifie exactement un chemin JSON canonique de joueur et que le validateur sémantique réussit.
5. La fusion de la PR constitue l'approbation humaine. Il n'y a pas d'indicateur `approved` distinct dans le fichier JSON.

Toute proposition générée échouera à la validation complète. Pour convertir les données au schéma v2 complet, supprimez les champs de brouillon `evidence.current_team`, `evidence.issue_number` et `evidence.issue_url` ; conservez les champs canoniques `evidence.profile_url`, `evidence.proof_urls` et `evidence.effective_date` ; et ajoutez une valeur `evidence.reason` non vide et révisée. Conservez l'UUID canonique dans `identity.pes_retro_stats_id` et uniquement les données de jeu revues dans `pes`. Pour une création, complétez également tous les champs requis listés dans `draft.missing`. Enfin, retirez les objets de premier niveau `source` et `draft` avant la validation finale.

### Méthode directe via une PR sur un seul fichier

Un contributeur expérimenté peut ignorer le brouillon généré et ouvrir directement une PR ajoutant ou modifiant exactement un fichier `players/<player-slug>.json` complet. Renseignez la provenance UUID/profil dans `identity` et `evidence`, les preuves, les valeurs PES revues, les références attendues, le cycle de vie et la révision de base exacte, puis exécutez `python run.py players validate` avant de demander une relecture. N'incluez pas les métadonnées `source` ou `draft`. N'ajoutez aucune autre modification de code ou de documentation dans cette PR.

L'application s'effectue toujours par commande explicite et requiert la révision exacte de `data/base_manifest.json` ; une non-concordance de révision échoue avant le déchiffrement de la sauvegarde cible.

### Cycle de vie des révisions

Lorsque la base officielle change, mettez à jour `base/EDIT00000000` et `data/base_manifest.json` conjointement. Conservez l'historique des Player Updates dans `players/` ; ne les supprimez pas simplement parce que la révision a changé. Une mise à jour active dont la liste `applies_to` ne contient pas la nouvelle révision devient inactive : la validation signale `needs_review` et l'application l'ignore. Après révision, ajoutez la nouvelle révision uniquement si la modification est toujours applicable, marquez-la comme `upstreamed` si la base officielle intègre déjà le changement, ou marquez-la comme `retired` si elle n'est plus pertinente.

Options courantes de `run` :

| Option | Objectif |
|---|---|
| `--deep` | Récupérer tous les clubs FotMob indexés localement |
| `--club "Chelsea,Arsenal"` | Limiter l'exécution aux clubs sélectionnés |
| `--window auto` | Rejouer tous les transferts datés disponibles jusqu'à aujourd'hui |
| `--window summer` | Utiliser la dernière période du 1er juin au 30 septembre |
| `--window winter` | Utiliser la période de janvier à février de l'année sélectionnée |
| `--since YYYY-MM-DD` | Définir manuellement la date limite inférieure |
| `--dry-run` | Planifier les modifications sans écrire de sauvegarde |
| `--from-base` | Partir de `base/EDIT00000000` |
| `--fotmob-only` | Exécuter sans sources de transferts complémentaires |

Sans `--from-base`, une exécution standard reprend depuis la dernière sortie vérifiée. Cela évite que les transferts disparaissent lorsqu'une exécution planifiée ultérieure relit l'historique cumulé.

## Sources de transferts

FotMob fournit l'historique principal des transferts et les métadonnées des effectifs. Les listes saisonnières de Wikipedia, les soumissions de transferts activées de SortitoutSI et les enregistrements datés et vérifiés de Transfermarkt complètent ou confirment les mouvements. Les profils de Pes Retro Stats fournissent des propositions dérivées de la source pour les brouillons de Player Update.

Les données provenant de différentes sources sont réconciliées sans perdre leurs dates, identifiants, citations ou liens de preuve. Les événements sans date, à effet futur, contradictoires ou ambigus ne peuvent pas mettre à jour la sauvegarde d'eux-mêmes.

La mise en correspondance des joueurs commence par l'effectif d'origine et utilise l'effectif de destination comme solution de secours idempotente. Le poste, la nationalité et l'âge ne sont pris en compte que lorsque ces informations sont disponibles.

## Développement

Lancez la suite de tests avec :

```bash
pytest -v
```

La suite couvre l'analyse et la validation des sauvegardes, la réconciliation des transferts, la planification des effectifs, l'historique des prêts, la correspondance des joueurs, les limites d'effectif, les rapports, les sauvegardes et le verrouillage des processus.

## Licence

FL Daily Edit est distribué sous [Licence MIT](LICENSE).
