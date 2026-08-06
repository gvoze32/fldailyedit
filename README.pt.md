[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](README.md) [![Bahasa Indonesia](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Bahasa_Indonesia-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

O FL Daily Edit atualiza os elencos do SP Football Life 2026 e do eFootball PES 2021 aplicando transferências do mundo real a um arquivo de edição `EDIT00000000`.

## Compatibilidade

A base incluída é destinada ao **SP Football Life 2026**. Ela requer:

- Football Life 26 Update 2.2
- SmokePatch's National Squads Update

Ela não é compatível com UML, versões anteriores do FL26 ou instalações sem a atualização das seleções nacionais. Inicie uma nova carreira na Liga Master ou Rumo ao Estrelato depois de instalar o arquivo de edição.

A [base incluída](base/EDIT00000000) é o [Gondowan's Mid-Summer EDIT](https://www.reddit.com/r/SPFootballLife/comments/1v7z782/release_gondowans_midsummer_edit_file_more_than/), datado de 27 de julho de 2026. Ela inclui mais de 500 transferências, atributos, posições e números de camisa atualizados, retornos de empréstimo, técnicos, escalações e alterações de promoção ou rebaixamento. Ela não cria jogadores nem adiciona clubes promovidos da terceira divisão.

## Instalador para Windows

O instalador para Windows é a opção recomendada para iniciantes. A interface do instalador está disponível somente em inglês no momento. Os downloads validados atuais destinam-se **somente ao Football Life 2026 Update 2.2 + SmokePatch's National Squads Update**. A detecção do eFootball PES 2021 vanilla está presente, mas a instalação permanece desativada até que uma base validada correspondente seja publicada.

1. Baixe [FLDailyEditInstaller.exe](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.exe).
2. Feche o jogo.
3. Escolha **Fast** ou **Deep**. São opções separadas de cobertura da atualização, e cada uma exibe seu horário de geração.
4. Confirme a pasta detectada do Football Life 2026 ou use **Browse**, se necessário.
5. Selecione **Download and install**. O instalador verifica o download, faz backup do arquivo de edição atual e o substitui de forma atômica.

**Atualizar um save existente pela GUI:** o instalador também pode atualizar um
`EDIT00000000` de layout comum escolhido pelo usuário, em vez de instalar uma
versão pré-compilada. Selecione **Update my local save**, escolha um local
detectado ou use **Browse**, escolha **Fast** ou **Deep** e, depois de revisar,
selecione **Apply update**. O assistente valida o save antes da alteração, cria
um backup no próprio local e mostra o progresso, o resultado ou os diagnósticos.
A elegibilidade local não depende do rótulo SPFL/PES/UML, e esse caminho não
baixa uma versão pré-compilada remota.


> [!WARNING]
> O executável inicial não é assinado, portanto o Windows SmartScreen pode exibir um aviso. Antes de continuar, compare o arquivo baixado com o `FLDailyEditInstaller.exe.sha256` publicado na [versão mais recente](https://github.com/gvoze32/fldailyedit/releases/tag/latest).

Para instalar manualmente sem o instalador, baixe o [ZIP público Fast](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip) ou o [ZIP público Deep](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip). Extraia `EDIT00000000`, faça backup do arquivo de edição atual e copie o arquivo extraído para:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Para executar sob demanda ou usar uma lista personalizada de clubes, faça um fork do repositório e use **Run workflow** na aba Actions.

## O que é atualizado

- Transferências, rescisões, empréstimos e retornos de empréstimo
- Números de camisa disponíveis com base nos dados de elenco do FotMob
- Identidades dos jogadores verificadas em relação ao elenco atual do FL26
- Escalações e planos de jogo afetados por mudanças de elenco
- Relatórios de transferências e logs de auditoria em JSON Lines
- Arquivos de edição pré-compilados diariamente pelo GitHub Actions
- Criações de jogadores e correções de atributos revisadas por meio de comandos explícitos de Atualização de Jogador

O atualizador não substitui um número de camisa que já esteja sendo usado por outro jogador do elenco. Ele também verifica o clube atual do jogador antes de aplicar uma transferência.

## Roteiro / Concluído por enquanto

Todos os itens atuais do roteiro foram concluídos. Estamos aguardando a próxima ideia útil.

## Segurança e limitações

- As execuções locais criam backups rotativos e realizam a criptografia de forma atômica, verificando o resultado.
- Os arquivos de edição são validados antes e depois das alterações de elenco.
- Um bloqueio de processo impede que duas execuções gravem no mesmo arquivo de saída ao mesmo tempo.
- Snapshots incompletos do FotMob interrompem a execução em vez de produzir um arquivo parcial.
- Correspondências ambíguas de jogadores, divergências no clube de origem e elencos de destino lotados são ignorados.
- Wikipedia, Sortitoutsi e Transfermarkt são fontes complementares. Uma indisponibilidade em uma dessas fontes não invalida um snapshot completo do FotMob.
- `--allow-overflow-release` falha de forma segura porque o catálogo incluído não contém dados completos de posição e OVR para todos os jogadores.

## Executar localmente

A configuração local é compatível com macOS, Linux e Windows por meio do WSL. É necessário usar Python 3.10 ou mais recente.

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

| Comando | Finalidade |
|---|---|
| `run` | Aplicar somente transferências verificadas |
| `players validate` | Validar todas as Atualizações de Jogadores em relação à base original |
| `players apply` | Aplicar explicitamente Atualizações de Jogadores revisadas a um arquivo de edição |
| `log` | Mostrar as transferências aplicadas recentemente |
| `inspect` | Inspecionar equipes, contagens de jogadores e offsets do arquivo de edição |
| `validate` | Verificar registros de elenco e mapeamentos de planos de jogo |
| `repair` | Reparar uma base antiga usando arquivos de edição de referência |

`run` processa somente transferências: ele nunca carrega nem aplica Atualizações de Jogadores. Para combinar os dois fluxos, primeiro execute o comando de transferência em um arquivo de saída e depois execute `players apply --in-place` nesse mesmo arquivo.

## Atualizações de jogadores

Cada Atualização de Jogador revisada consiste em um arquivo JSON completo de versão 2 do esquema por jogador em `players/`. Ele registra uma `operation` (`create` ou `update`), um ciclo de vida (`active`, `upstreamed` ou `retired`), revisões-base exatas em `applies_to`, identidade estável do jogador e proveniência do UUID/perfil do Pes Retro Stats, evidências citadas e dados PES revisados. Atualizações de criação contêm uma proposta de registro completo do jogador e dados do elenco de destino. Atualizações de jogadores existentes contêm somente os valores compatíveis que diferem da base verificada; cada mudança registra valores literais `from` e `to`.

Os grupos de atualização compatíveis são atributos, proficiência de posição, estilo de jogo, habilidades do jogador, estilos COM, nacionalidade, configurações físicas/básicas e posição registrada.

### Caminho simples via issue

1. Abra o [formulário de issue para atualização de jogador](.github/ISSUE_TEMPLATE/player-update.yml). Insira o `Player name` exatamente como exibido em um perfil canônico do `Pes Retro Stats`, forneça as URLs de comprovação e aguarde um mantenedor aplicar o rótulo exato `generate-player-draft`.
2. O workflow de geração configurado busca esse perfil e abre um PR de rascunho contendo uma proposta `players/<player-slug>.json` de versão 2 do esquema. Ele deriva do perfil o snapshot de origem, a identidade, as configurações físicas, os dados de posição, os atributos, o estilo de jogo, as habilidades do jogador e os estilos COM.
3. Para uma criação, somente os valores locais do jogo indisponíveis na fonte permanecem listados em `draft.missing`: os IDs e nomes para impressão na camisa do PES para a identidade e o jogador, o ID e o nome da equipe, o ID da nacionalidade, a cor da pele e a cor da íris. Um colaborador ou mantenedor deve fornecê-los. Para uma atualização, o gerador encontra o jogador na base verificada e emite somente as diferenças reais de `from`/`to`. Uma posição da fonte não compatível com o PES 2021, como `RWB`, é omitida em vez de ser remapeada, inclusive na alteração da posição registrada.
4. Um colaborador e um mantenedor revisam cada valor gerado como uma proposta não aprovada. O CI aceita uma Atualização de Jogador somente quando o PR adiciona ou modifica exatamente um caminho JSON canônico de jogador e o validador semântico compartilhado é bem-sucedido.
5. O merge do PR continua sendo o estado de aprovação humana. Não há uma propriedade `approved` separada no arquivo JSON.

Espera-se que toda proposta gerada falhe na validação de arquivo completo. Para converter suas evidências geradas ao esquema v2 completo, remova os campos exclusivos de rascunho `evidence.current_team`, `evidence.issue_number` e `evidence.issue_url`; mantenha o `evidence.profile_url` canônico, os `evidence.proof_urls` revisados e `evidence.effective_date`; e adicione um `evidence.reason` revisado e não vazio. Salve o UUID canônico do perfil como `identity.pes_retro_stats_id` e somente os valores de jogabilidade revisados em `pes`. Para uma criação, complete também todos os campos locais do jogo nomeados em `draft.missing`. Em seguida, remova os objetos de nível superior `source` e `draft`, que são metadados exclusivos da revisão do rascunho gerado, antes da validação completa.

### Caminho direto de PR com um único arquivo

Um colaborador avançado pode ignorar o rascunho gerado pela issue e abrir diretamente um PR que adicione ou modifique exatamente um arquivo completo `players/<player-slug>.json`. Forneça a proveniência canônica de UUID/perfil nos campos `identity` e `evidence`, comprovações citadas, valores PES revisados, valores-base esperados para a atualização, ciclo de vida e revisão-base exata; depois execute `python run.py players validate` antes de solicitar revisão. Não inclua os metadados de nível superior `source` ou `draft` do rascunho gerado. Não inclua outras alterações de código ou documentação nesse PR.

A aplicação é sempre feita por um comando explícito e exige a revisão exata de `data/base_manifest.json`; uma divergência de revisão causa falha antes da descriptografia do arquivo de destino.

### Ciclo de vida da revisão

Quando a base oficial mudar, atualize `base/EDIT00000000` e `data/base_manifest.json` juntos. Mantenha as Atualizações de Jogadores históricas em `players/`; não as exclua apenas porque a revisão mudou. Uma Atualização de Jogador ativa cuja lista `applies_to` não contenha a nova revisão fica inativa: a validação informa `needs_review` e a aplicação a ignora. Após a revisão, adicione a nova revisão somente quando a Atualização de Jogador ainda for aplicável, marque-a como `upstreamed` quando a base oficial incluir sua alteração ou como `retired` quando ela deixar de ser aplicável.

Opções comuns de `run`:

| Opção | Finalidade |
|---|---|
| `--deep` | Buscar todos os clubes indexados localmente no FotMob |
| `--club "Chelsea,Arsenal"` | Limitar a execução aos clubes selecionados |
| `--window auto` | Reproduzir todas as transferências datadas disponíveis até hoje |
| `--window summer` | Usar o período mais recente de 1º de junho a 30 de setembro |
| `--window winter` | Usar o período de janeiro a fevereiro do ano selecionado |
| `--since YYYY-MM-DD` | Definir manualmente a data inicial |
| `--dry-run` | Planejar alterações sem gravar um arquivo de edição |
| `--from-base` | Iniciar a partir de `base/EDIT00000000` |
| `--fotmob-only` | Executar sem fontes de transferências complementares |

Sem `--from-base`, uma execução normal continua a partir do último arquivo de saída verificado. Isso impede que transferências desapareçam quando uma execução agendada posterior lê novamente o histórico acumulado.

## Fontes de transferências

O FotMob fornece o histórico principal de transferências e os metadados de elenco. Listas sazonais da Wikipedia, envios de transferências habilitados no SortitoutSI e registros datados verificados do Transfermarkt complementam ou confirmam as rotas de transferência. Os perfis do Pes Retro Stats fornecem propostas derivadas da fonte e não aprovadas para rascunhos de Atualizações de Jogadores.

Registros de fontes diferentes são reconciliados sem descartar suas datas, IDs, citações ou links de comprovação. Eventos sem data, com vigência futura, conflitantes ou ambíguos não podem atualizar o arquivo de edição por conta própria.

A correspondência de jogadores começa no elenco de origem e usa o elenco de destino como fallback idempotente. Posição, nacionalidade e idade são consideradas somente quando essas informações estão disponíveis.

## Desenvolvimento

Execute a suíte de testes com:

```bash
pytest -v
```

A suíte cobre parsing e validação do arquivo de edição, reconciliação de transferências, planejamento de elencos, histórico de empréstimos, correspondência de jogadores, limites de elenco, relatórios, backups e bloqueio de processo.

## Licença

O FL Daily Edit está disponível sob a [Licença MIT](LICENSE).
