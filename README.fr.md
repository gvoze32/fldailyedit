[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Version Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Licence: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

FL Daily Edit met à jour les effectifs de SP Football Life 2026 et d'eFootball PES 2021
en appliquant les transferts réels à un fichier de sauvegarde `EDIT00000000`.

> **La création de nouveaux joueurs est opt-in. Les appels API directs restent désactivés
> par défaut ; `players apply --allow-create` exige un donneur validé de `PlayerAppearance.bin`.**
>
> Les transferts pour les joueurs déjà présents dans la sauvegarde et les mises à jour révisées
> pour les joueurs existants restent pleinement pris en charge. Les joueurs absents sont ignorés
> et un effectif de destination complet est ignoré par défaut plutôt que de libérer un joueur existant.

> [!WARNING]
> **Avis beta :** FL Daily Edit, les données de son dépôt et les versions générées sont encore en cours de test. Ils peuvent ne pas fonctionner avec toutes les configurations du jeu/de la sauvegarde ; certaines conditions ne sont pas encore prises en charge.

## Compatibilité

La base fournie est destinée à **SP Football Life 2026**. Elle requiert :

- Football Life 26 Update 2.2
- SmokePatch's National Squads Update

Elle n'est pas compatible avec UML, les versions antérieures de FL26 ou les installations
sans mise à jour des équipes nationales. Démarrez une nouvelle carrière en Ligue des Masters
ou Vers une Légende après avoir installé le fichier de sauvegarde.

La [base incluse](base/EDIT00000000) correspond au fichier
[Gondowan's Mid-Summer EDIT](https://www.reddit.com/r/SPFootballLife/comments/1v7z782/release_gondowans_midsummer_edit_file_more_than/)
du 27 juillet 2026. Elle intègre plus de 500 transferts, des notes générales actualisées,
les postes, les numéros de maillot, les retours de prêt, les entraîneurs, les compositions d'équipe
ainsi que les montées et descentes. Elle ne crée pas de joueurs et n'ajoute pas les clubs promus de troisième division.

## Programme d’installation Windows

Le programme d'installation Windows est l'option recommandée pour les débutants. L’interface du programme d’installation est actuellement disponible uniquement en anglais. Les téléchargements validés actuels sont **exclusivement destinés à Football Life 2026 Update 2.2 + SmokePatch's National Squads Update**. La détection d’eFootball PES 2021 vanilla est disponible, mais l’installation reste désactivée jusqu’à la publication d’une base validée correspondante.

1. Téléchargez et extrayez [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip).
2. Fermez le jeu.
3. Choisissez **Fast** ou **Deep**. Ce sont des options distinctes d'étendue de mise à jour, affichant chacune la date et l'heure de génération.
4. Confirmez le dossier détecté de Football Life 2026, ou utilisez **Browse** si nécessaire.
5. Cliquez sur **Download and install**. L'installateur vérifie le téléchargement, sauvegarde le fichier actuel et le remplace de manière atomique.

**Mettre à jour une sauvegarde existante via l'interface graphique :** L'installateur
peut également mettre à jour un fichier `EDIT00000000` au format standard sélectionné
par l'utilisateur, au lieu d'installer une version précompilée. Choisissez
**Update my local save**, sélectionnez un emplacement détecté ou utilisez
**Browse**, choisissez **Fast** ou **Deep** et après vérification, cliquez sur
**Apply update**. L'assistant valide la sauvegarde avant toute modification, crée
une sauvegarde de secours au même endroit et affiche la progression, le résultat ou
les diagnostics. L'éligibilité locale ne dépend pas de l'étiquette SPFL/PES/UML,
et cette méthode ne télécharge pas de build distant précompilé. Lorsque ces catalogues
externes optionnels de SPFL ne sont pas disponibles, le comparateur local utilise
les noms de joueurs et d'équipes intégrés dans la sauvegarde sélectionnée, permettant
au processus de mise à jour locale de fonctionner sans eux.

> [!WARNING]
> L’exécutable de l’installateur n’est pas signé ; Windows SmartScreen peut donc afficher un avertissement lors de son lancement. Avant de continuer, comparez le `FLDailyEditInstaller.zip` téléchargé avec le `FLDailyEditInstaller.zip.sha256` publié dans la [dernière version](https://github.com/gvoze32/fldailyedit/releases/tag/latest).
> Si Windows bloque l'installateur via Smart App Control, ouvrez **Settings → Privacy & security → Windows Security → App & browser control → Smart App Control settings** et passez sur **Off**. Vous pouvez également faire un clic droit sur le fichier téléchargé, ouvrir **Properties** et cocher **Unblock** si l'option est présente.

Pour une installation manuelle sans installateur, téléchargez le [ZIP public de la version Fast](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip) ou le [ZIP public de la version Deep](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip). Extrayez `EDIT00000000`, sauvegardez votre fichier actuel et copiez le fichier extrait dans :

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Pour une exécution à la demande ou pour cibler une liste personnalisée de clubs, forkez le dépôt et utilisez **Run workflow** depuis l'onglet Actions.

## Ce qui est mis à jour

- Transferts, ruptures de contrat, prêts et retours de prêt
- Numéros de maillot disponibles d'après les données d'effectif FotMob
- Identités des joueurs vérifiées par rapport à l'effectif actuel de FL26
- Compositions et plans de jeu ajustés selon les mouvements d'effectif
- Rapports de transfert et journaux d'audit JSON Lines
- Sauvegardes précompilées quotidiennes via GitHub Actions
- Créations de joueurs et corrections d'attributs révisées via les commandes explicites Player Update

L'outil ne remplace pas un numéro de maillot déjà attribué à un autre membre de l'effectif.
Il contrôle également le club actuel du joueur avant d'appliquer tout transfert.

## Feuille de route / Terminée pour l’instant

Tous les éléments actuels de la feuille de route sont terminés. Nous attendons la prochaine idée utile.

## Sécurité et limites

- Les exécutions locales créent des sauvegardes tournantes et utilisent un chiffrement atomique vérifié.
- Les sauvegardes sont validées avant et après chaque modification d'effectif.
- Un verrouillage de processus empêche deux exécutions d'écrire simultanément dans la même sortie.
- Les instantanés FotMob incomplets interrompent l'exécution plutôt que de générer une sauvegarde partielle.
- Les correspondances ambiguës et les incohérences de club d'origine sont ignorées.
- Les effectifs de destination complets sont ignorés par défaut ; l'outil de transfert ne libère jamais automatiquement un joueur existant.
- `--allow-overflow-release` est une option distincte et explicite réservée aux transferts. Elle exige des métadonnées complètes sur les postes et l'OVR et peut libérer un candidat sans risque pour faire de la place. Si ces métadonnées sont incomplètes, l'exécution s'arrête en toute sécurité.
- Wikipedia, Sortitoutsi et Transfermarkt sont des sources complémentaires. Une indisponibilité de l'une d'elles n'invalide pas un instantané complet de FotMob.

**Mises à jour des transferts vs Player Updates**

Il s'agit de flux de travail distincts :

- `run` traite les transferts pour les joueurs déjà présents dans la sauvegarde. Si un club de destination est complet, ce transfert est ignoré ; les autres transferts sûrs de la même exécution peuvent toujours être appliqués.
- `players apply` applique les modifications d'attributs révisées. Les spécifications `update` pour les joueurs existants sont prises en charge.
- Les spécifications `create` pour les nouveaux joueurs restent chargeables et révisables.
  Leur application exige `players apply --allow-create`, un donneur d'apparence explicite
  et une source valide de `PlayerAppearance.bin`. Un donneur absent ou invalide rejette
  la spécification sans modifier les octets de la sauvegarde.
- Un effectif de destination complet exige aussi `--allow-overflow-release` ; seul un
  remplaçant avec un OVR positif complet de la sauvegarde peut être libéré. Les métadonnées
  de `Player.bin` ne remplacent pas l'OVR.

## Exécution locale

La configuration locale est prise en charge sous macOS, Linux et Windows via WSL. Python 3.10
ou supérieur est requis.

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
| `audit` | Auditer en lecture seule la sauvegarde et les métadonnées natives |
| `compare` | Comparer en lecture seule deux variantes CPK natives |

`run` gère uniquement les transferts : il ne charge ni n'applique jamais les Player Updates.
Pour combiner les deux flux de travail, exécutez d'abord la commande de transferts sur une
sauvegarde de sortie, puis lancez `players apply --in-place` sur cette même sauvegarde.

## Player Updates

Chaque Player Update validée est un fichier JSON conforme au schéma v2 par joueur sous `players/`. Elle enregistre une `operation` (`create` ou `update`), un cycle de vie (`active`, `upstreamed` ou `retired`), les révisions de base exactes dans `applies_to`, l'identité stable du joueur et la provenance UUID/profil Pes Retro Stats, les preuves citées et les données PES révisées. Les créations contiennent une proposition de profil complet du joueur et les données d'effectif cible. Les mises à jour de joueurs existants contiennent uniquement les valeurs compatibles différentes de la base vérifiée ; chaque modification consigne les valeurs littérales `from` et `to`.
Les enregistrements `create` restent pris en charge par le schéma pour révision. La mutation
via la CLI exige `players apply --allow-create` et des données d'apparence valides ; les appels
API directs restent désactivés par défaut. Si l'effectif est complet, ajoutez
`--allow-overflow-release` ; des métadonnées de sécurité absentes ou invalides laissent la
sauvegarde inchangée.
Les groupes pris en charge sont les compétences, la maîtrise des postes, le style de jeu, les aptitudes de joueur, les styles COM, la nationalité, les paramètres physiques/de base et le poste enregistré.
- Les valeurs de révision d'OVR générées sont des calculs déterministes basés sur la formule publiée de PES 2021. Elles constituent une aide à la parité et non une garantie indépendante du comportement du jeu en exécution ; les valeurs de capacité proposées nécessitent toujours une révision.
- Les brouillons de joueurs générés avec l'ancien identifiant de modèle OVR doivent être régénérés avant toute validation ; il n'y a pas de migration implicite de v1 vers v2.

### Méthode simple via une Issue

1. Ouvrez le [formulaire d'issue de mise à jour de joueur](.github/ISSUE_TEMPLATE/player-update.yml). Entrez le `Player name` exactement tel qu'il apparaît sur un profil canonique `Pes Retro Stats profile`, fournissez les URL de preuve et attendez qu'un mainteneur applique le label exact `generate-player-draft`.
2. Le workflow configuré récupère ce profil et ouvre une PR en brouillon contenant une proposition `players/<player-slug>.json` au format schéma v2. Il extrait du profil l'instantané source, l'identité, les paramètres physiques, les données de poste, les compétences, le style de jeu, les aptitudes et les styles COM.
3. Pour une création, seules les valeurs propres au jeu non disponibles à la source restent listées dans `draft.missing` : les ID PES et noms d'affichage, l'ID et le nom de l'équipe, l'ID de nationalité, la couleur de peau et la couleur des yeux. Un contributeur ou un mainteneur doit les renseigner. Pour une mise à jour, le générateur retrouve le joueur dans la base vérifiée et ne produit que les différences réelles `from`/`to`. Un poste source non pris en charge par PES 2021, tel que `RWB`, est omis plutôt que réassigné, y compris pour le changement de poste enregistré.
4. Un contributeur et un mainteneur vérifient chaque valeur générée comme une proposition non approuvée. L'intégration continue n'accepte une Player Update que si la PR ajoute ou modifie exactement un chemin JSON canonique de joueur et que le validateur sémantique partagé réussit.
5. La fusion de la PR constitue l'approbation humaine. Il n'y a pas d'indicateur `approved` distinct dans le fichier JSON.

Toute proposition générée échouera à la validation complète. Pour convertir les données au schéma v2 complet, supprimez les champs de brouillon `evidence.current_team`, `evidence.issue_number` et `evidence.issue_url` ; conservez les champs canoniques `evidence.profile_url`, `evidence.proof_urls` révisées et `evidence.effective_date` exacte ; et ajoutez une valeur `evidence.reason` non vide et révisée. Conservez l'UUID canonique dans `identity.pes_retro_stats_id` et uniquement les données de jeu revues dans `pes`. Pour une création, complétez également tous les champs requis listés dans `draft.missing`. Les ID PES des joueurs créés doivent être uniques et d'au moins `0x100000` (1 048 576) ; l'allocateur de propositions reste dans cette plage réservée.
Enfin, retirez les objets de premier niveau `source` et `draft`, qui sont des métadonnées de brouillon générées uniquement pour la révision, avant la validation finale.

### Méthode directe via une PR sur un seul fichier

Un contributeur expérimenté peut ignorer le brouillon généré et ouvrir directement une PR ajoutant ou modifiant exactement un fichier `players/<player-slug>.json` complet. Renseignez la provenance UUID/profil dans `identity` et `evidence`, les preuves, les valeurs PES revues, les références attendues, le cycle de vie et la révision de base exacte, puis exécutez `python run.py players validate` avant de demander une relecture. N'incluez pas les métadonnées `source` ou `draft` du brouillon généré. N'ajoutez aucune autre modification de code ou de documentation dans cette PR.

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

FotMob fournit l'historique principal des transferts et les métadonnées des effectifs. Les listes saisonnières de Wikipedia, les soumissions de transferts activées de SortitoutSI et les enregistrements datés et vérifiés de Transfermarkt complètent ou confirment les mouvements. Les profils de Pes Retro Stats fournissent des propositions dérivées de la source et non approuvées pour les brouillons de Player Update.

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
