"""Installer UI strings. English is always the first-run default."""
import json
import os
from pathlib import Path
import tempfile

LANGUAGES = (
    ("en", "English"),
    ("pt-BR", "Português (Brasil)"),
    ("ru", "Русский"),
    ("zh-Hans", "简体中文"),
)

# Untranslated entries use their English source string, never Portuguese fallback text.
CATALOG = {
    "Savage Wind — Installer": ("Savage Wind — Instalador", "Savage Wind — Установка", "Savage Wind — 安装程序"),
    "Portable installer · Savage Wind Recomp": ("Instalador portátil · Savage Wind Recomp", "Портативная установка · Savage Wind Recomp", "便携安装 · Savage Wind Recomp"),
    "Game files": ("Arquivos do jogo", "Файлы игры", "游戏文件"),
    "Choose extracted files or import your own NSP/XCI packages.": ("Escolha arquivos extraídos ou importe seus pacotes NSP/XCI.", "Выберите распакованные файлы или свои NSP/XCI.", "选择已解包的文件或导入自己的 NSP/XCI。"),
    "Extracted folder": ("Pasta extraída", "Папка с файлами", "已解包文件夹"),
    "NSP / XCI": ("NSP / XCI", "NSP / XCI", "NSP / XCI"),
    "Path containing exefs/ and romfs/:": ("Pasta com exefs/ e romfs/:", "Путь к exefs/ и romfs/:", "包含 exefs/ 和 romfs/ 的路径："),
    "Browse...": ("Procurar...", "Обзор...", "浏览..."),
    "Base NSP/XCI, update NSP and prod.keys from your own copy.": ("Jogo base NSP/XCI, atualização NSP e prod.keys da sua cópia.", "Базовый NSP/XCI, NSP обновления и prod.keys вашей копии.", "您自己副本的基础 NSP/XCI、更新 NSP 和 prod.keys。"),
    "Base game": ("Jogo base", "Основная игра", "基础游戏"),
    "Update": ("Atualização", "Обновление", "更新"),
    "Keys": ("Chaves", "Ключи", "密钥"),
    "Saves, settings and caches are stored in game/ next to this launcher.": ("Saves, configurações e caches ficam em game/ ao lado do launcher.", "Сохранения, настройки и кэш находятся в game/ рядом с лаунчером.", "存档、设置和缓存保存在启动器旁的 game/ 文件夹中。"),
    "Install and play": ("Instalar e jogar", "Установить и играть", "安装并启动"),
    "Build AppImage": ("Compilar AppImage", "Собрать AppImage", "构建 AppImage"),
    "Choose AppImage source": ("Escolher origem do AppImage", "Выбрать источник AppImage", "选择 AppImage 来源"),
    "No Savage Wind AppImage is installed. Which version would you like to use?": ("Nenhum AppImage do Savage Wind foi encontrado. Qual versão deseja usar?", "AppImage Savage Wind не найден. Какую версию использовать?", "未找到 Savage Wind AppImage。您想使用哪个版本？"),
    "The official release is recommended. To use PRs, select them on the Source builds page.": ("A versão oficial é a recomendada. Para usar PRs, selecione-os na aba Compilações locais.", "Рекомендуется официальная версия. Выберите PR на вкладке локальных сборок.", "建议使用官方版本。若使用 PR，请在本地构建页面中选择。"),
    "Upstream test branch": ("Branch test do upstream", "Ветка test upstream", "上游 test 分支"),
    "Build with pull requests": ("Compilar com pull requests", "Собрать с pull request", "使用拉取请求构建"),
    "Don't ask anymore": ("Não perguntar novamente", "Больше не спрашивать", "不再询问"),
    "Yes": ("Sim", "Да", "是"),
    "No": ("Não", "Нет", "否"),
    "The AppImage is ready.\n\nRecompile the game's native library (swgame.so) using this AppImage now?\n\nThis can take a long time and will replace the existing native library. Saves, settings and extracted game files will be preserved.\n\nChoose No to finish without recompiling the game.": ("O AppImage está pronto.\n\nDeseja recompilar agora a biblioteca nativa do jogo (swgame.so) usando este AppImage?\n\nIsso pode demorar e substituirá a biblioteca nativa existente. Os saves, as configurações e os arquivos extraídos do jogo serão preservados.\n\nEscolha Não para concluir sem recompilar o jogo.", "AppImage готов.\n\nПерекомпилировать библиотеку игры (swgame.so) с помощью этого AppImage?\n\nЭто может занять много времени и заменит существующую библиотеку. Сохранения, настройки и распакованные файлы останутся нетронутыми.\n\nВыберите «Нет», чтобы завершить без перекомпиляции.", "AppImage 已准备就绪。\n\n是否现在使用此 AppImage 重新编译游戏原生库（swgame.so）？\n\n这可能耗费较长时间，并会替换现有原生库。存档、设置和已解包的游戏文件将保留。\n\n选择“否”可直接完成，不重新编译游戏。"),
    "Build AppImage only": ("Compilar apenas o AppImage", "Собрать только AppImage", "仅构建 AppImage"),
    "Recompile game code": ("Recompilar o código do jogo", "Перекомпилировать игровой код", "重新编译游戏代码"),
    "Play existing game": ("Iniciar jogo existente", "Запустить установленную игру", "启动已有游戏"),
    "Fetch sources": ("Baixar código-fonte", "Загрузка исходников", "获取源代码"),
    "Apply pull requests": ("Aplicar pull requests", "Применить запросы PR", "应用拉取请求"),
    "Prepare compiler": ("Preparar compilador", "Подготовить компилятор", "准备编译器"),
    "Compile runtime": ("Compilar executável", "Сборка программы", "编译程序"),
    "Package AppImage": ("Empacotar AppImage", "Упаковать AppImage", "打包 AppImage"),
    "Build dependencies and package AppImage": ("Compilar dependências e empacotar AppImage", "Сборка зависимостей и упаковка AppImage", "构建依赖项并打包 AppImage"),
    "Publish AppImage": ("Validar e salvar AppImage", "Проверить и сохранить AppImage", "验证并保存 AppImage"),
    "Select test or pull requests to compile an AppImage.": ("Selecione a branch test ou algum PR para compilar um AppImage.", "Выберите ветку test или PR для сборки AppImage.", "请选择 test 分支或拉取请求来构建 AppImage。"),
    "Please import game files before recompiling.": ("Importe os arquivos do jogo antes de recompilar.", "Импортируйте игровые файлы перед перекомпиляцией.", "重新编译前请导入游戏文件。"),
    "Delete extracted game files, compiled code and AppImages? Saves and settings are preserved.": ("Excluir arquivos extraídos, código compilado e AppImages? Saves e configurações serão preservados.", "Удалить распакованные файлы, код и AppImage? Сохранения и настройки останутся.", "删除已解包文件、编译代码和 AppImage？存档和设置将保留。"),
    "AppImage build completed.": ("Compilação do AppImage concluída.", "Сборка AppImage завершена.", "AppImage 构建完成。"),
    "Game code recompilation completed.": ("Recompilação do jogo concluída.", "Перекомпиляция игрового кода завершена.", "游戏代码重新编译完成。"),
    "Use existing game files": ("Usar arquivos existentes", "Использовать имеющиеся файлы", "使用现有游戏文件"),
    "Installed game detected. Input sources are locked until deletion.": ("Jogo detectado. As entradas ficam bloqueadas até a exclusão.", "Игра найдена. Источники заблокированы до удаления.", "检测到已安装游戏，删除前输入源已锁定。"),
    "No compiled game detected.": ("Nenhum jogo recompilado detectado.", "Скомпилированная игра не найдена.", "未检测到已编译游戏。"),
    "Native game library detected.": ("Biblioteca nativa detectada.", "Найдена библиотека игры.", "检测到原生游戏库。"),
    "AppImage detected.": ("AppImage detectado.", "AppImage найден.", "检测到 AppImage。"),
    "AppImage missing — setup required.": ("AppImage ausente — configuração necessária.", "AppImage отсутствует — нужна настройка.", "缺少 AppImage — 需要安装。"),
    "Runtime source": ("Origem do executável", "Источник исполняемого файла", "运行时来源"),
    "Official upstream AppImage": ("AppImage oficial", "Официальный AppImage", "官方 AppImage"),
    "Build from selected pull requests": ("Compilar a partir dos PRs selecionados", "Собрать из выбранных PR", "从选定的拉取请求构建"),
    "Source builds": ("Compilações locais", "Локальные сборки", "本地构建"),
    "Build upstream test branch (no PRs)": ("Compilar a branch test do upstream (sem PRs)", "Собрать ветку test upstream (без PR)", "构建上游 test 分支（不含 PR）"),
    "Compiles the latest upstream test commit locally; no pull requests are applied.": ("Compila localmente o commit mais recente da branch test, sem aplicar PRs.", "Локальная сборка последнего коммита ветки test без PR.", "本地构建上游 test 分支的最新提交，不应用任何 PR。"),
    "Leave both unchecked to use the official upstream AppImage.": ("Deixe as duas opções desmarcadas para usar o AppImage oficial.", "Снимите обе галочки, чтобы использовать официальный AppImage.", "取消勾选这两个选项以使用官方 AppImage。"),
    "Pull requests": ("Pull requests", "Пул-реквесты", "拉取请求"),
    "Open pull requests": ("Pull requests abertos", "Открытые PR", "开放的拉取请求"),
    "Refresh pull requests": ("Atualizar pull requests", "Обновить PR", "刷新拉取请求"),
    "Select PRs to merge with upstream test. Missing build tools can be installed with your permission.": ("Selecione PRs para combinar com test. As ferramentas ausentes podem ser instaladas com sua autorização.", "Выберите PR для объединения с test. Недостающие инструменты можно установить с вашего согласия.", "选择要合并到 test 的 PR。经您同意后可安装缺少的构建工具。"),
    "Loading pull requests...": ("Carregando pull requests...", "Загрузка PR...", "正在加载拉取请求..."),
    "No open pull requests targeting test.": ("Nenhum PR aberto para test.", "Нет открытых PR для test.", "没有针对 test 的开放 PR。"),
    "Select at least one pull request.": ("Selecione pelo menos um pull request.", "Выберите хотя бы один PR.", "请至少选择一个拉取请求。"),
    "Delete installation": ("Excluir instalação", "Удалить установку", "删除安装"),
    "This permanently removes root/, game/, all saves, and downloaded/built AppImages. The portable launcher remains.": ("Isso apaga permanentemente root/, game/, todos os saves e os AppImages. O launcher permanece.", "Будут навсегда удалены root/, game/, сохранения и AppImage. Лаунчер останется.", "这将永久删除 root/、game/、所有存档以及 AppImage。保留启动器。"),
    "Type DELETE to confirm:": ("Digite DELETE para confirmar:", "Введите DELETE для подтверждения:", "输入 DELETE 以确认："),
    "Installation deleted.": ("Instalação excluída.", "Установка удалена.", "安装已删除。"),
    "Installation files": ("Arquivos da instalação", "Файлы установки", "安装文件"),
    "Progress": ("Progresso", "Прогресс", "进度"),
    "Storage": ("Armazenamento", "Хранилище", "存储"),
    "Game files": ("Arquivos do jogo", "Файлы игры", "游戏文件"),
    "Updates": ("Atualizações", "Обновления", "更新"),
    "Recompilation": ("Recompilação", "Перекомпиляция", "重新编译"),
    "Launch game": ("Iniciar jogo", "Запуск игры", "启动游戏"),
    "Show log": ("Mostrar log", "Показать журнал", "显示日志"),
    "Hide log": ("Ocultar log", "Скрыть журнал", "隐藏日志"),
    "Back": ("Voltar", "Назад", "返回"),
    "Cancel": ("Cancelar", "Отмена", "取消"),
    "Pause": ("Pausar", "Пауза", "暂停"),
    "Resume": ("Continuar", "Продолжить", "继续"),
    "Paused": ("Pausado", "Приостановлено", "已暂停"),
    "Resuming...": ("Continuando...", "Возобновление...", "正在继续..."),
    "Preparing...": ("Preparando...", "Подготовка...", "准备中..."),
    "Starting...": ("Iniciando...", "Запуск...", "启动中..."),
    "Cancelling...": ("Cancelando...", "Отмена...", "正在取消..."),
    "Operation cancelled.": ("Operação cancelada.", "Операция отменена.", "操作已取消。"),
    "Could not finish.": ("Não foi possível concluir.", "Не удалось завершить.", "无法完成。"),
    "Select a folder": ("Selecione uma pasta", "Выберите папку", "选择文件夹"),
    "Please select a folder containing exefs/ and romfs/.": ("Selecione uma pasta com exefs/ e romfs/.", "Выберите папку с exefs/ и romfs/.", "请选择包含 exefs/ 和 romfs/ 的文件夹。"),
    "Invalid game files": ("Arquivos inválidos", "Неверные файлы", "游戏文件无效"),
    "Please select valid base, update and prod.keys files.": ("Selecione arquivos válidos de jogo base, atualização e prod.keys.", "Выберите корректные основную игру, обновление и prod.keys.", "请选择有效的基础游戏、更新和 prod.keys 文件。"),
    "Checking for updates": ("Verificando atualizações", "Проверка обновлений", "检查更新"),
    "Language": ("Idioma", "Язык", "语言"),
}

def tr(source, language="en"):
    if language == "en":
        return source
    positions = {"pt-BR": 0, "ru": 1, "zh-Hans": 2}
    idx = positions.get(language)
    return CATALOG.get(source, (source, source, source))[idx] if idx is not None else source


def get_language(root):
    try:
        path = Path(root) / ".sw-wrapper/gui.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        language = data.get("language", "en")
        if language in dict(LANGUAGES):
            return language
    except (OSError, ValueError, AttributeError):
        pass
    return "en"


def set_language(root, language):
    if language not in dict(LANGUAGES):
        raise ValueError("Unsupported UI language")
    folder = Path(root) / ".sw-wrapper"
    if folder.is_symlink():
        raise OSError("Installer state is a symbolic link")
    folder.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".gui-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump({"language": language}, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, folder / "gui.json")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# Only human-readable progress needs localization. Raw developer logs are kept intact.
PROGRESS = (
    ("Preparando o armazenamento portátil", "Preparing portable storage", "Подготовка переносного хранилища", "准备便携存储"),
    ("Procurando atualizações oficiais", "Checking official updates", "Проверка обновлений", "检查官方更新"),
    ("Preparando o código nativo do jogo", "Preparing native game code", "Подготовка нативного кода игры", "准备本地游戏代码"),
    ("Compilação recuperada", "Recovered compilation", "Восстановлена сборка", "已恢复编译"),
    ("Arquivos do jogo preparados", "Game files prepared", "Файлы игры подготовлены", "游戏文件已准备"),
    ("Copiando arquivos do jogo", "Copying game files", "Копирование файлов игры", "正在复制游戏文件"),
    ("Analisando os arquivos do jogo", "Analyzing game files", "Анализ файлов игры", "正在分析游戏文件"),
    ("Analisando módulos do jogo", "Analyzing game modules", "Анализ модулей игры", "正在分析游戏模块"),
    ("Identificando os módulos NSO e preparando a geração de C", "Identifying NSO modules and preparing C generation", "Поиск NSO-модулей и подготовка генерации C", "正在识别 NSO 模块并准备生成 C"),
    ("Gerando C e compilando", "Generating C and compiling", "Генерация C и компиляция", "正在生成 C 并编译"),
    ("Gerando código C", "Generating C code", "Генерация кода C", "正在生成 C 代码"),
    ("Vinculando a biblioteca nativa", "Linking native library", "Компоновка нативной библиотеки", "正在链接本地库"),
    ("Vinculando ", "Linking ", "Компоновка ", "正在链接 "),
    ("ETA estimado:", "Estimated ETA:", "Расчётное время:", "预计剩余时间："),
    ("ETA: calculando", "ETA: calculating", "Время: вычисляется", "剩余时间：计算中"),
    ("até a vinculação", "until linking", "до компоновки", "直到链接"),
    ("Gerador:", "Generator:", "Генератор:", "生成器："),
    ("Compilador:", "Compiler:", "Компилятор:", "编译器："),
    ("funções", "functions", "функций", "函数"),
    ("objetos compilados", "compiled objects", "скомпилированных объектов", "已编译对象"),
    ("objetos concluídos", "completed objects", "готовых объектов", "已完成对象"),
    ("Tempo decorrido:", "Elapsed:", "Прошло:", "已用时间："),
    ("Decorrido:", "Elapsed:", "Прошло:", "已用时间："),
    ("ETA preliminar:", "Preliminary ETA:", "Предварительная оценка:", "初步预计剩余："),
    ("(pode variar)", "(may vary)", "(может измениться)", "（可能变化）"),
    ("(execuções anteriores)", "(past builds)", "(по предыдущим сборкам)", "（参考以往构建）"),
    ("Concluído em", "Completed in", "Завершено за", "完成耗时"),

    ("partes geradas", "generated chunks", "готовых частей", "已生成分块"),
    ("partes", "chunks", "частей", "分块"),
    ("O compilador oficial", "The official compiler", "Официальный компилятор", "官方编译器"),
    ("Preparando ", "Preparing ", "Подготовка ", "正在准备 "),
    ("Baixando ", "Downloading ", "Загрузка ", "正在下载 "),
    ("Verificando ", "Checking ", "Проверка ", "正在检查 "),
    ("Migrando ", "Migrating ", "Перенос ", "正在迁移 "),
    ("Recuperando ", "Recovering ", "Восстановление ", "正在恢复 "),
    ("Instalando ", "Installing ", "Установка ", "正在安装 "),
    ("Iniciando ", "Starting ", "Запуск ", "正在启动 "),
    ("Organizando ", "Organizing ", "Организация ", "正在整理 "),
    ("Recompilação", "Recompilation", "Перекомпиляция", "重新编译"),
    ("Código nativo validado", "Native game code validated", "Нативный код проверен", "本地代码已验证"),
)

def progress_text(message, language):
    if language == "pt-BR":
        return message
    idx = {"en": 1, "ru": 2, "zh-Hans": 3}.get(language, 1)
    for forms in PROGRESS:
        message = message.replace(forms[0], forms[idx])
    return message
