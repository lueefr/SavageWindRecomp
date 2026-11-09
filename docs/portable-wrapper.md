# Savage Wind: instalador portátil nativo para Linux

Este instalador usa a release oficial do [Savage Wind](https://github.com/Revivedx/SavageWindRecomp) sem modificá-la.
O AppImage desta branch é um *wrapper* leve com interface Qt no Plasma e GTK 3/Adwaita no GNOME.
Não inclui jogos, arquivos Nintendo Switch ou chaves.

## Instalação

Baixe o AppImage na [release de prévia portátil](https://github.com/lueefr/SavageWindRecomp/releases/tag/portable-preview),
coloque-o na pasta em que pretende manter os dados e execute-o.

Na primeira execução, escolha uma pasta contendo exefs/ e romfs/ do seu próprio dump atualizado
ou a aba NSP/XCI para selecionar jogo base, atualização NSP e prod.keys.
A barra de progresso informa a etapa real de importação, download e compilação.
O log fica recolhido por padrão. Cancelar encerra o processo de importação ou compilação,
inclusive seus subprocessos, e descarta os temporários de uma instalação incompleta.

A instalação utiliza a pasta **real** do próprio wrapper AppImage (não a pasta temporária montada pelo AppImage).
A estrutura é aproximadamente:

    Jogos/SavageWind/
      SavageWind-Portable-x86_64.AppImage
      SavageWind-official.AppImage
      extracted/exefs/, extracted/romfs/, extracted/swgame.so
      save/, cache/, savagewind.ini
      .sw-wrapper/ (estados, registros, temporários, rollback)
      .xdg/ (outros dados XDG usados por bibliotecas)

O wrapper inclui um pacote atualizado de certificados confiáveis (certifi/Mozilla),
para que consultas e downloads por HTTPS funcionem mesmo quando o Python empacotado
não consegue descobrir os certificados da distribuição Linux. A validação TLS e
a conferência de SHA-256 permanecem obrigatórias.

O wrapper define SAVAGEWIND_DATA_ROOT, HOME, XDG_CONFIG_HOME, XDG_DATA_HOME,
XDG_STATE_HOME, XDG_CACHE_HOME, MESA_SHADER_CACHE_DIR e TMPDIR **antes**
de executar o upstream e seus auxiliares. O novo fluxo não escreve em
~/.local/share/savagewind. Se encontrar dados de uma instalação anterior nesse
caminho, oferece migração com consentimento; nunca os apaga ou sobrescreve silenciosamente.

Para pedir a janela do instalador mesmo com o jogo já instalado:

    ./SavageWind-Portable-x86_64.AppImage --config

## Atualizações e execução

A cada abertura, consulta as releases publicadas no repositório oficial,
incluindo **pré-releases**, e compara o SHA-256 do AppImage.
Se o jogo já estiver instalado, essa consulta acontece em uma thread de trabalho
**com a janela inteiramente oculta**; sem atualização nem preparação obrigatória,
o jogo abre diretamente, sem exibir a interface do wrapper. Se encontrar uma
atualização, mostra apenas a confirmação. A janela de progresso só aparece
depois que o usuário aceitar a atualização, ou para recuperação, migração,
compilação necessária ou um erro. No primeiro uso, o formulário de instalação
é exibido normalmente. A consulta de rede ainda é concluída antes de iniciar
o jogo, para que atualizações possam ser oferecidas antes de abrir a sessão. Quando encontra
atualização, pergunta se deseja instalá-la. O download tem progresso,
pode ser cancelado e só substitui a cópia gerenciada após validação de tamanho
e hash. A busca por AppImages já baixados se limita a pastas comuns e à pasta
do wrapper, sem varreduras custosas do disco. Apenas binários oficiais
com hash correspondente são reaproveitados.

Se a versão não mudou e swgame.so já está preparado para ela, pula a recompilação.
Quando ela é necessária, acompanha os registros temporários do gerador oficial e os objetos nativos compilados,
mostrando módulos, funções convertidas, partes geradas, unidades compiladas e a vinculação final de swgame.so.
A barra apresenta uma **porcentagem aproximada** derivada dessas medições. O ETA dinâmico só aparece após
acumular amostras suficientes; ele estima o tempo até a vinculação, e não promete um horário exato de término.
Os detalhes são registrados em .sw-wrapper/build.log (linhas [progresso]) e no log expansível da interface.
A leitura é limitada a uma janela do final de cada log e a uma amostragem da pasta temporária, evitando
varreduras recursivas ou consumo excessivo de CPU.
O compilador recebe prioridade de agendamento menor para preservar a
responsividade do desktop, mas ainda pode consumir CPU e RAM em máquinas lentas.
O upstream é iniciado diretamente com --play, sem mostrar o menu dele.

Em caso de falha após iniciar a troca de versão, a atualização guarda o AppImage
anterior e o código nativo compilado em .sw-wrapper/rollback/ e recupera os
arquivos na execução seguinte. Saves e configurações não participam dessa troca.

O importador requer espaço livre significativo para os dados temporários de BKTR
e RomFS. Os pacotes NSP/XCI são processados localmente com o hactool fixado
e validadores provenientes de MacNativeGames (MIT); o arquivo prod.keys é lido
na importação e não é incorporado ao AppImage.

## Limitações e testes

A instalação portátil não faz varredura recursiva em todos os discos e não executa
AppImages antigos não verificados. Pode ser necessário selecionar manualmente
seus próprios arquivos no primeiro uso.

A preparação de swgame.so usa um mecanismo de montagem/teste já presente no upstream,
mas ainda não é uma API estável garantida por seus mantenedores. Testes unitários
não substituem validação com um dump próprio do jogo nem comprovam desempenho em
todos os desktops.

Os testes sintéticos estão em wrapper/tests/ e tools/linux/test_import_game.py.
O workflow .github/workflows/wrapper.yml também gera e verifica o AppImage Linux.
