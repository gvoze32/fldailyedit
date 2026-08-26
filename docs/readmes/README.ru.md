[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](../../README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Версия Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Лицензия: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../../LICENSE)

Обновляет файлы сохранения `EDIT00000000` для SP Football Life 2026 и
eFootball PES 2021, добавляя проверенные реальные трансферы и изменения
игроков после проверки.

> **Бета:** версии и совместимость сохранений ещё тестируются.
>
> **Создание новых игроков пока отключено.** Поддерживаются трансферы и
> проверенные изменения игроков, уже присутствующих в сохранении. Отсутствующие
> или неоднозначные игроки пропускаются. Если состав клуба назначения заполнен,
> по умолчанию освобождается безопасный резервный игрок с учётом роли; чтобы
> оставить состав без изменений, используйте `--no-allow-overflow-release`.

## Совместимость

[Входящая база](../../base/EDIT00000000) требует:

- **SP Football Life 2026 Update 2.2**
- **SmokePatch's National Squads Update**

Она несовместима с UML, старыми версиями FL26 и установками без обновления
национальных сборных. После установки начните новую карьеру в Master League или
Become a Legend.

## Установщик Windows

Установщик — самый простой вариант:

1. Скачайте и распакуйте [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip).
2. Закройте игру и выберите **Fast** или **Deep**.
3. Подтвердите папку Football Life и нажмите **Download and install**.

Установщик проверяет релиз, создаёт резервную копию и атомарно заменяет файл.
Чтобы обновить существующее сохранение, выберите **Update my local save**,
укажите файл и нажмите **Apply update**.

Установщик не подписан. Перед запуском проверьте `FLDailyEditInstaller.zip` по
файлу `FLDailyEditInstaller.zip.sha256` в [последнем релизе](https://github.com/gvoze32/fldailyedit/releases/tag/latest);
Windows SmartScreen может показать предупреждение.

Для ручной установки скачайте [Fast ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip)
или [Deep ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip).
Сделайте резервную копию, распакуйте `EDIT00000000` и скопируйте его в:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Для запуска по запросу или собственного списка клубов создайте fork репозитория
и используйте **Run workflow** на вкладке Actions.

## Что обновляется

- Трансферы, освобождения, аренды и возвращения из аренды
- Номера футболок, составы и игровые планы после изменений в командах
- Отчёты о трансферах и журналы аудита
- Ежедневные готовые сохранения через GitHub Actions

Программа проверяет текущий клуб игрока и не заменяет уже занятый номер футболки.

## Локальный запуск

Поддерживаются macOS, Linux и Windows через WSL. Требуется Python 3.10 или
новее.

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

## Основные команды

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

`run` применяет только трансферы. `players apply` — отдельный процесс. Чтобы
использовать оба, сначала запустите трансферы, затем примените Player Updates к
тому же сохранению. Для аудита, сравнения, журналов и восстановления используйте
`python run.py <command> --help`.

## Обновления игроков

Проверенные обновления хранятся в `players/`, по одному JSON-файлу на игрока.
Записи `update` для существующих игроков можно применять. Записи `create` для
новых игроков предназначены только для проверки и отклоняются командой
`players apply` с ошибкой `create_temporarily_unavailable`.

Чтобы предложить обновление:

1. Откройте [форму issue для обновления игрока](../../.github/ISSUE_TEMPLATE/player-update.yml).
2. Укажите имя точно как в профиле Pes Retro Stats и добавьте ссылки на доказательства.
3. Проверьте созданный черновик, выполните `python run.py players validate` и отправьте один JSON-файл игрока.

## Безопасность

- Сохранения проверяются до и после изменений.
- Локальные запуски создают резервные копии и используют проверенное атомарное шифрование.
- Блокировка процесса не допускает одновременную запись в один файл.
- Неполные исходные данные останавливают запуск; неоднозначные совпадения пропускаются.
- FotMob — основной источник; остальные только дополняют или подтверждают его данные.

## Разработка

```bash
pytest -v
```

## Лицензия

FL Daily Edit распространяется по [лицензии MIT](../../LICENSE).
