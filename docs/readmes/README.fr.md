[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](../../README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Version Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Licence : MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../../LICENSE)

Mettez à jour les fichiers `EDIT00000000` de SP Football Life 2026 et
eFootball PES 2021 avec des transferts réels vérifiés et des mises à jour de
joueurs révisées.

> **Bêta :** les versions et la compatibilité des sauvegardes sont encore testées.
>
> **La création de nouveaux joueurs est désactivée pour le moment.** Les transferts
> et les mises à jour révisées de joueurs existants restent disponibles. Les joueurs
> absents ou ambigus sont ignorés. Si l’effectif de destination est complet, un
> remplaçant sûr est libéré selon son rôle par défaut ; utilisez
> `--no-allow-overflow-release` pour ne rien modifier.

## Compatibilité

La [base incluse](../../base/EDIT00000000) nécessite :

- **SP Football Life 2026 Update 2.2**
- **SmokePatch's National Squads Update**

Elle n’est pas compatible avec UML, les anciennes versions de FL26 ou les
installations sans la mise à jour des équipes nationales. Commencez une nouvelle
carrière de Ligue des Masters ou de Vers une légende après l’installation.

## Programme d’installation Windows

Le programme d’installation est la solution la plus simple :

1. Téléchargez et extrayez [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip).
2. Fermez le jeu et choisissez **Fast** ou **Deep**.
3. Confirmez le dossier Football Life, puis sélectionnez **Download and install**.

Le programme vérifie la version, sauvegarde le fichier actuel et le remplace de
façon atomique. Pour mettre à jour une sauvegarde existante, choisissez **Update
my local save**, sélectionnez-la, puis cliquez sur **Apply update**.

Le programme n’est pas signé. Vérifiez `FLDailyEditInstaller.zip` avec le fichier
`FLDailyEditInstaller.zip.sha256` publié dans la [dernière version](https://github.com/gvoze32/fldailyedit/releases/tag/latest)
avant de l’exécuter ; Windows SmartScreen peut afficher un avertissement.

Pour une installation manuelle, téléchargez le [ZIP Fast](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip)
ou le [ZIP Deep](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip).
Sauvegardez votre fichier, extrayez `EDIT00000000` et copiez-le vers :

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Pour une exécution à la demande ou une liste de clubs personnalisée, forkez le
dépôt et utilisez **Run workflow** dans l’onglet Actions.

## Ce qui est mis à jour

- Transferts, libérations, prêts et retours de prêt
- Numéros de maillot, compositions et plans de jeu touchés par les changements d’effectif
- Rapports de transferts et journaux d’audit
- Sauvegardes précompilées quotidiennement par GitHub Actions

Le programme vérifie le club actuel du joueur et n’écrase jamais un numéro de
maillot déjà utilisé.

## Exécution locale

Compatible avec macOS, Linux et Windows via WSL. Python 3.10 ou une version plus
récente est requis.

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

`run` applique uniquement les transferts. `players apply` est un flux séparé.
Pour utiliser les deux, lancez d’abord les transferts, puis appliquez les Player
Updates au même fichier. Utilisez `python run.py <command> --help` pour les
outils d’audit, de comparaison, de journalisation et de réparation.

## Mises à jour des joueurs

Les mises à jour révisées sont stockées dans `players/`, avec un fichier JSON par
joueur. Les entrées `update` de joueurs existants peuvent être appliquées. Les
entrées `create` de nouveaux joueurs servent uniquement à la révision et sont
refusées par `players apply` avec `create_temporarily_unavailable`.

Pour proposer une mise à jour :

1. Ouvrez le [formulaire d’issue de mise à jour de joueur](../../.github/ISSUE_TEMPLATE/player-update.yml).
2. Saisissez le nom exactement comme dans le profil Pes Retro Stats et ajoutez les URL justificatives.
3. Vérifiez le brouillon, exécutez `python run.py players validate` et envoyez un seul fichier JSON de joueur.

## Sécurité

- Les sauvegardes sont validées avant et après les changements.
- Les exécutions locales créent des sauvegardes tournantes et utilisent un chiffrement atomique vérifié.
- Un verrou empêche les écritures simultanées dans la même sortie.
- Les données incomplètes interrompent l’exécution ; les correspondances ambiguës sont ignorées.
- FotMob est la source principale ; les autres sources la complètent ou la confirment.

## Développement

```bash
pytest -v
```

## Licence

FL Daily Edit est disponible sous [licence MIT](../../LICENSE).
