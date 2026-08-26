[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](../../README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Versão do Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Licença: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../../LICENSE)

Atualize os arquivos `EDIT00000000` do SP Football Life 2026 e do eFootball
PES 2021 com transferências reais verificadas e atualizações de jogadores revisadas.

> **Beta:** As versões e a compatibilidade com os arquivos de edição ainda estão
> em testes.
>
> **A criação de novos jogadores está desativada por enquanto.** Transferências e
> atualizações revisadas de jogadores existentes continuam disponíveis. Jogadores
> ausentes ou ambíguos são ignorados. Elencos de destino cheios liberam por padrão
> um reserva seguro conforme a função; use `--no-allow-overflow-release` para
> manter o elenco sem alterações.

## Compatibilidade

A [base incluída](../../base/EDIT00000000) requer:

- **SP Football Life 2026 Update 2.2**
- **SmokePatch's National Squads Update**

Não é compatível com UML, versões antigas do FL26 ou instalações sem a atualização
das seleções nacionais. Inicie uma nova carreira na Master League ou Rumo ao
Estrelato depois de instalá-la.

## Instalador para Windows

O instalador é a opção mais simples:

1. Baixe e extraia o [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip).
2. Feche o jogo e escolha **Fast** ou **Deep**.
3. Confirme a pasta do Football Life e selecione **Download and install**.

O instalador verifica a versão, faz backup do arquivo atual e o substitui de
forma atômica. Para atualizar um arquivo existente, escolha **Update my local
save**, selecione o arquivo e clique em **Apply update**.

O instalador não é assinado. Verifique `FLDailyEditInstaller.zip` com o arquivo
`FLDailyEditInstaller.zip.sha256` publicado na [versão mais recente](https://github.com/gvoze32/fldailyedit/releases/tag/latest)
antes de executá-lo; o Windows SmartScreen pode exibir um aviso.

Para instalar manualmente, baixe o [ZIP Fast](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip)
ou o [ZIP Deep](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip).
Faça backup, extraia `EDIT00000000` e copie-o para:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Para uma execução sob demanda ou uma lista personalizada de clubes, faça um fork
do repositório e use **Run workflow** na aba Actions.

## O que é atualizado

- Transferências, dispensas, empréstimos e retornos de empréstimo
- Números de camisa, escalações e planos de jogo afetados por mudanças no elenco
- Relatórios de transferências e logs de auditoria
- Arquivos de edição pré-compilados diariamente pelo GitHub Actions

O atualizador verifica o clube atual do jogador e nunca substitui um número de
camisa já usado por outro jogador do elenco.

## Execução local

Compatível com macOS, Linux e Windows por meio do WSL. É necessário Python 3.10
ou superior.

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

## Comandos comuns

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

`run` aplica apenas transferências. `players apply` é um fluxo separado. Para
combinar os dois, execute primeiro as transferências e depois aplique as Player
Updates ao mesmo arquivo. Use `python run.py <command> --help` para ferramentas
de auditoria, comparação, registro e reparo.

## Atualizações de jogadores

As atualizações revisadas ficam em um arquivo JSON por jogador dentro de
`players/`. Registros `update` de jogadores existentes podem ser aplicados.
Registros `create` de novos jogadores servem apenas para revisão e são rejeitados
por `players apply` com `create_temporarily_unavailable`.

Para propor uma atualização:

1. Abra o [formulário de issue para atualização de jogadores](../../.github/ISSUE_TEMPLATE/player-update.yml).
2. Informe o nome exatamente como aparece no perfil do Pes Retro Stats e inclua URLs de comprovação.
3. Revise o rascunho gerado, execute `python run.py players validate` e envie um único arquivo JSON de jogador.

## Segurança

- Os arquivos são validados antes e depois das alterações.
- Execuções locais criam backups rotativos e usam criptografia atômica verificada.
- Um bloqueio impede gravações simultâneas na mesma saída.
- Dados incompletos interrompem a execução; correspondências ambíguas são ignoradas.
- FotMob é a fonte principal; as demais apenas complementam ou confirmam os dados.

## Desenvolvimento

```bash
pytest -v
```

## Licença

O FL Daily Edit está disponível sob a [Licença MIT](../../LICENSE).
