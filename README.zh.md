[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

FL Daily Edit 通过将现实世界中的转会应用到 `EDIT00000000` 存档文件，更新 SP Football Life 2026 和 eFootball PES 2021 的阵容。

> **新球员创建采用显式 opt-in。直接 API 调用默认保持禁用；
> `players apply --allow-create` 必须使用经过验证的 `PlayerAppearance.bin` donor。**
>
> 仍支持存档中已有球员的转会以及现有球员的已审核更新。缺失的球员将被跳过；若目标球队阵容已满，默认会跳过该操作，而不是解约现有球员。

## 兼容性

随附的基础存档适用于 **SP Football Life 2026**。需要：

- Football Life 26 Update 2.2
- SmokePatch's National Squads Update

本工具不兼容 UML、旧版 FL26，或未安装国家队更新的版本。安装存档后，请开始新的大师联赛或一球成名生涯。

[随附的基础存档](base/EDIT00000000)为 2026 年 7 月 27 日发布的 [Gondowan's Mid-Summer EDIT](https://www.reddit.com/r/SPFootballLife/comments/1v7z782/release_gondowans_midsummer_edit_file_more_than/)。其中包含 500 多笔转会，以及更新后的评分、位置、球衣号码、租借回归、主教练、首发阵容和升降级变动。该存档不会创建球员，也不会添加从第三级别联赛升级的俱乐部。

## Windows 安装程序

Windows 安装程序是面向新手的推荐方式。安装程序界面目前仅提供英语版本。当前经过验证的下载内容**仅适用于 Football Life 2026 Update 2.2 + SmokePatch's National Squads Update**。程序可以检测原版 eFootball PES 2021，但在发布匹配且经过验证的基础存档前，安装功能将保持禁用。

1. 下载并解压 [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip)。
2. 关闭游戏。
3. 选择 **Fast** 或 **Deep**。二者是独立的更新范围选项，并分别显示生成时间。
4. 确认检测到的 Football Life 2026 文件夹；如有需要，请使用 **Browse**。
5. 选择 **Download and install**。安装程序会验证下载内容、备份当前存档，并以原子方式替换存档。

**通过 GUI 更新现有存档：** 安装程序也可以更新用户选择的通用布局
`EDIT00000000`，而不是安装预制版本。选择 **Update my local save**，选择
检测到的位置或使用 **Browse**，选择 **Fast** 或 **Deep**，检查后选择
**Apply update**。向导会在修改前验证存档，创建原地备份，并显示进度、结果
或诊断信息。本地更新资格不取决于 SPFL/PES/UML 标签，此路径不会下载远程
预制版本。当这些可选的外部 SPFL 目录不可用时，本地匹配器将回退使用所选
存档中内置的球员和球队名称，因此打包的本地更新路径无需这些目录即可运行。

> [!WARNING]
> 初始安装程序可执行文件尚未签名，因此运行时 Windows SmartScreen 可能会显示警告。继续前，请将下载的 `FLDailyEditInstaller.zip` 与[最新版本](https://github.com/gvoze32/fldailyedit/releases/tag/latest)中发布的 `FLDailyEditInstaller.zip.sha256` 进行比较。
> 如果 Windows 通过 Smart App Control 阻止安装程序，请打开 **Settings → Privacy & security → Windows Security → App & browser control → Smart App Control settings**，然后切换为 **Off**。或者右键单击下载的文件，打开 **Properties**，如果有 **Unblock** 选项请勾选。

如需不使用安装程序手动安装，请下载公开的 [Fast 发布 ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip) 或 [Deep 发布 ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip)。解压 `EDIT00000000`，备份当前存档，然后将解压出的文件复制到：

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

如需按需运行或使用自定义俱乐部列表，请复刻仓库，然后在 Actions 选项卡中使用 **Run workflow**。

## 更新内容

- 转会、解约、租借和租借回归
- 根据 FotMob 阵容数据分配可用的球衣号码
- 对照当前 FL26 球员名单检查球员身份
- 处理受阵容变动影响的首发阵容和比赛计划
- 生成转会报告和 JSON Lines 审计日志
- 通过 GitHub Actions 每日提供预构建存档
- 通过显式 Player Update 命令处理经审核的球员创建和属性修正

更新程序不会覆盖已被队内其他球员使用的球衣号码。在执行转会前，它还会检查球员当前所属俱乐部。

## 路线图 / 当前已完成

当前路线图中的所有项目都已完成。我们正在等待下一个有价值的想法。

## 安全性与限制

- 本地运行会创建滚动备份，并使用具有原子性且经过验证的加密流程。
- 更改阵容前后都会验证存档。
- 进程锁可防止两个进程同时写入同一输出。
- FotMob 快照不完整时，运行会中止，而不会生成残缺存档。
- 球员匹配有歧义、来源俱乐部不符时，会跳过相应操作。
- 目标球队阵容已满时默认跳过；转会更新程序绝不会自动解约现有球员。
- `--allow-overflow-release` 是一个独立的、仅用于转会的显式选项。它需要完整的球员位置和 OVR 元数据，并可以解约安全的候选球员以腾出名额。若元数据不完整，运行将安全关闭（fail-closed）。
- Wikipedia、Sortitoutsi 和 Transfermarkt 是补充来源。其中任一来源中断，都不会使完整的 FotMob 快照失效。

**转会更新 vs. 球员更新 (Player Updates)**

这是两个独立的工作流：

- `run` 仅处理存档中已有球员的转会。若目标俱乐部已满，则跳过该转会；同一运行中的其他安全转会仍可正常应用。
- `players apply` 应用经审核的属性变更。支持现有球员的 `update` 规范。
- 新球员的 `create` 规范仍可加载和审核。应用它需要
  `players apply --allow-create`、明确的外观 donor 和有效的
  `PlayerAppearance.bin` 来源。donor 缺失或无效时拒绝该规范且不改变存档字节。
- 目标球队阵容已满时还必须使用 `--allow-overflow-release`；只有存档中
  OVR 完整且为正的 reserve 才能被释放。`Player.bin` 元数据不能替代 OVR。

## 本地运行

支持在 macOS、Linux 和通过 WSL 使用的 Windows 上进行本地设置。需要 Python 3.10 或更高版本。

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

| 命令 | 用途 |
|---|---|
| `run` | 仅应用已验证的转会 |
| `players validate` | 对照原始基础存档验证所有 Player Update |
| `players apply` | 将经审核的 Player Update 显式应用到一个存档 |
| `log` | 显示最近应用的转会 |
| `inspect` | 检查球队、球员人数和存档偏移量 |
| `validate` | 检查阵容注册和比赛计划映射 |
| `repair` | 使用参考存档修复旧版基础存档 |
| `audit` | 以只读方式审计存档与原生 Player/Team 元数据 |
| `compare` | 以只读方式比较两个原生 CPK 元数据变体 |

`run` 仅处理转会：它绝不会加载或应用 Player Update。要结合使用这两个工作流，请先对输出存档运行转会命令，然后对同一存档运行 `players apply --in-place`。

## 球员更新

每个经审核的 Player Update 都是 `players/` 下每名球员一个的完整 schema-version-2 JSON 文件。文件记录 `operation`（`create` 或 `update`）、生命周期（`active`、`upstreamed` 或 `retired`）、精确的 `applies_to` 基础版本、稳定的球员身份信息和 Pes Retro Stats UUID/资料来源、带引用的证据，以及经审核的 PES 数据。创建更新包含拟定的完整球员记录和目标球队阵容数据。现有球员更新仅包含与已验证基础存档不同的受支持值；每项更改都会记录字面量 `from` 和 `to` 值。
`create` 记录继续受 schema 支持以供审核。CLI 变更必须使用
`players apply --allow-create` 和有效外观数据；直接 API 调用默认保持禁用。
若目标阵容已满，请同时使用 `--allow-overflow-release`；缺失或无效的安全
元数据会保持存档不变。
受支持的更新分组包括能力值、位置熟练度、比赛风格、球员技能、COM 风格、国籍、身体/基本设置和注册位置。
- 生成的 OVR 审核值是基于已公布 PES 2021 公式进行的确定性计算。它们是平性辅助工具，并非游戏运行时环境的独立保证；拟定的能力值仍需审核。
- 使用旧版 OVR 模型标识符生成的球员草稿必须在验证前重新生成；不支持从 v1 到 v2 的隐式迁移。

### 简易 Issue 流程

1. 打开[球员更新 Issue 表单](.github/ISSUE_TEMPLATE/player-update.yml)。严格按照一个规范的 `Pes Retro Stats profile` 中显示的内容填写 `Player name`，提供证明 URL，然后等待维护者添加精确的 `generate-player-draft` 标签。
2. 配置好的生成器工作流会获取该资料，并创建草稿 PR，其中包含一个 schema-version-2 的 `players/<player-slug>.json` 提案。它会从资料中推导来源快照、身份信息、身体设置、位置数据、能力值、比赛风格、技能和 COM 风格。
3. 对于创建操作，只有来源无法提供的游戏本地值会继续列在 `draft.missing` 中：身份和球员的 PES ID 与球衣印字名、球队 ID 与名称、国籍 ID、肤色和虹膜颜色。贡献者或维护者必须补全这些值。对于更新操作，生成器会在已验证的基础存档中解析球员，并仅输出实际的 `from`/`to` 差异。对于 PES 2021 不支持的来源位置（例如 `RWB`），将直接省略而不是重新映射，注册位置更改中也同样如此。
4. 贡献者和维护者会将所有生成值作为尚未批准的提案逐一审核。仅当 PR 恰好新增或修改一个规范的球员 JSON 路径，且共享语义验证器成功通过时，CI 才会接受 Player Update。
5. 合并 PR 仍代表人工批准状态。JSON 文件中没有单独的 `approved` 标志。

每个生成的提案按预期都无法通过完整文件验证。要将其中生成的证据转换为完整的 schema v2，请删除仅供草稿使用的 `evidence.current_team`、`evidence.issue_number` 和 `evidence.issue_url` 字段；保留规范的 `evidence.profile_url`、经审核的 `evidence.proof_urls` 和 `evidence.effective_date`；再添加经审核且非空的 `evidence.reason`。将规范资料 UUID 持久保存为 `identity.pes_retro_stats_id`，并且只在 `pes` 中保留经审核的游戏数值。对于创建操作，还需补全 `draft.missing` 中列出的所有游戏本地字段。创建球员的 PES ID 必须唯一且至少为 `0x100000`（1,048,576）；提案分配器会保留在该预留范围内。
随后删除顶层的 `source` 和 `draft` 对象；它们是仅供审核使用的生成草稿元数据。完成后再进行完整验证。

### 单文件 PR 直接流程

高级贡献者可以跳过由 Issue 生成的草稿，直接创建一个 PR，且仅新增或修改一个完整的 `players/<player-slug>.json` 文件。请在 `identity` 和 `evidence` 中提供规范 UUID/资料来源、带引用的证明、经审核的 PES 数值、预期的更新基线、生命周期和精确的基础版本，然后在请求审核前运行 `python run.py players validate`。不要包含生成草稿的顶层 `source` 或 `draft` 元数据。该 PR 中不要混入其他代码或文档更改。

应用更新始终需要显式命令，并且要求使用 `data/base_manifest.json` 中的精确版本；版本不匹配时，会在解密目标存档前失败。

### 版本生命周期

官方基础存档发生变化时，请同时更新 `base/EDIT00000000` 和 `data/base_manifest.json`。将历史 Player Update 保留在 `players/` 中；不要仅仅因为版本变化就删除它们。如果一个 active Player Update 的 `applies_to` 列表不包含新版本，则该更新处于非活动状态：验证会报告 `needs_review`，应用时会跳过它。审核后，仅当 Player Update 仍然适用时才添加新版本；如果官方基础存档已包含其更改，则将其标记为 `upstreamed`；如果不再适用，则标记为 `retired`。

常用 `run` 选项：

| 选项 | 用途 |
|---|---|
| `--deep` | 获取本地已建立索引的所有 FotMob 俱乐部 |
| `--club "Chelsea,Arsenal"` | 将运行范围限定为选定俱乐部 |
| `--window auto` | 重放截至今天所有可用的有日期转会 |
| `--window summer` | 使用最近的 6 月 1 日至 9 月 30 日时间范围 |
| `--window winter` | 使用所选年份的 1 月至 2 月时间范围 |
| `--since YYYY-MM-DD` | 手动设置日期下限 |
| `--dry-run` | 规划更改，但不写入存档 |
| `--from-base` | 从 `base/EDIT00000000` 开始 |
| `--fotmob-only` | 不使用补充转会来源运行 |

不使用 `--from-base` 时，常规运行会从上次已验证的输出继续。这样可以避免后续定时运行再次读取累积历史时，已应用的转会消失。

## 转会数据来源

FotMob 提供主要的转会历史和阵容元数据。Wikipedia 的赛季列表、已启用的 SortitoutSI 转会投稿，以及经验证且带日期的 Transfermarkt 记录，会补充或确认转会路径。Pes Retro Stats 资料为 Player Update 草稿提供基于来源但未经批准的提案。

来自不同来源的记录会在不丢弃日期、ID、引用或证明链接的情况下进行协调。无日期、未来生效、互相冲突或存在歧义的事件，无法单独用于更新存档。

球员匹配从来源球队名单开始，并使用目标球队名单作为保证幂等性的后备方案。仅在相应信息可用时，才会考虑位置、国籍和年龄。

## 开发

运行测试套件：

```bash
pytest -v
```

该测试套件涵盖存档解析与验证、转会协调、阵容规划、租借历史、球员匹配、球队人数限制、报告、备份和进程锁。

## 许可证

FL Daily Edit 基于 [MIT License](LICENSE) 提供。
