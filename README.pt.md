[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

# FL Daily Edit

[![Versão do Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Licença: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

O FL Daily Edit atualiza os elencos do SP Football Life 2026 e do eFootball PES 2021 aplicando transferências do mundo real a um arquivo de edição `EDIT00000000`.

> **Limitação atual — a criação de novos jogadores está temporariamente desativada enquanto
> corrigimos e verificamos um problema de salvamento/aparência.**
>
> As transferências de jogadores que já estão no arquivo de edição e as atualizações revisadas
> de jogadores existentes continuam suportadas. Jogadores ausentes são ignorados, e um elenco
> de destino cheio é ignorado por padrão em vez de dispensar um jogador existente.

## Compatibilidade

A base incluída é voltada para o **SP Football Life 2026**. Requisitos:

- Football Life 26 Update 2.2
- SmokePatch's National Squads Update

Não é compatível com UML, versões anteriores do FL26 ou instalações sem a atualização de seleções nacionais. Inicie uma nova carreira na Master League ou Rumo ao Estrelato após instalar o arquivo de edição.

A [base incluída](base/EDIT00000000) é o arquivo [Gondowan's Mid-Summer EDIT](https://www.reddit.com/r/SPFootballLife/comments/1v7z782/release_gondowans_midsummer_edit_file_more_than/), de 27 de julho de 2026. Ele inclui mais de 500 transferências, além de avaliações gerais, posições, números de camisa, retornos de empréstimo, treinadores, escalações e alterações de acesso/rebaixamento atualizados. Ele não cria novos jogadores nem adiciona clubes promovidos de terceiras divisões.

## Instalador para Windows

O instalador para Windows é a opção recomendada para iniciantes. A interface do instalador está disponível somente em inglês no momento. Os downloads validados atualmente são **exclusivos para Football Life 2026 Update 2.2 + SmokePatch's National Squads Update**. A detecção do eFootball PES 2021 vanilla está presente, mas a instalação permanece desativada até que uma base validada correspondente seja publicada.

1. Baixe e extraia o [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip).
2. Feche o jogo.
3. Escolha **Fast** ou **Deep**. São opções separadas de cobertura de atualização, e cada uma exibe o horário de geração.
4. Confirme a pasta do Football Life 2026 detectada ou use **Browse** se necessário.
5. Selecione **Download and install**. O instalador verifica o download, faz backup do arquivo de edição atual e o substitui atomicamente.

**Atualizar um arquivo de edição existente pela interface gráfica:** O instalador
também pode atualizar um `EDIT00000000` de layout comum selecionado pelo usuário,
em vez de instalar uma versão pré-compilada. Escolha **Update my local save**,
selecione um local detectado ou use **Browse**, escolha **Fast** ou **Deep** e,
após a revisão, selecione **Apply update**. O assistente valida o arquivo antes
de modificá-lo, cria um backup no mesmo local e exibe o progresso, o resultado
ou os diagnósticos. A qualificação local não depende do rótulo SPFL/PES/UML, e
este caminho não baixa uma versão remota pré-compilada. Quando esses catálogos
externos opcionais do SPFL não estiverem disponíveis, o comparador local usa os
nomes de jogadores e equipes integrados no arquivo selecionado, permitindo que o
caminho de atualização local empacotado funcione sem eles.

> [!WARNING]
> O executável do instalador não é assinado, portanto o Windows SmartScreen pode exibir um aviso ao executá-lo. Antes de continuar, verifique o `FLDailyEditInstaller.zip` baixado com o `FLDailyEditInstaller.zip.sha256` publicado na [versão mais recente](https://github.com/gvoze32/fldailyedit/releases/tag/latest).
> Se o Windows bloquear o instalador por meio do Smart App Control, abra **Settings → Privacy & security → Windows Security → App & browser control → Smart App Control settings** e mude para **Off**. Alternativamente, clique com o botão direito no arquivo baixado, abra **Properties** e marque **Unblock**, se disponível.

Para instalação manual sem o instalador, baixe o [ZIP da versão Fast pública](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip) ou o [ZIP da versão Deep pública](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip). Extraia `EDIT00000000`, faça backup do seu arquivo atual e copie o arquivo extraído para:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

Para uma execução sob demanda ou para usar uma lista personalizada de clubes, faça um fork do repositório e use **Run workflow** na aba Actions.

## O que é atualizado

- Transferências, rescisões, empréstimos e retornos de empréstimo
- Números de camisa disponíveis a partir dos dados de elenco do FotMob
- Identidades de jogadores verificadas em relação ao elenco atual do FL26
- Escalações e planos de jogo afetados por alterações no elenco
- Relatórios de transferências e logs de auditoria em JSON Lines
- Arquivos de edição pré-compilados diariamente via GitHub Actions
- Criações de jogadores e correções de atributos revisadas por meio de comandos explícitos de Player Update

O atualizador não substitui um número de camisa que já esteja sendo usado por outro membro do elenco. Ele também verifica o clube atual do jogador antes de aplicar uma transferência.

## Roteiro / Concluído por enquanto

Todos os itens atuais do roteiro foram concluídos. Estamos aguardando a próxima ideia útil.

## Segurança e limitações

- As execuções locais criam backups rotativos e utilizam criptografia atômica e verificada.
- Os arquivos de edição são validados antes e depois das alterações de elenco.
- Um bloqueio de processo impede que duas execuções gravem na mesma saída ao mesmo tempo.
- Instantâneos incompletos do FotMob abortam a execução em vez de gerar um arquivo parcial.
- Correspondências ambíguas de jogadores e divergências de clube de origem são ignoradas.
- Elencos de destino cheios são ignorados por padrão; o atualizador de transferências nunca dispensa um jogador existente automaticamente.
- `--allow-overflow-release` é uma opção separada e explícita apenas para transferências. Ela requer metadados completos de posição e OVR e pode dispensar um candidato seguro para abrir vaga. Se esses metadados estiverem incompletos, a execução falha de forma segura.
- Wikipedia, Sortitoutsi e Transfermarkt são fontes suplementares. A indisponibilidade de uma dessas fontes não invalida um instantâneo completo do FotMob.

**Atualizações de transferências vs. Player Updates**

Estes são fluxos de trabalho distintos:

- `run` processa transferências para jogadores que já existem no arquivo de edição. Se o clube de destino estiver cheio, a transferência é ignorada; outras transferências seguras na mesma execução ainda podem ser aplicadas.
- `players apply` aplica alterações de atributos revisadas. Especificações `update` de jogadores existentes são suportadas.
- Especificações `create` de novos jogadores continuam carregáveis e revisáveis, mas estão temporariamente desativadas após testes de segurança de aparência/salvamento. A aplicação de uma delas retorna `create_temporarily_unavailable` e mantém o arquivo de edição byte a byte inalterado.

## Execução local

A configuração local é compatível com macOS, Linux e Windows por meio do WSL. É necessário o Python 3.10 ou superior.

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
| `run` | Aplica apenas transferências verificadas |
| `players validate` | Valida todas as Player Updates em relação à base original |
| `players apply` | Aplica explicitamente as Player Updates revisadas a um arquivo de edição |
| `log` | Exibe as transferências aplicadas recentemente |
| `inspect` | Inspeciona equipes, contagem de jogadores e deslocamentos do arquivo |
| `validate` | Verifica os registros nos elencos e os mapeamentos dos planos de jogo |
| `repair` | Repara uma base legada usando arquivos de referência |

`run` lida apenas com transferências: ele nunca carrega nem aplica Player Updates. Para combinar ambos os fluxos de trabalho, execute primeiro o comando de transferências em um arquivo de edição de saída e, em seguida, execute `players apply --in-place` nesse mesmo arquivo.

## Atualizações de jogadores

Cada Player Update revisada é um arquivo JSON completo no schema versão 2 por jogador em `players/`. Ela registra uma operação (`operation`: `create` ou `update`), um ciclo de vida (`active`, `upstreamed` ou `retired`), revisões de base exatas em `applies_to`, identidade estável do jogador e procedência do perfil/UUID no Pes Retro Stats, evidências citadas e dados do PES revisados. As atualizações de criação contêm uma proposta de registro completo de jogador e dados de elenco de destino. As atualizações de jogadores existentes contêm apenas valores suportados que diferem da base verificada; cada alteração registra valores literais `from` e `to`.
Registros `create` continuam suportados pelo schema para revisão e futura reativação. Atualmente, apenas registros `update` de jogadores existentes alteram os arquivos de edição; a aplicação de um `create` concluído retorna `create_temporarily_unavailable` sem alterar o arquivo.
Os grupos de atualização suportados são habilidades, proficiência de posição, estilo de jogo, habilidades de jogador, estilos COM, nacionalidade, configurações físicas/básicas e posição registrada.
- Os valores de revisão de OVR gerados são cálculos determinísticos baseados na fórmula publicada do PES 2021. Eles são uma ajuda de paridade, não uma garantia independente da execução do jogo; os valores de habilidade propostos ainda exigem revisão.
- Rascunhos de jogadores gerados com o identificador de modelo OVR anterior devem ser gerados novamente antes da validação; nenhuma migração de v1 para v2 é implícita.

### Fluxo simples por issue

1. Abra o [formulário de issue para atualização de jogadores](.github/ISSUE_TEMPLATE/player-update.yml). Insira o `Player name` exatamente como ele aparece em um `Pes Retro Stats profile` canônico, forneça as URLs de comprovação e aguarde até que um mantenedor aplique o rótulo exato `generate-player-draft`.
2. O fluxo de trabalho configurado do gerador obtém esse perfil e abre um PR de rascunho contendo uma proposta `players/<player-slug>.json` no schema versão 2. Ele extrai o instantâneo de origem, identidade, configurações físicas, dados de posição, habilidades, estilo de jogo, habilidades de jogador e estilos COM do perfil.
3. Para uma criação, apenas os valores locais do jogo indisponíveis na fonte continuam listados em `draft.missing`: IDs do PES e nomes de exibição para a identidade e o jogador, ID e nome da equipe, ID de nacionalidade, cor da pele e cor da íris. Um colaborador ou mantenedor deve fornecê-los. Para uma atualização, o gerador localiza o jogador na base verificada e gera apenas as diferenças reais `from`/`to`. Uma posição de origem não suportada pelo PES 2021, como `RWB`, é omitida em vez de remapeada, inclusive na alteração da posição registrada.
4. Um colaborador e um mantenedor revisam cada valor gerado como uma proposta não aprovada. O CI aceita uma Player Update apenas quando o PR adiciona ou modifica exatamente um caminho JSON canônico de jogador e o validador semântico compartilhado é aprovado com sucesso.
5. A mesclagem do PR continua sendo o estado de aprovação humana. Não há um sinalizador `approved` separado no arquivo JSON.

Espera-se que toda proposta gerada falhe na validação de arquivo completo. Para converter suas evidências geradas para o schema v2 completo, remova os campos exclusivos de rascunho `evidence.current_team`, `evidence.issue_number` e `evidence.issue_url`; mantenha a `evidence.profile_url` canônica, as `evidence.proof_urls` revisadas e a `evidence.effective_date` exata; e adicione um campo `evidence.reason` revisado e não vazio. Persista o UUID de perfil canônico como `identity.pes_retro_stats_id` e apenas os valores de jogabilidade revisados em `pes`. Para uma criação, preencha também todos os campos locais do jogo indicados em `draft.missing`. Os IDs de PES de jogadores criados devem ser únicos e ter valor mínimo de `0x100000` (1.048.576); o alocador de propostas permanece nessa faixa reservada.
Em seguida, remova os objetos de nível superior `source` e `draft`, que são metadados de rascunho gerados exclusivos para revisão, antes da validação completa do arquivo.

### Fluxo direto por PR de arquivo único

Um colaborador avançado pode pular o rascunho gerado por issue e abrir diretamente um PR que adicione ou modifique exatamente um arquivo completo `players/<player-slug>.json`. Forneça a procedência canônica do perfil/UUID em `identity` e `evidence`, comprovações citadas, valores do PES revisados, bases de referência esperadas para a atualização, o ciclo de vida e a revisão de base exata; depois execute `python run.py players validate` antes de solicitar uma revisão. Não inclua os metadados de nível superior `source` ou `draft` do rascunho gerado. Mantenha outras alterações de código ou documentação fora desse PR.

A aplicação é sempre feita por um comando explícito e requer a revisão exata de `data/base_manifest.json`; uma incompatibilidade de revisão falha antes de descriptografar o arquivo de edição de destino.

### Ciclo de vida das revisões

Quando a base oficial mudar, atualize `base/EDIT00000000` e `data/base_manifest.json` juntos. Mantenha as Player Updates históricas em `players/`; não as exclua apenas porque a revisão mudou. Uma Player Update ativa cuja lista `applies_to` não contém a nova revisão fica inativa: a validação reporta `needs_review` e a aplicação a ignora. Após a revisão, adicione a nova revisão apenas quando a Player Update ainda for aplicável, marque-a como `upstreamed` quando a base oficial incluir a alteração ou como `retired` quando não for mais aplicável.

Opções comuns de `run`:

| Opção | Finalidade |
|---|---|
| `--deep` | Busca todos os clubes do FotMob indexados localmente |
| `--club "Chelsea,Arsenal"` | Limita a execução aos clubes selecionados |
| `--window auto` | Reproduz todas as transferências com data disponíveis até hoje |
| `--window summer` | Usa a janela mais recente de 1º de junho a 30 de setembro |
| `--window winter` | Usa a janela de janeiro a fevereiro do ano selecionado |
| `--since YYYY-MM-DD` | Define manualmente o limite inferior de data |
| `--dry-run` | Planeja as alterações sem gravar um arquivo de edição |
| `--from-base` | Inicia a partir de `base/EDIT00000000` |
| `--fotmob-only` | Executa sem fontes complementares de transferência |

Sem `--from-base`, uma execução normal continua a partir da última saída verificada. Isso evita que as transferências desapareçam quando uma execução agendada posterior ler novamente o histórico acumulado.

## Fontes de transferências

O FotMob fornece o histórico principal de transferências e os metadados dos elencos. As listas de temporada da Wikipedia, as contribuições de transferências ativadas do SortitoutSI e os registros verificados com data do Transfermarkt complementam ou confirmam as rotas de transferência. Os perfis do Pes Retro Stats fornecem propostas derivadas da fonte e não aprovadas para rascunhos de Player Update.

Os registros de diferentes fontes são reconciliados sem descartar suas datas, IDs, citações ou links de comprovação. Eventos sem data, com vigência futura, conflitantes ou ambíguos não podem atualizar o arquivo de edição por conta própria.

A correspondência de jogadores começa pelo elenco do clube de origem e usa o elenco do clube de destino como contingência idempotente. Posição, nacionalidade e idade só são consideradas quando essas informações estão disponíveis.

## Desenvolvimento

Execute a suíte de testes com:

```bash
pytest -v
```

A suíte abrange análise e validação de arquivos de edição, reconciliação de transferências, planejamento de elencos, histórico de empréstimos, correspondência de jogadores, limites de elenco, relatórios, backups e bloqueio de processos.

## Licença

O FL Daily Edit é disponibilizado sob a [Licença MIT](LICENSE).
