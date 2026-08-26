[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](../../README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Versión de Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Licencia: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../../LICENSE)

Actualiza los archivos `EDIT00000000` de SP Football Life 2026 y eFootball PES
2021 con transferencias reales verificadas y actualizaciones de jugadores revisadas.

> **Beta:** Las versiones y la compatibilidad con los archivos guardados aún están
> en pruebas.
>
> **La creación de jugadores nuevos está desactivada por ahora.** Se admiten las
> transferencias y actualizaciones revisadas de jugadores existentes. Los jugadores
> ausentes o ambiguos se omiten. Si una plantilla de destino está llena, se libera
> por defecto un suplente seguro según su rol; usa
> `--no-allow-overflow-release` para dejarla sin cambios.

## Compatibilidad

La [base incluida](../../base/EDIT00000000) requiere:

- **SP Football Life 2026 Update 2.2**
- **SmokePatch's National Squads Update**

No es compatible con UML, versiones anteriores de FL26 ni instalaciones sin la
actualización de selecciones nacionales. Inicia una nueva carrera de Liga Máster
o Ser una Leyenda después de instalarla.

## Instalador para Windows

El instalador es la opción más sencilla:

1. Descarga y extrae [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip).
2. Cierra el juego y elige **Fast** o **Deep**.
3. Confirma la carpeta de Football Life y selecciona **Download and install**.

El instalador verifica la versión, crea una copia de seguridad y reemplaza el
archivo de forma atómica. Para actualizar un archivo existente, elige **Update
my local save**, selecciona el archivo y pulsa **Apply update**.

El instalador no está firmado. Verifica `FLDailyEditInstaller.zip` con el archivo
`FLDailyEditInstaller.zip.sha256` publicado en la [última versión](https://github.com/gvoze32/fldailyedit/releases/tag/latest)
antes de ejecutarlo; Windows SmartScreen puede mostrar una advertencia.

Para instalar manualmente, descarga el [ZIP Fast](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip)
o el [ZIP Deep](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip).
Haz una copia de seguridad, extrae `EDIT00000000` y cópialo en:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Para una ejecución bajo demanda o una lista personalizada de clubes, haz un fork
del repositorio y usa **Run workflow** en la pestaña Actions.

## Qué actualiza

- Transferencias, liberaciones, préstamos y regresos de préstamos
- Dorsales, alineaciones y planes de juego afectados por cambios de plantilla
- Informes de transferencias y registros de auditoría
- Archivos guardados precompilados a diario mediante GitHub Actions

El actualizador comprueba el club actual del jugador y no sobrescribe un dorsal
que ya use otro integrante de la plantilla.

## Ejecución local

Compatible con macOS, Linux y Windows mediante WSL. Se requiere Python 3.10 o
posterior.

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

## Comandos comunes

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

`run` solo aplica transferencias. `players apply` es un flujo separado. Para
combinar ambos, ejecuta primero las transferencias y después aplica las Player
Updates al mismo archivo. Usa `python run.py <command> --help` para las
herramientas de auditoría, comparación, registro y reparación.

## Actualizaciones de jugadores

Las actualizaciones revisadas se guardan como un archivo JSON por jugador en
`players/`. Los registros `update` de jugadores existentes se pueden aplicar.
Los registros `create` de jugadores nuevos son solo para revisión y `players
apply` los rechaza con `create_temporarily_unavailable`.

Para proponer una actualización:

1. Abre el [formulario de issue para actualizar jugadores](../../.github/ISSUE_TEMPLATE/player-update.yml).
2. Escribe el nombre exactamente como aparece en el perfil de Pes Retro Stats e incluye URL de pruebas.
3. Revisa el borrador generado, ejecuta `python run.py players validate` y envía un solo archivo JSON de jugador.

## Seguridad

- Los archivos guardados se validan antes y después de los cambios.
- Las ejecuciones locales crean copias de seguridad rotativas y usan cifrado atómico verificado.
- Un bloqueo evita escrituras simultáneas en la misma salida.
- Los datos incompletos detienen la ejecución; las coincidencias ambiguas se omiten.
- FotMob es la fuente principal; las demás solo complementan o confirman sus datos.

## Desarrollo

```bash
pytest -v
```

## Licencia

FL Daily Edit está disponible bajo la [Licencia MIT](../../LICENSE).
