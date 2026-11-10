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
    "Pull requests": ("Pull requests", "Пул-реквесты", "拉取请求"),
    "Open pull requests": ("Pull requests abertos", "Открытые PR", "开放的拉取请求"),
    "Refresh pull requests": ("Atualizar pull requests", "Обновить PR", "刷新拉取请求"),
    "Choose one or more PRs; each contains third-party code and may conflict. Requires git and Podman.": ("Escolha um ou mais PRs; eles podem ter código de terceiros e entrar em conflito. Requer git e Podman.", "Выберите один или несколько PR; они могут конфликтовать и содержат сторонний код. Требуются git и Podman.", "选择一个或多个 PR；可能包含第三方代码并产生冲突。需要 git 和 Podman。"),
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
PROGRESS_EN = (
    ("Preparando o armazenamento portátil", "Preparing portable storage"),
    ("Procurando atualizações oficiais", "Checking official updates"),
    ("Preparando o código nativo do jogo", "Preparing native game code"),
    ("Compilação recuperada", "Recovered completed compilation"),
    ("Arquivos do jogo preparados", "Game files prepared"),
    ("Copiando arquivos do jogo", "Copying game files"),
    ("Analisando os arquivos do jogo", "Checking game files"),
    ("Preparando ", "Preparing "),
    ("Baixando ", "Downloading "),
    ("Verificando ", "Checking "),
    ("Migrando ", "Migrating "),
    ("Recuperando ", "Recovering "),
    ("Instalando ", "Installing "),
    ("Iniciando ", "Starting "),
    ("Organizando ", "Organizing "),
    ("Recompilação", "Recompilation"),
    ("Código nativo validado", "Native game code validated"),
)

def progress_text(message, language):
    if language != "en":
        return message
    for original, english in PROGRESS_EN:
        if message.startswith(original):
            return english + message[len(original):]
    return message
