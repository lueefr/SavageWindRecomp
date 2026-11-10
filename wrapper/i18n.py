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
