[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](../../README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Versión de Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Licencia: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../../LICENSE)

FL Daily Edit actualiza las plantillas de SP Football Life 2026 y eFootball PES 2021 mediante
la aplicación de transferencias reales a un archivo guardado `EDIT00000000`.

> **La creación de jugadores nuevos está habilitada por defecto para
> especificaciones revisadas en `players apply` cuando existe un donante válido
> de `PlayerAppearance.bin`. La opción positiva es `--allow-create`; use
> `--no-allow-create` para desactivar las creaciones de la CLI. La API directa
> permanece desactivada por defecto.**
>
> Las transferencias de jugadores que ya están en el guardado y las actualizaciones
> revisadas de jugadores existentes siguen siendo compatibles. Los jugadores faltantes
> se omiten. La liberación de overflow basada en roles está activa por defecto; use
> `--no-allow-overflow-release` para conservar una plantilla llena sin cambios.

> [!WARNING]
> **Aviso de beta:** FL Daily Edit, sus datos del repositorio y las versiones generadas todavía están en pruebas. Puede que no funcionen con todas las configuraciones del juego/guardado; algunas condiciones aún no son compatibles.

## Compatibilidad

La base incluida está destinada a **SP Football Life 2026**. Requiere:

- Football Life 26 Update 2.2
- SmokePatch's National Squads Update

No es compatible con UML, versiones anteriores de FL26 ni instalaciones sin
la actualización de selecciones nacionales. Se debe iniciar una nueva carrera de Liga Máster o Ser una Leyenda
después de instalar el archivo guardado.

La [base incluida](../../base/EDIT00000000) es el
[Gondowan's EDIT del 22 de agosto de 2026](https://www.reddit.com/r/SPFootballLife/comments/1vvh129/release_gondowans_edit_file_22082026_latest/).
Incluye transferencias de última hora del 22/08/2026 para todas las ligas,
cambios de valoración para más de 600 jugadores, ascensos y descensos entre
primera y segunda división, correcciones de altura y posición, cambios de nombres
y dorsales, cambios de entrenadores disponibles y alineaciones automáticas
ordenadas por los mejores jugadores. No crea jugadores ni agrega clubes
ascendidos desde terceras divisiones.

## Instalador para Windows

El instalador para Windows es la opción recomendada para principiantes. La interfaz del instalador actualmente solo está disponible en inglés. Las descargas validadas actuales son **exclusivamente para Football Life 2026 Update 2.2 + SmokePatch's National Squads Update**. La detección de eFootball PES 2021 vanilla está disponible, pero la instalación permanece desactivada hasta que se publique una base validada correspondiente.

1. Descargar y extraer [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip).
2. Cerrar el juego.
3. Elegir **Fast** o **Deep**. Son opciones separadas de cobertura de actualización y cada una muestra su hora de generación.
4. Confirmar la carpeta de Football Life 2026 detectada o usar **Browse** si es necesario.
5. Seleccionar **Download and install**. El instalador verifica la descarga, crea una copia de seguridad del archivo guardado actual y lo sustituye de forma atómica.

**Actualizar un archivo guardado existente mediante la GUI:** El instalador
también puede actualizar un `EDIT00000000` de diseño común seleccionado por el
usuario, en lugar de instalar una versión precompilada. Seleccione **Update my
local save**, elija una ubicación detectada o use **Browse**, elija **Fast** o
**Deep** y, después de revisarlo, seleccione **Apply update**. El asistente
valida el archivo antes de modificarlo, crea una copia de seguridad en el mismo
lugar y muestra el progreso, el resultado o los diagnósticos. La elegibilidad
local no depende de la etiqueta SPFL/PES/UML y esta ruta no descarga una versión
precompilada remota. Cuando esos catálogos externos opcionales de SPFL no están
disponibles, el comparador local recurre a los nombres de jugadores y equipos
integrados en el archivo guardado seleccionado, por lo que la ruta de
actualización local empaquetada puede ejecutarse sin ellos.

> [!WARNING]
> El ejecutable del instalador no está firmado, por lo que Windows SmartScreen puede mostrar una advertencia al ejecutarlo. Antes de continuar, verificar el `FLDailyEditInstaller.zip` descargado con el `FLDailyEditInstaller.zip.sha256` publicado en la [versión más reciente](https://github.com/gvoze32/fldailyedit/releases/tag/latest).
> Si Windows bloquea el instalador mediante Smart App Control, abra **Settings → Privacy & security → Windows Security → App & browser control → Smart App Control settings** y cámbielo a **Off**. Como alternativa, haga clic derecho en el archivo descargado, abra **Properties** y marque **Unblock** si está disponible.

Para realizar una instalación manual sin el instalador, descargar el [ZIP público Fast](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip) o el [ZIP público Deep](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip). Extraer `EDIT00000000`, crear una copia de seguridad del archivo guardado actual y copiar el archivo extraído en:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Para ejecutar el proceso cuando se necesite o usar una lista personalizada de clubes, se debe bifurcar el repositorio y usar **Run workflow** en la pestaña Actions.

## Qué actualiza

- Transferencias, liberaciones, préstamos y regresos de préstamos
- Dorsales disponibles a partir de los datos de plantillas de FotMob
- Identidades de jugadores verificadas con la plantilla actual de FL26
- Alineaciones y planes de juego afectados por cambios en las plantillas
- Informes de transferencias y registros de auditoría JSON Lines
- Archivos guardados precompilados a diario mediante GitHub Actions
- Creaciones de jugadores y correcciones de atributos revisadas mediante comandos explícitos de Player Update

El actualizador no sobrescribe un dorsal que ya use otro integrante de la
plantilla. También verifica el club actual del jugador antes de aplicar un movimiento.

## Hoja de ruta / Completada por ahora

Todos los elementos actuales de la hoja de ruta están terminados. Esperamos la próxima idea útil.

## Seguridad y limitaciones

- Las ejecuciones locales crean copias de seguridad rotativas y usan cifrado atómico verificado.
- Los archivos guardados se validan antes y después de los cambios en las plantillas.
- Un bloqueo de proceso evita que dos ejecuciones escriban al mismo tiempo en la misma salida.
- Las instantáneas incompletas de FotMob interrumpen la ejecución en lugar de producir un archivo guardado parcial.
- Se omiten las coincidencias ambiguas de jugadores y las discrepancias del club de origen.
- Las plantillas de destino llenas usan liberación de overflow basada en roles por
  defecto. El primer equipo y el banquillo de la jornada están protegidos; se
  prefiere la reserva nativa más profunda y los jugadores creados se protegen cuando
  existe un candidato nativo. La habilidad/OVR nunca se usa; use
  `--no-allow-overflow-release` para desactivar esta conducta.
- Wikipedia, Sortitoutsi y Transfermarkt son fuentes complementarias. Una interrupción en una
  de estas fuentes no invalida una instantánea completa de FotMob.

**Actualizaciones de transferencias frente a Player Updates**

Son flujos de trabajo separados:

- `run` procesa transferencias para jugadores que ya existen en el archivo guardado. Si un club
  de destino está lleno, se libera el candidato de overflow basado en roles por defecto; use
  `--no-allow-overflow-release` para omitir esa transferencia.
- `players apply` aplica cambios de atributos revisados. Se admiten especificaciones `update`
  de jugadores existentes.
- Las especificaciones `create` de nuevos jugadores siguen siendo cargables y revisables.
  `players apply` las intenta por defecto con un donante de apariencia explícito y una fuente
  válida de `PlayerAppearance.bin`. Use `--no-allow-create` para desactivarlas; un donante
  ausente o inválido rechaza la especificación sin cambiar los bytes del guardado.

- Los workflows de sincronización Fast y Deep usan temporalmente `--no-allow-create` y `--no-allow-overflow-release`; la sincronización automática no aplica especificaciones `create`. El comando local `players apply` mantiene su comportamiento normal.
- Para `players apply`, una plantilla llena usa overflow basado en roles por defecto. La opción
  positiva es `--allow-overflow-release`; use `--no-allow-overflow-release` para omitirlo.
  No se exige un OVR positivo.

## Ejecución local

La configuración local es compatible con macOS, Linux y Windows mediante WSL. Se requiere Python 3.10
o una versión posterior.

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

| Comando | Propósito |
|---|---|
| `run` | Aplicar únicamente transferencias verificadas |
| `players validate` | Validar todas las Player Updates con la base intacta |
| `players apply` | Aplicar explícitamente las Player Updates revisadas a un archivo guardado |
| `base-audit` | Comprobar Player Updates activos, destinos y parent de préstamos contra una base |
| `base-refresh` | Verificar y, opcionalmente, promover una base local o HTTPS |
| `usage-import` | Combinar datos CSV de uso de jugadores en la política de release |
| `players apply --preflight` | Mostrar destinos create revisados y datos de seguridad sin escribir |
| `log` | Mostrar las transferencias aplicadas recientemente |
| `inspect` | Inspeccionar equipos, cantidades de jugadores y desplazamientos del archivo guardado |
| `validate` | Verificar las inscripciones en las plantillas y las asignaciones de los planes de juego |
| `repair` | Reparar una base heredada mediante archivos guardados de referencia |
| `audit` | Auditar en modo solo lectura el guardado y los metadatos nativos |
| `compare` | Comparar en modo solo lectura dos variantes CPK nativas |

`run` solo gestiona transferencias: nunca carga ni aplica Player Updates. Para combinar
ambos flujos de trabajo, primero se debe ejecutar el comando de transferencias sobre un archivo guardado de salida y luego ejecutar
`players apply --in-place` sobre ese mismo archivo guardado.

## Actualizaciones de jugadores

Cada Player Update revisada es un archivo JSON completo con la versión 2 del esquema por
jugador en `players/`. Registra una `operation` (`create` o `update`), un
ciclo de vida (`active`, `integrated` o `superseded`), las revisiones base exactas en `applies_to`,
la identidad estable del jugador y la procedencia del UUID/perfil de Pes Retro Stats,
evidencia citada y datos de PES revisados. Las actualizaciones de creación contienen una propuesta de
registro completo del jugador y datos de la plantilla de destino. Las actualizaciones de jugadores existentes
contienen únicamente valores compatibles que difieren de la base verificada; cada cambio
registra valores literales `from` y `to`.

> **Nota del ciclo de vida:** `superseded` es un estado del Player Update, no de la carrera del jugador. Significa que la actualización ya no se aplica a la revisión base seleccionada.
Los registros `create` siguen siendo compatibles con el esquema para su revisión.
La mutación desde la CLI requiere `players apply --allow-create` y datos de
apariencia válidos; la API directa permanece desactivada por defecto. Si la
plantilla está llena, añada `--allow-overflow-release`; los metadatos de seguridad
ausentes o inválidos dejan el guardado sin cambios.
Los grupos de actualización compatibles son habilidades, dominio de posiciones, estilo de juego,
habilidades del jugador, estilos COM, nacionalidad, configuración física/básica y
posición registrada.
- Los valores de revisión de OVR generados son cálculos deterministas basados en la
  fórmula publicada de PES 2021. Son una ayuda de paridad, no una garantía independiente
  de la ejecución del juego; los valores de habilidad propuestos aún requieren revisión.
- Los borradores de jugadores generados con el identificador de modelo OVR anterior deben
  regenerarse antes de la validación; no hay migración implícita de v1 a v2.

### Flujo sencillo mediante un issue

1. Abrir el [formulario de issue para actualizar jugadores](../../.github/ISSUE_TEMPLATE/player-update.yml).
   Ingresar el `Player name` exactamente como aparece en un `Pes Retro Stats
   profile` canónico, proporcionar las URL de prueba y esperar a que un responsable del mantenimiento aplique la
   etiqueta exacta `generate-player-draft`.
2. El flujo de trabajo configurado del generador obtiene ese perfil y abre un PR en borrador
   que contiene una propuesta `players/<player-slug>.json` con la versión 2 del esquema. A partir del perfil,
   deriva la instantánea de origen, la identidad, la configuración física, los datos de posición,
   las habilidades, el estilo de juego, las habilidades del jugador y los estilos COM.
3. Para una creación, solo los valores locales del juego que no están disponibles en la fuente permanecen
   enumerados en `draft.missing`: los ID de PES y los nombres para mostrar de la identidad y el
   jugador, el ID y el nombre del equipo, el ID de nacionalidad, el color de piel y el color de iris. Un
   colaborador o responsable del mantenimiento debe proporcionarlos. Para una actualización, el generador
   encuentra al jugador en la base verificada y emite únicamente diferencias reales `from`/`to`.
   Una posición de origen no compatible con PES 2021, como `RWB`, se
   omite en lugar de reasignarse, incluso en el cambio de posición registrada.
4. Un colaborador y un responsable del mantenimiento revisan cada valor generado como una propuesta
   no aprobada. La integración continua acepta una Player Update únicamente cuando el PR agrega o modifica
   exactamente una ruta JSON canónica de jugador y el validador semántico compartido
   finaliza correctamente.
5. La fusión del PR sigue siendo el estado de aprobación humana. No existe una marca
   `approved` separada en el archivo JSON.

Se espera que toda propuesta generada falle la validación de archivos completos. Para
convertir su evidencia generada al esquema v2 completo, se deben eliminar los campos exclusivos del borrador
`evidence.current_team`, `evidence.issue_number` y `evidence.issue_url`;
conservar `evidence.profile_url`, `evidence.proof_urls` revisadas y
`evidence.effective_date` canónicos; y agregar un valor `evidence.reason` revisado
y no vacío. Se debe conservar el UUID canónico del perfil como
`identity.pes_retro_stats_id` y únicamente los valores de jugabilidad revisados en `pes`.
Para una creación, también se deben completar todos los campos locales del juego indicados por `draft.missing`.
Los ID de PES de jugadores creados deben ser únicos y de al menos `0x100000` (1,048,576);
el asignador de propuestas se mantiene en ese rango reservado.
Luego se deben eliminar los objetos de nivel superior `source` y `draft`, que son metadatos de borrador generado
exclusivos de la revisión, antes de la validación de archivos completos.

### Flujo directo mediante un PR de un solo archivo

Un colaborador con experiencia puede omitir el borrador generado a partir de un issue y abrir directamente un
PR que agregue o modifique exactamente un archivo completo
`players/<player-slug>.json`. Se debe proporcionar la procedencia canónica del UUID/perfil
en `identity` y `evidence`, pruebas citadas, valores de PES revisados, bases de referencia esperadas para la actualización,
el ciclo de vida y la revisión base exacta; luego se debe ejecutar
`python run.py players validate` antes de solicitar una revisión. No se deben incluir los
metadatos de nivel superior `source` o `draft` del borrador generado. No se deben incluir otros cambios de código o
documentación en ese PR.

La aplicación siempre se realiza mediante un comando explícito y requiere la revisión exacta de
`data/base_manifest.json`; una discrepancia de revisión provoca un fallo antes de descifrar el
archivo guardado de destino.

### Ciclo de vida de las revisiones

Cuando cambia la base oficial, se deben actualizar juntos `base/EDIT00000000` y
`data/base_manifest.json`. Las Player Updates históricas deben conservarse en
`players/`; no se deben eliminar solo porque cambió la revisión. Una Player Update activa
cuya lista `applies_to` no contiene la nueva revisión queda inactiva: la validación informa
`needs_review` y la aplicación la omite. Después de revisarla, se debe agregar la nueva revisión únicamente cuando la
Player Update todavía corresponda, marcarla como `integrated` cuando la base oficial incluya su cambio
o marcarla como `superseded` cuando ya no corresponda.

Opciones comunes de `run`:

| Opción | Propósito |
|---|---|
| `--deep` | Obtener todos los clubes de FotMob indexados localmente |
| `--club "Chelsea,Arsenal"` | Limitar la ejecución a los clubes seleccionados |
| `--window auto` | Reproducir todas las transferencias con fecha disponibles hasta hoy |
| `--window summer` | Usar el intervalo más reciente del 1 de junio al 30 de septiembre |
| `--window winter` | Usar el intervalo de enero a febrero del año seleccionado |
| `--since YYYY-MM-DD` | Establecer manualmente la fecha límite inferior |
| `--dry-run` | Planificar los cambios sin escribir un archivo guardado |
| `--from-base` | Comenzar desde `base/EDIT00000000` |
| `--fotmob-only` | Ejecutar sin fuentes de transferencias complementarias |
| `--release-policy PATH` | Cargar jugadores protegidos por club y contadores de uso offline |
| `--numbers-only` | Corregir los dorsales actuales usando solo los datos de plantillas de FotMob |

Sin `--from-base`, una ejecución normal continúa desde la última salida verificada.
Esto evita que las transferencias desaparezcan cuando una ejecución programada posterior vuelve a leer el
historial acumulado.

## Fuentes de transferencias

FotMob proporciona el historial principal de transferencias y los metadatos de las plantillas. Las listas de temporada de
Wikipedia, los envíos de transferencias habilitados de SortitoutSI y los registros verificados con fecha de
Transfermarkt complementan o confirman las rutas de transferencia. Los perfiles de Pes Retro Stats
proporcionan propuestas derivadas de la fuente y no aprobadas para los borradores de Player Update.

Los registros de distintas fuentes se concilian sin descartar sus fechas,
ID, citas ni enlaces de prueba. Los eventos sin fecha, con vigencia futura, contradictorios o
ambiguos no pueden actualizar el archivo guardado por sí solos.

La búsqueda de coincidencias de jugadores comienza con la plantilla de origen y usa la plantilla de destino
como alternativa idempotente. La posición, la nacionalidad y la edad se tienen en cuenta únicamente
cuando esa información está disponible.

## Desarrollo

Ejecutar el conjunto de pruebas con:

```bash
pytest -v
```

El conjunto abarca el análisis y la validación de archivos guardados, la conciliación de transferencias, la planificación de
plantillas, el historial de préstamos, la búsqueda de coincidencias de jugadores, los límites de las plantillas, los informes, las copias de seguridad y
el bloqueo de procesos.

## Licencia

FL Daily Edit está disponible bajo la [Licencia MIT](../../LICENSE).
