[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](../../README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../../LICENSE)

使用经过验证的真实转会和已审核的球员更新，更新 SP Football Life 2026
与 eFootball PES 2021 的 `EDIT00000000` 存档。

> **Beta：** 版本和存档兼容性仍在测试中。
>
> **新建球员功能暂时关闭。** 已存在于存档中的球员仍可进行转会和审核后的
> 更新。找不到或匹配不明确的球员会被跳过。目标阵容已满时，默认会按角色
> 释放安全的替补；如需保持阵容不变，请使用
> `--no-allow-overflow-release`。

## 兼容性

[随附的基础存档](../../base/EDIT00000000)需要：

- **SP Football Life 2026 Update 2.2**
- **SmokePatch's National Squads Update**

不兼容 UML、旧版 FL26，或未安装国家队阵容更新的版本。安装后请开始新的
Master League 或 Become a Legend 生涯。

## Windows 安装程序

安装程序是最简单的方式：

1. 下载并解压 [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip)。
2. 关闭游戏并选择 **Fast** 或 **Deep**。
3. 确认 Football Life 文件夹，然后选择 **Download and install**。

安装程序会验证版本、备份当前存档，并以原子方式替换文件。更新已有存档时，
选择 **Update my local save**，选择存档，然后点击 **Apply update**。

安装程序未签名。运行前，请使用[最新版本](https://github.com/gvoze32/fldailyedit/releases/tag/latest)
中的 `FLDailyEditInstaller.zip.sha256` 验证 `FLDailyEditInstaller.zip`；
Windows SmartScreen 可能显示警告。

手动安装时，下载 [Fast ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip)
或 [Deep ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip)。
先备份存档，解压 `EDIT00000000`，然后复制到：

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

如需按需运行或使用自定义俱乐部列表，请 fork 仓库并在 Actions 中使用
**Run workflow**。

## 更新内容

- 转会、解约、租借和租借回归
- 因阵容变化而调整的球衣号码、阵容和比赛计划
- 转会报告和审计日志
- 通过 GitHub Actions 每日生成的预构建存档

程序会检查球员当前俱乐部，也不会覆盖其他队员已经使用的球衣号码。

干净的 PES21 存档可能会在空阵容槽中保留球衣号码。这些情况会显示为非阻塞警告，
不会阻止本地更新。

## 本地运行

支持 macOS、Linux，以及通过 WSL 使用的 Windows。需要 Python 3.10 或更高版本。

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

## 常用命令

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

`run` 只处理转会，`players apply` 是独立流程。如需同时使用，先运行转会，
再对同一个存档应用 Player Updates。审计、比较、日志和修复工具请使用
`python run.py <command> --help`。

## 球员更新

已审核的更新存放在 `players/` 中，每名球员一个 JSON 文件。现有球员的
`update` 记录可以应用。新球员的 `create` 记录仅用于审核，`players apply`
当前会以 `create_temporarily_unavailable` 拒绝它们。

如需提交更新：

1. 打开[球员更新 Issue 表单](../../.github/ISSUE_TEMPLATE/player-update.yml)。
2. 按 Pes Retro Stats 资料中的写法填写球员姓名，并附上证明链接。
3. 检查生成的草稿，运行 `python run.py players validate`，并提交一个球员 JSON 文件。

## 安全性

- 存档会在修改前后进行验证。
- 本地运行会创建滚动备份，并使用经过验证的原子加密。
- 进程锁可防止同时写入同一个输出文件。
- 数据不完整时会停止运行；匹配不明确的球员会被跳过。
- FotMob 是主要来源，其他来源仅用于补充或确认。

## 开发

```bash
pytest -v
```

## 许可证

FL Daily Edit 采用 [MIT 许可证](../../LICENSE)。
